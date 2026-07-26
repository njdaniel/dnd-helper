"""Post NPC dialogue through cached Discord webhooks."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Sequence
from typing import Protocol

import discord
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import repo
from bot.db.models import Persona

WEBHOOK_NAME = "Campaign Companion"
TARGET_CHUNK_SIZE = 1900
DISCORD_MESSAGE_LIMIT = 2000

_PARAGRAPH_BREAK = re.compile(r"\n\s*\n")
_SENTENCE_END = re.compile(r"(?<=[.!?])\s+|(?<=[.!?][\"'”’)\]])\s+")


class WebhookChannel(Protocol):
    """The subset of a Discord text channel used by the speech layer."""

    id: int

    async def webhooks(self) -> Sequence[discord.Webhook]: ...

    async def create_webhook(self, *, name: str) -> discord.Webhook: ...


def chunk_prose(text: str, *, target: int = TARGET_CHUNK_SIZE) -> list[str]:
    """Split prose at paragraph or sentence boundaries for Discord.

    A single sentence longer than Discord's hard limit is rejected rather than
    silently splitting it mid-sentence.
    """
    if not 0 < target <= DISCORD_MESSAGE_LIMIT:
        raise ValueError("target must be between 1 and 2000 characters")

    paragraphs = [part.strip() for part in _PARAGRAPH_BREAK.split(text) if part.strip()]
    if not paragraphs:
        raise ValueError("text must contain something to speak")

    # (text, starts_a_new_paragraph). The flag is carried rather than inferred:
    # a long paragraph is replaced by its sentences, so testing membership in
    # `paragraphs` reports False for the sentence that *begins* one and the
    # joiner silently merges it into the paragraph before it.
    units: list[tuple[str, bool]] = []
    for paragraph in paragraphs:
        if len(paragraph) <= target:
            units.append((paragraph, True))
            continue
        sentences = [
            sentence.strip()
            for sentence in _SENTENCE_END.split(paragraph)
            if sentence.strip()
        ]
        if any(len(sentence) > DISCORD_MESSAGE_LIMIT for sentence in sentences):
            raise ValueError("a sentence exceeds Discord's 2000 character limit")
        units.extend((sentence, index == 0) for index, sentence in enumerate(sentences))

    chunks: list[str] = []
    current = ""
    for unit, starts_paragraph in units:
        separator = "\n\n" if current and starts_paragraph else " "
        candidate = f"{current}{separator}{unit}" if current else unit
        if current and len(candidate) > target:
            chunks.append(current)
            current = unit
        else:
            current = candidate
        if len(current) > DISCORD_MESSAGE_LIMIT:
            raise ValueError("a sentence exceeds Discord's 2000 character limit")
    if current:
        chunks.append(current)
    return chunks


class Speech:
    """Serialize, post, and persist NPC speech per Discord channel."""

    def __init__(self) -> None:
        self._webhooks: dict[int, discord.Webhook] = {}
        self._locks: dict[int, asyncio.Lock] = {}

    async def speak(
        self,
        session: AsyncSession,
        channel: WebhookChannel,
        persona: Persona,
        text: str,
    ) -> discord.WebhookMessage:
        """Post all chunks as one persona and return the final Discord message.

        The session is per call; the webhook cache and per-channel lock are not.
        Both belong to the process, so a caller that builds a fresh `Speech` per
        command silently loses them — chunks from two NPCs interleave, and every
        call re-creates a webhook against Discord's per-channel cap of ten.
        Hold one instance for the lifetime of the cog and pass the session in.
        """
        chunks = chunk_prose(text)
        lock = self._locks.setdefault(channel.id, asyncio.Lock())

        async with lock:
            scene = await repo.get_active_scene(session, persona.guild_id, channel.id)
            if scene is None:
                raise ValueError("cannot speak without an active scene")

            webhook = await self._get_or_create_webhook(channel)
            last_message: discord.WebhookMessage | None = None
            for chunk in chunks:
                try:
                    message = await webhook.send(
                        content=chunk,
                        username=persona.name,
                        avatar_url=persona.avatar_url,
                        wait=True,
                    )
                except discord.NotFound:
                    self._webhooks.pop(channel.id, None)
                    webhook = await self._create_webhook(channel)
                    message = await webhook.send(
                        content=chunk,
                        username=persona.name,
                        avatar_url=persona.avatar_url,
                        wait=True,
                    )

                last_message = message
                await repo.create_scene_message(
                    session,
                    persona.guild_id,
                    scene_id=scene.id,
                    discord_message_id=last_message.id,
                    author_type="npc",
                    author_name=persona.name,
                    persona_id=persona.id,
                    content=chunk,
                )

            assert last_message is not None
            return last_message

    async def _get_or_create_webhook(self, channel: WebhookChannel) -> discord.Webhook:
        cached = self._webhooks.get(channel.id)
        if cached is not None:
            return cached

        webhooks = await channel.webhooks()
        webhook = next(
            (
                candidate
                for candidate in webhooks
                if candidate.name == WEBHOOK_NAME and candidate.token is not None
            ),
            None,
        )
        if webhook is None:
            return await self._create_webhook(channel)
        self._webhooks[channel.id] = webhook
        return webhook

    async def _create_webhook(self, channel: WebhookChannel) -> discord.Webhook:
        webhook = await channel.create_webhook(name=WEBHOOK_NAME)
        self._webhooks[channel.id] = webhook
        return webhook
