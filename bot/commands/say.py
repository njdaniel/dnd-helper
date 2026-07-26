"""Explicit NPC speech command."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import cast

from discord import Interaction, app_commands
from discord.ext import commands

from bot.commands.npc import may_view_secrets
from bot.config import Settings
from bot.db import repo
from bot.db.models import SceneMessage
from bot.db.session import (
    SessionFactory,
    create_engine,
    create_session_factory,
    session_scope,
)
from bot.engine.llm import BudgetExceededError, LLMEngine
from bot.engine.persona import generate_reply
from bot.engine.speech import Speech, WebhookChannel


@dataclass
class SayScenePrompt:
    """Concrete scene prompt passed to persona generation."""

    content_rating: str
    location: str | None
    on_stage: Sequence[str]
    summary: str
    messages: Sequence[SceneMessage]


class Say(commands.Cog):
    """Generate a requested line and post it as an on-stage NPC."""

    def __init__(
        self,
        bot: commands.Bot,
        settings: Settings,
        *,
        sessions: SessionFactory | None = None,
    ) -> None:
        self.bot = bot
        self.settings = settings
        self._engine = None
        if sessions is None:
            self._engine = create_engine(settings.database_url)
            sessions = create_session_factory(self._engine)
        self.sessions = sessions
        # One instance for the life of the cog: the webhook cache and the
        # per-channel lock live on it, and a per-call instance would start
        # both empty every time.
        self._speech = Speech()

    async def cog_unload(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()

    @app_commands.command(name="say", description="Ask an NPC to speak in character.")
    @app_commands.describe(npc="On-stage NPC name", message="What you say to the NPC")
    async def say(self, interaction: Interaction, npc: str, message: str) -> None:
        guild = interaction.guild
        channel = interaction.channel
        if guild is None or channel is None:
            await interaction.response.send_message(
                "`/say` can only be used in a server channel.", ephemeral=True
            )
            return
        # Checked before generating, not after. An NPC speaks through a channel
        # webhook, and a thread — like a voice or forum channel — has no
        # `webhooks()` or `create_webhook()` of its own. Left to fail naturally
        # it raises inside the speech layer *after* a 16–36s local generation
        # has already run and been charged to the reply budget, and the player
        # sees nothing posted.
        if not _can_host_a_webhook(channel):
            await interaction.response.send_message(
                "NPCs speak through a channel webhook, which this kind of "
                "channel does not support. Use `/say` in a regular text "
                "channel.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            async with session_scope(self.sessions) as session:
                db_guild = await repo.get_or_create_guild(session, guild.id, guild.name)
                scene = await repo.get_active_scene(session, db_guild.id, channel.id)
                if scene is None:
                    await interaction.followup.send(
                        "There is no active scene in this channel.", ephemeral=True
                    )
                    return
                persona = await repo.get_persona_by_name(session, db_guild.id, npc)
                on_stage = await repo.list_scene_personas(
                    session, db_guild.id, scene.id
                )
                if persona is None or persona.id not in {item.id for item in on_stage}:
                    await interaction.followup.send(
                        f'"{npc}" is not on stage in this scene.', ephemeral=True
                    )
                    return

                await repo.create_scene_message(
                    session,
                    db_guild.id,
                    scene_id=scene.id,
                    discord_message_id=interaction.id,
                    author_type=(
                        "dm" if _is_dm(interaction, db_guild.dm_role_id) else "player"
                    ),
                    author_name=interaction.user.display_name,
                    persona_id=None,
                    content=message,
                )
                location = None
                if scene.location_lore_id is not None:
                    lore = await repo.get_lore_entry(
                        session, db_guild.id, scene.location_lore_id
                    )
                    location = lore.title if lore is not None else None
                prompt = SayScenePrompt(
                    content_rating=db_guild.content_rating,
                    location=location,
                    on_stage=[item.name for item in on_stage],
                    summary=scene.summary,
                    messages=await repo.list_scene_messages(
                        session, db_guild.id, scene.id
                    ),
                )

            # Deliberately a second transaction. The block above ends here, so
            # the player's line is committed and SQLite's write lock released
            # before the model is called. A local 27B reply takes 16–36s on the
            # reference machine; holding the write lock for that long makes any
            # concurrent `/say` or `on_message` log fail with "database is
            # locked". Sessions are created with expire_on_commit=False, so the
            # objects loaded above stay usable here.
            async with session_scope(self.sessions) as session:
                reply = await generate_reply(
                    LLMEngine(
                        settings=self.settings,
                        session=session,
                        guild_id=db_guild.id,
                    ),
                    persona,
                    prompt,
                    is_dm_context=_is_dm_only_channel(interaction, db_guild.dm_role_id),
                    tier="epic" if scene.mode == "epic" else "dialogue",
                )
                await self._speech.speak(
                    session, cast(WebhookChannel, channel), persona, reply.line
                )
        except BudgetExceededError as error:
            await interaction.followup.send(str(error), ephemeral=True)
            return
        await interaction.followup.send(f"{npc} spoke.", ephemeral=True)


def _can_host_a_webhook(channel: object) -> bool:
    """Whether an NPC can be given a name and face in this channel.

    Asked by capability rather than by type: threads, voice and forum channels
    all lack the webhook API, and a type allowlist would have to grow every
    time Discord adds one. Anything that cannot be confirmed is refused, which
    costs a usable channel at worst and never burns a generation.
    """
    return callable(getattr(channel, "webhooks", None)) and callable(
        getattr(channel, "create_webhook", None)
    )


def _is_dm_only_channel(interaction: Interaction, dm_role_id: int | None) -> bool:
    """Whether every member who can read this channel is a DM.

    Secret visibility is a property of the **destination**, not the caller. A
    DM running `/say` in the tavern still posts the reply where every player
    reads it, so deriving `is_dm_context` from the caller's role would put
    `persona.secrets` and `dm_only` lore into a prompt whose output is public.
    That is hard rule #1, and the reason the parameter is explicit at all.

    Asking only whether `@everyone` is denied is not enough: a channel can deny
    the default role and still grant `view_channel` to a player role or to an
    individual member, and that channel is not private. The question that
    actually matters is who ends up able to read it, so this checks the
    channel's resolved member list rather than any single overwrite.

    Everything undeterminable counts as player-facing — an unknown channel
    type, no readable member list, or a list that is empty. The fail-safe
    direction is to omit a secret that could have been used, never to include
    one that should not have been.
    """
    guild = interaction.guild
    channel = interaction.channel
    if guild is None or channel is None:
        return False
    members = getattr(channel, "members", None)
    if not members:
        return False
    return all(
        may_view_secrets(
            user_id=member.id,
            owner_id=guild.owner_id,
            role_ids={role.id for role in getattr(member, "roles", ())},
            dm_role_id=dm_role_id,
        )
        for member in members
    )


def _is_dm(interaction: Interaction, dm_role_id: int | None) -> bool:
    guild = interaction.guild
    role_ids = {role.id for role in getattr(interaction.user, "roles", ())}
    return may_view_secrets(
        user_id=interaction.user.id,
        owner_id=guild.owner_id if guild is not None else None,
        role_ids=role_ids,
        dm_role_id=dm_role_id,
    )


async def setup(bot: commands.Bot) -> None:
    """Load the explicit speech command into a configured bot."""
    settings = getattr(bot, "settings", None)
    if not isinstance(settings, Settings):
        raise RuntimeError("the bot must expose validated settings before loading cogs")
    await bot.add_cog(Say(bot, settings))
