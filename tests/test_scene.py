"""Scene lifecycle and transcript tests."""

import discord
import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from bot.commands.scene import (
    ActiveSceneError,
    ScenePersonaError,
    SceneService,
    parse_npc_names,
    scene_embed,
)
from bot.db import repo
from bot.db.session import create_session_factory


async def _persona(session: AsyncSession, guild_id: int, name: str):
    return await repo.create_persona(
        session,
        guild_id,
        name=name,
        public_desc="A traveler",
        personality="Watchful",
        goals="Learn the road",
        secrets=None,
        knowledge_tags=[],
        created_by=1,
    )


def test_parse_npc_names_normalizes_and_deduplicates() -> None:
    assert parse_npc_names(" Mira, Corvin, mira, , CORVIN ") == ["Mira", "Corvin"]


async def test_start_populates_stage_and_refuses_a_second_active_scene(
    db_session: AsyncSession,
) -> None:
    guild = await repo.get_or_create_guild(db_session, 1001, "Campaign")
    await _persona(db_session, guild.id, "Mira")
    await _persona(db_session, guild.id, "Corvin")
    await db_session.commit()
    service = SceneService(create_session_factory(db_session.bind))  # type: ignore[arg-type]

    view = await service.start(1001, "Campaign", 55, "The Old Road", ["Mira", "Corvin"])

    assert view.scene.status == "active"
    assert [persona.name for persona in view.personas] == ["Mira", "Corvin"]
    embed = scene_embed(view).to_dict()
    assert embed["title"] == "The Old Road"
    assert {field["name"]: field["value"] for field in embed["fields"]} == {
        "Location": "Unknown",
        "On stage": "Mira, Corvin",
    }
    with pytest.raises(ActiveSceneError):
        await service.start(1001, "Campaign", 55, "Another", [])


async def test_add_and_end_scene(db_session: AsyncSession) -> None:
    guild = await repo.get_or_create_guild(db_session, 1001, "Campaign")
    await _persona(db_session, guild.id, "Mira")
    await db_session.commit()
    service = SceneService(create_session_factory(db_session.bind))  # type: ignore[arg-type]
    await service.start(1001, "Campaign", 55, None, [])

    view = await service.add(1001, "Campaign", 55, "Mira")
    assert [persona.name for persona in view.personas] == ["Mira"]
    ended = await service.end(1001, "Campaign", 55)
    assert ended is not None
    assert ended.scene.status == "ended"
    assert await service.end(1001, "Campaign", 55) is None


async def test_unknown_npc_does_not_leave_a_scene_behind(
    db_session: AsyncSession,
) -> None:
    service = SceneService(create_session_factory(db_session.bind))  # type: ignore[arg-type]
    with pytest.raises(ScenePersonaError):
        await service.start(1001, "Campaign", 55, None, ["Missing"])

    view = await service.start(1001, "Campaign", 55, None, [])
    assert view.scene.status == "active"


async def test_silent_player_messages_are_logged_only_in_active_scene(
    db_session: AsyncSession,
) -> None:
    service = SceneService(create_session_factory(db_session.bind))  # type: ignore[arg-type]
    await service.start(1001, "Campaign", 55, "Road", [])

    assert await service.log_channel_message(
        1001, 55, 9001, "Player", "We wait and listen."
    )
    assert not await service.log_channel_message(1001, 99, 9002, "Player", "Elsewhere.")

    guild = await repo.get_or_create_guild(db_session, 1001, "Campaign")
    scene = await repo.get_active_scene(db_session, guild.id, 55)
    assert scene is not None
    messages = await repo.list_scene_messages(db_session, guild.id, scene.id)
    assert [(row.author_type, row.author_name, row.content) for row in messages] == [
        ("player", "Player", "We wait and listen.")
    ]


def test_say_derives_secret_visibility_from_the_channel_not_the_caller() -> None:
    """A DM running `/say` in a channel a player can read must not pull secrets.

    The reply is posted publicly by a webhook regardless of who invoked the
    command, so deriving `is_dm_context` from the caller's role puts
    `persona.secrets` into a prompt whose output every player reads. Hard
    rule #1: the barrier is the absence of the string, not an instruction.

    Denying `@everyone` is not sufficient either — a channel can deny the
    default role and still grant one player role or member `view_channel`.
    What matters is who can actually read it.
    """
    from unittest.mock import MagicMock

    from bot.commands.say import _is_dm_only_channel

    DM_ROLE, PLAYER_ROLE = 700, 800
    OWNER = 1

    def member(user_id: int, *role_ids: int) -> MagicMock:
        value = MagicMock()
        value.id = user_id
        value.roles = [MagicMock(id=role_id) for role_id in role_ids]
        return value

    def channel_with(*members: MagicMock) -> MagicMock:
        value = MagicMock()
        value.guild = MagicMock()
        value.guild.owner_id = OWNER
        value.channel = MagicMock()
        value.channel.members = list(members)
        return value

    dm = member(2, DM_ROLE)
    player = member(3, PLAYER_ROLE)
    owner = member(OWNER)

    # Only DMs can read it: the one place secrets belong.
    assert _is_dm_only_channel(channel_with(dm, owner), DM_ROLE) is True

    # The tavern. Everyone reads it, whoever typed the command.
    assert _is_dm_only_channel(channel_with(dm, player), DM_ROLE) is False

    # The case that a default-role check alone would get wrong: @everyone is
    # denied, but one player was granted access by an overwrite.
    assert _is_dm_only_channel(channel_with(dm, owner, player), DM_ROLE) is False

    # No DM role configured — nobody but the owner qualifies.
    assert _is_dm_only_channel(channel_with(dm), None) is False

    # Undeterminable cases fail closed: player-facing.
    no_guild = MagicMock()
    no_guild.guild = None
    assert _is_dm_only_channel(no_guild, DM_ROLE) is False

    no_member_list = MagicMock()
    no_member_list.guild = MagicMock()
    no_member_list.channel = object()
    assert _is_dm_only_channel(no_member_list, DM_ROLE) is False

    assert _is_dm_only_channel(channel_with(), DM_ROLE) is False


def test_speech_state_survives_across_say_invocations() -> None:
    """The webhook cache and per-channel lock are process state, not request
    state. A fresh `Speech` per command silently discards both: chunks from two
    NPCs interleave, and Discord's ten-webhooks-per-channel cap gets hit."""
    import inspect

    from bot.commands.say import Say
    from bot.engine.speech import Speech

    # The session is per call; the caches are not.
    assert "session" not in inspect.signature(Speech.__init__).parameters
    assert "session" in inspect.signature(Speech.speak).parameters

    # And the cog holds exactly one, rather than building one per invocation.
    source = inspect.getsource(Say.say.callback)
    assert "Speech(" not in source, "constructs a new Speech per invocation"
    assert "self._speech.speak(" in source


async def test_dm_narration_is_not_logged_as_a_player_line(
    db_session: AsyncSession,
) -> None:
    """The transcript feeds back into prompts, so this distinction is not
    cosmetic: a DM's narration logged as a player line reads to the NPC as
    another character speaking, and counts towards ambient player activity."""
    service = SceneService(create_session_factory(db_session.bind))  # type: ignore[arg-type]
    await service.start(1001, "Campaign", 55, "Road", [])

    async with create_session_factory(db_session.bind)() as session:  # type: ignore[arg-type]
        guild = await repo.get_or_create_guild(session, 1001, "Campaign")
        guild.dm_role_id = 4242
        await session.commit()

    assert await service.log_channel_message(
        1001, 55, 9101, "A Player", "I draw my sword.", frozenset({999})
    )
    assert await service.log_channel_message(
        1001, 55, 9102, "The DM", "The door groans open.", frozenset({4242})
    )
    assert await service.log_channel_message(
        1001, 55, 9103, "The Owner", "Roll for it.", frozenset(), True
    )

    async with create_session_factory(db_session.bind)() as session:  # type: ignore[arg-type]
        guild = await repo.get_or_create_guild(session, 1001, "Campaign")
        scene = await repo.get_active_scene(session, guild.id, 55)
        assert scene is not None
        messages = await repo.list_scene_messages(session, guild.id, scene.id)

    by_author = {message.author_name: message.author_type for message in messages}
    assert by_author == {"A Player": "player", "The DM": "dm", "The Owner": "dm"}


def test_say_refuses_channels_that_cannot_host_a_webhook() -> None:
    """Refused before generating, not after.

    An NPC speaks through a channel webhook. Two things stop that, and both
    surface only once the speech layer tries to post — after a 16-36s local
    generation has run and been charged to the reply budget, with nothing
    posted for it: a channel type with no webhook API, and a missing
    **Manage Webhooks** permission, which is the one people miss on invite.
    """
    from unittest.mock import AsyncMock, MagicMock

    from bot.commands.say import _can_host_a_webhook

    me = MagicMock()

    def channel(*, has_api: bool, may_manage: bool) -> MagicMock:
        value = MagicMock(spec=[] if not has_api else None)
        if has_api:
            value.webhooks = AsyncMock()
            value.create_webhook = AsyncMock()
            value.permissions_for.return_value.manage_webhooks = may_manage
        return value

    assert _can_host_a_webhook(channel(has_api=True, may_manage=True), me) is True

    # Has the API, lacks the permission: webhooks() would raise Forbidden.
    assert _can_host_a_webhook(channel(has_api=True, may_manage=False), me) is False

    # A thread has no webhook API of its own at all.
    assert _can_host_a_webhook(MagicMock(spec=discord.Thread), me) is False
    assert _can_host_a_webhook(object(), me) is False

    # Unknown bot member: refuse rather than guess.
    assert _can_host_a_webhook(channel(has_api=True, may_manage=True), None) is False
