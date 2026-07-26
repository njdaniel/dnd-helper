"""Scene lifecycle commands and player transcript logging."""

from __future__ import annotations

from dataclasses import dataclass

import discord
from discord import Interaction, app_commands
from discord.ext import commands
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import Settings
from bot.db import repo
from bot.db.models import Persona, Scene
from bot.db.session import (
    SessionFactory,
    create_engine,
    create_session_factory,
    session_scope,
)


class ActiveSceneError(ValueError):
    """Raised when a channel already has an active scene."""


class ScenePersonaError(ValueError):
    """Raised when a requested NPC cannot be put on stage."""


@dataclass(frozen=True)
class SceneView:
    """Scene data needed to render the command response."""

    scene: Scene
    location: str | None
    personas: list[Persona]


def parse_npc_names(value: str | None) -> list[str]:
    """Parse a comma-separated NPC list, preserving order and removing repeats."""
    names: list[str] = []
    seen: set[str] = set()
    for raw_name in (value or "").split(","):
        name = raw_name.strip()
        key = name.casefold()
        if name and key not in seen:
            names.append(name)
            seen.add(key)
    return names


def scene_embed(view: SceneView) -> discord.Embed:
    """Render the current scene state."""
    embed = discord.Embed(title=view.scene.title or "Untitled scene")
    embed.add_field(name="Location", value=view.location or "Unknown", inline=False)
    embed.add_field(
        name="On stage",
        value=", ".join(persona.name for persona in view.personas) or "Nobody",
        inline=False,
    )
    return embed


class SceneService:
    """Guild-scoped scene operations independent of Discord interactions."""

    def __init__(self, sessions: SessionFactory) -> None:
        self.sessions = sessions

    async def start(
        self,
        discord_guild_id: int,
        guild_name: str,
        channel_id: int,
        title: str | None,
        npc_names: list[str],
    ) -> SceneView:
        try:
            async with session_scope(self.sessions) as session:
                guild = await repo.get_or_create_guild(
                    session, discord_guild_id, guild_name
                )
                if await repo.get_active_scene(session, guild.id, channel_id):
                    raise ActiveSceneError

                personas: list[Persona] = []
                for name in npc_names:
                    persona = await repo.get_persona_by_name(session, guild.id, name)
                    if persona is None or persona.status != "active":
                        raise ScenePersonaError(name)
                    personas.append(persona)

                scene = await repo.create_scene(
                    session,
                    guild.id,
                    channel_id=channel_id,
                    title=title.strip() if title and title.strip() else None,
                )
                for persona in personas:
                    await repo.add_scene_persona(
                        session, guild.id, scene.id, persona.id
                    )
                return SceneView(scene=scene, location=None, personas=personas)
        except IntegrityError as error:
            # The pre-check provides the normal friendly path. The partial
            # unique index remains authoritative if two starts race.
            raise ActiveSceneError from error

    async def add(
        self,
        discord_guild_id: int,
        guild_name: str,
        channel_id: int,
        npc_name: str,
    ) -> SceneView:
        async with session_scope(self.sessions) as session:
            guild = await repo.get_or_create_guild(
                session, discord_guild_id, guild_name
            )
            scene = await repo.get_active_scene(session, guild.id, channel_id)
            if scene is None:
                raise ValueError("no active scene")
            persona = await repo.get_persona_by_name(session, guild.id, npc_name)
            if persona is None or persona.status != "active":
                raise ScenePersonaError(npc_name)
            personas = await repo.list_scene_personas(session, guild.id, scene.id)
            if persona.id not in {item.id for item in personas}:
                await repo.add_scene_persona(session, guild.id, scene.id, persona.id)
                personas.append(persona)
                personas.sort(key=lambda item: item.name)
            return SceneView(
                scene=scene,
                location=await _location_name(session, guild.id, scene),
                personas=personas,
            )

    async def end(
        self, discord_guild_id: int, guild_name: str, channel_id: int
    ) -> SceneView | None:
        async with session_scope(self.sessions) as session:
            guild = await repo.get_or_create_guild(
                session, discord_guild_id, guild_name
            )
            scene = await repo.get_active_scene(session, guild.id, channel_id)
            if scene is None:
                return None
            personas = await repo.list_scene_personas(session, guild.id, scene.id)
            location = await _location_name(session, guild.id, scene)
            scene.status = "ended"
            await session.flush()
            return SceneView(scene=scene, location=location, personas=personas)

    async def log_channel_message(
        self,
        discord_guild_id: int,
        channel_id: int,
        message_id: int,
        author_name: str,
        content: str,
        author_role_ids: frozenset[int] = frozenset(),
        author_is_owner: bool = False,
    ) -> bool:
        """Record a line from this channel if it has a live scene.

        This is the only path that runs for *every* message the bot can see, so
        it looks the guild up read-only and gives up as soon as there is no
        active scene. Creating a guild row here would enrol any server the bot
        merely sits in, and would make routine conversation a database write.

        The DM/player distinction is preserved because the transcript feeds
        back into prompts: logging a DM's narration as a player line makes the
        NPC read it as another character speaking, and the ambient trigger
        counts it towards player activity.
        """
        async with session_scope(self.sessions) as session:
            guild = await repo.get_guild(session, discord_guild_id)
            if guild is None:
                return False
            scene = await repo.get_active_scene(session, guild.id, channel_id)
            if scene is None:
                return False
            await repo.create_scene_message(
                session,
                guild.id,
                scene_id=scene.id,
                discord_message_id=message_id,
                author_type=(
                    "dm"
                    if author_is_owner
                    or (
                        guild.dm_role_id is not None
                        and guild.dm_role_id in author_role_ids
                    )
                    else "player"
                ),
                author_name=author_name,
                persona_id=None,
                content=content,
            )
            return True


async def _location_name(
    session: AsyncSession, guild_id: int, scene: Scene
) -> str | None:
    if scene.location_lore_id is None:
        return None
    lore = await repo.get_lore_entry(session, guild_id, scene.location_lore_id)
    return lore.title if lore is not None else None


class SceneCommands(commands.GroupCog, group_name="scene"):
    """Commands for starting and managing scenes."""

    def __init__(
        self,
        bot: commands.Bot,
        settings: Settings,
        *,
        sessions: SessionFactory | None = None,
    ) -> None:
        self.bot = bot
        self._engine = None
        if sessions is None:
            self._engine = create_engine(settings.database_url)
            sessions = create_session_factory(self._engine)
        self.service = SceneService(sessions)

    async def cog_unload(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()

    @app_commands.command(name="start", description="Start a scene in this channel.")
    @app_commands.describe(
        title="Optional scene title",
        npcs="Optional comma-separated NPC names",
    )
    async def start(
        self,
        interaction: Interaction,
        title: str | None = None,
        npcs: str | None = None,
    ) -> None:
        context = await self._server_context(interaction)
        if context is None:
            return
        guild, channel_id = context
        try:
            view = await self.service.start(
                guild.id,
                guild.name,
                channel_id,
                title,
                parse_npc_names(npcs),
            )
        except ActiveSceneError:
            await interaction.response.send_message(
                "A scene is already active in this channel. End it with `/scene end` "
                "before starting another.",
                ephemeral=True,
            )
            return
        except ScenePersonaError as error:
            await interaction.response.send_message(
                f'No active NPC named "{error.args[0]}" was found.',
                ephemeral=True,
            )
            return
        await interaction.response.send_message(embed=scene_embed(view))

    @app_commands.command(name="add", description="Add an NPC to the active scene.")
    @app_commands.describe(npc="NPC name")
    async def add(self, interaction: Interaction, npc: str) -> None:
        context = await self._server_context(interaction)
        if context is None:
            return
        guild, channel_id = context
        try:
            view = await self.service.add(guild.id, guild.name, channel_id, npc)
        except ScenePersonaError:
            await interaction.response.send_message(
                f'No active NPC named "{npc}" was found.', ephemeral=True
            )
            return
        except ValueError:
            await interaction.response.send_message(
                "There is no active scene in this channel.", ephemeral=True
            )
            return
        await interaction.response.send_message(embed=scene_embed(view))

    @app_commands.command(name="end", description="End the active scene.")
    async def end(self, interaction: Interaction) -> None:
        context = await self._server_context(interaction)
        if context is None:
            return
        guild, channel_id = context
        view = await self.service.end(guild.id, guild.name, channel_id)
        if view is None:
            await interaction.response.send_message(
                "There is no active scene in this channel.", ephemeral=True
            )
            return
        await interaction.response.send_message("Scene ended.", embed=scene_embed(view))

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        if message.webhook_id is not None or message.author.bot:
            return
        if message.content.startswith("(("):
            return
        if message.guild is None:
            return
        await self.service.log_channel_message(
            message.guild.id,
            message.channel.id,
            message.id,
            message.author.display_name,
            message.content,
            author_role_ids=frozenset(
                role.id for role in getattr(message.author, "roles", ())
            ),
            author_is_owner=message.author.id == message.guild.owner_id,
        )

    async def _server_context(
        self, interaction: Interaction
    ) -> tuple[discord.Guild, int] | None:
        if interaction.guild is None or interaction.channel_id is None:
            await interaction.response.send_message(
                "Scene commands can only be used in a server channel.",
                ephemeral=True,
            )
            return None
        return interaction.guild, interaction.channel_id


async def setup(bot: commands.Bot) -> None:
    """Load scene commands into a configured bot."""
    settings = getattr(bot, "settings", None)
    if not isinstance(settings, Settings):
        raise RuntimeError("the bot must expose validated settings before loading cogs")
    await bot.add_cog(SceneCommands(bot, settings))
