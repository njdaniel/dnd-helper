"""Webhook speech posting and prose chunking."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import repo
from bot.engine import speech
from bot.engine.speech import DISCORD_MESSAGE_LIMIT, Speech, chunk_prose


def test_chunk_prose_keeps_real_prose_boundaries() -> None:
    first = (
        "Rain silvered the old road while the travelers watched the tree line. "
        "No birds called from the pines."
    )
    second = 'Mira lowered her lantern. "We should turn back," she said. Nobody moved.'
    text = f"{first}\n\n{second}"

    chunks = chunk_prose(text, target=len(first) + 1)

    assert chunks == [first, second]
    assert "".join(chunks).replace(" ", "") == text.replace("\n", "").replace(" ", "")
    assert all(chunk[-1] in '.!?"' for chunk in chunks)


def test_chunk_prose_splits_long_paragraph_only_between_sentences() -> None:
    sentences = [
        f"Sentence {number} carries several deliberate words." for number in range(80)
    ]
    text = " ".join(sentences)

    chunks = chunk_prose(text)

    assert len(chunks) > 1
    assert all(len(chunk) <= DISCORD_MESSAGE_LIMIT for chunk in chunks)
    assert all(chunk.endswith(".") for chunk in chunks)
    assert " ".join(chunks) == text


def test_chunk_prose_rejects_an_indivisible_oversized_sentence() -> None:
    text = f"{'word ' * 450}ends."

    with pytest.raises(ValueError, match="sentence exceeds"):
        chunk_prose(text)


class FakeWebhook:
    def __init__(self, *, message_ids: list[int]) -> None:
        self.name = "Campaign Companion"
        self.token = "token"
        self.message_ids = iter(message_ids)
        self.calls: list[dict[str, object]] = []
        self.active_sends = 0
        self.max_active_sends = 0

    async def send(self, **kwargs: object) -> SimpleNamespace:
        self.active_sends += 1
        self.max_active_sends = max(self.max_active_sends, self.active_sends)
        await asyncio.sleep(0)
        self.calls.append(kwargs)
        self.active_sends -= 1
        return SimpleNamespace(id=next(self.message_ids))


class FakeChannel:
    def __init__(self, channel_id: int, webhook: FakeWebhook) -> None:
        self.id = channel_id
        self.webhook = webhook
        self.webhooks_calls = 0
        self.create_calls = 0

    async def webhooks(self) -> list[Any]:
        self.webhooks_calls += 1
        return [self.webhook]

    async def create_webhook(self, *, name: str) -> Any:
        assert name == "Campaign Companion"
        self.create_calls += 1
        return self.webhook


async def _campaign(db_session: AsyncSession, channel_id: int = 55) -> tuple[Any, Any]:
    guild = await repo.get_or_create_guild(db_session, 1001, "Campaign")
    persona = await repo.create_persona(
        db_session,
        guild.id,
        name="Mira",
        avatar_url="https://example.com/mira.png",
        public_desc="A cartographer",
        personality="Careful",
        goals="Map the ruins",
        knowledge_tags=[],
        created_by=10,
    )
    scene = await repo.create_scene(db_session, guild.id, channel_id=channel_id)
    return persona, scene


async def test_speak_caches_webhook_posts_and_persists_real_ids(
    db_session: AsyncSession,
) -> None:
    persona, scene = await _campaign(db_session)
    webhook = FakeWebhook(message_ids=[7001, 7002])
    channel = FakeChannel(55, webhook)
    speech = Speech()

    first = await speech.speak(db_session, channel, persona, "The road remembers.")
    second = await speech.speak(db_session, channel, persona, "So do I.")
    stored = await repo.list_scene_messages(db_session, persona.guild_id, scene.id)

    assert (first.id, second.id) == (7001, 7002)
    assert channel.webhooks_calls == 1
    assert [call["wait"] for call in webhook.calls] == [True, True]
    assert webhook.calls[0]["username"] == "Mira"
    assert webhook.calls[0]["avatar_url"] == "https://example.com/mira.png"
    assert [
        (row.discord_message_id, row.author_type, row.persona_id, row.content)
        for row in stored
    ] == [
        (7001, "npc", persona.id, "The road remembers."),
        (7002, "npc", persona.id, "So do I."),
    ]


async def test_speak_recreates_deleted_webhook(db_session: AsyncSession) -> None:
    persona, _ = await _campaign(db_session)
    deleted = FakeWebhook(message_ids=[])
    replacement = FakeWebhook(message_ids=[8001])
    channel = FakeChannel(55, replacement)
    speech = Speech()
    speech._webhooks[channel.id] = deleted  # arrange a previously cached webhook
    deleted.send = AsyncMock(
        side_effect=discord.NotFound(AsyncMock(), "Unknown Webhook")
    )

    message = await speech.speak(db_session, channel, persona, "I have returned.")

    assert message.id == 8001
    assert channel.create_calls == 1


async def test_speak_serializes_posts_per_channel(db_session: AsyncSession) -> None:
    persona, _ = await _campaign(db_session)
    webhook = FakeWebhook(message_ids=[9001, 9002])
    channel = FakeChannel(55, webhook)
    speech = Speech()

    await asyncio.gather(
        speech.speak(db_session, channel, persona, "First."),
        speech.speak(db_session, channel, persona, "Second."),
    )

    assert webhook.max_active_sends == 1


def test_paragraph_break_survives_a_split_paragraph() -> None:
    """A long paragraph is replaced by its sentences, so the sentence that
    *starts* it is no longer a known paragraph. Inferring the separator by
    membership joined it to the previous paragraph with a space, silently
    merging two paragraphs of an NPC's dialogue into one."""
    short = "She sets the glass down."
    long_paragraph = " ".join(f"Sentence number {n} of the reply." for n in range(60))
    text = f"{short}\n\n{long_paragraph}"

    chunks = speech.chunk_prose(text, target=400)

    joined = "\n\n".join(chunks)
    assert short in joined
    # The short paragraph must not have been glued to the long one's opening.
    assert f"{short} Sentence number 0" not in joined
    assert f"{short}\n\nSentence number 0" in joined


async def test_speak_refuses_a_scene_that_was_replaced(
    db_session: AsyncSession,
) -> None:
    """A 16-36s generation is long enough for /scene end then /scene start.

    Without checking, the old scene's line is posted into the new scene and
    persisted under it — a character answering a question nobody asked.
    """
    guild = await repo.get_or_create_guild(db_session, 1001, "Campaign")
    persona = await repo.create_persona(
        db_session,
        guild.id,
        name="Kestrel Vane",
        public_desc="Innkeeper",
        personality="Wary",
        goals="Keep the peace",
        secrets=None,
        knowledge_tags=[],
        created_by=1,
    )
    first = await repo.create_scene(db_session, guild.id, channel_id=55, title="Before")
    await db_session.flush()
    stale_scene_id = first.id
    first.status = "ended"
    await repo.create_scene(db_session, guild.id, channel_id=55, title="After")
    await db_session.flush()

    channel = MagicMock()
    channel.id = 55
    channel.webhooks = AsyncMock(return_value=[])
    channel.create_webhook = AsyncMock()

    with pytest.raises(ValueError, match="scene changed"):
        await Speech().speak(
            db_session,
            channel,
            persona,
            "The glass stops.",
            expected_scene_id=stale_scene_id,
        )

    channel.create_webhook.assert_not_awaited()


def test_each_posted_chunk_is_committed_before_the_next_send() -> None:
    """Discord has already displayed the earlier chunks.

    If a later `send` fails and the surrounding transaction rolls back, the
    transcript loses dialogue players actually read, and there is no
    `scene_message` row left to resolve a reply-trigger against. So the commit
    has to sit inside the per-chunk loop, after the row is written and before
    the next send is attempted.

    Asserted on structure rather than behaviour: the in-memory SQLite fixture
    shares one connection across sessions, so it cannot distinguish committed
    rows from merely flushed ones.
    """
    import ast
    import inspect
    import textwrap

    from bot.engine.speech import Speech

    tree = ast.parse(textwrap.dedent(inspect.getsource(Speech.speak)))
    loops = [node for node in ast.walk(tree) if isinstance(node, ast.For)]
    assert len(loops) == 1, "expected exactly one per-chunk loop"

    body = "\n".join(ast.unparse(statement) for statement in loops[0].body)
    assert "create_scene_message" in body, "the row is written outside the loop"
    assert "session.commit()" in body, "chunks are not committed as they are sent"
    assert body.index("create_scene_message") < body.index("session.commit()")
