"""NPC persona management commands."""

from __future__ import annotations

from dataclasses import dataclass

import discord
from discord import Interaction, app_commands
from discord.ext import commands
from sqlalchemy.exc import IntegrityError

from bot.config import Settings
from bot.db import repo
from bot.db.models import Guild, Persona
from bot.db.session import (
    SessionFactory,
    create_engine,
    create_session_factory,
    session_scope,
)


class DuplicatePersonaNameError(ValueError):
    """Raised when a persona name is already in use in a guild."""


@dataclass(frozen=True)
class PersonaDetails:
    """Values collected by the five-field create modal."""

    name: str
    public_desc: str
    personality: str
    goals: str
    secrets: str


def parse_tags(value: str) -> list[str]:
    """Normalize comma-separated tags while preserving their input order."""
    tags: list[str] = []
    seen: set[str] = set()
    for raw_tag in value.split(","):
        tag = raw_tag.strip()
        key = tag.casefold()
        if tag and key not in seen:
            seen.add(key)
            tags.append(tag)
    return tags


def may_view_secrets(
    *, user_id: int, owner_id: int | None, role_ids: set[int], dm_role_id: int | None
) -> bool:
    """Return whether a guild member may see DM-only persona details."""
    return user_id == owner_id or (dm_role_id is not None and dm_role_id in role_ids)


def persona_embed(persona: Persona, *, include_secrets: bool) -> discord.Embed:
    """Render a persona without accidentally exposing its secrets."""
    embed = discord.Embed(title=persona.name, description=persona.public_desc)
    embed.add_field(name="Status", value=persona.status, inline=True)
    embed.add_field(
        name="Tags",
        value=", ".join(persona.knowledge_tags) or "None",
        inline=True,
    )
    embed.add_field(name="Personality", value=persona.personality, inline=False)
    embed.add_field(name="Goals", value=persona.goals, inline=False)
    if include_secrets:
        embed.add_field(name="Secrets", value=persona.secrets or "None", inline=False)
    if persona.avatar_url:
        embed.set_thumbnail(url=persona.avatar_url)
    return embed


class NpcService:
    """Guild-scoped persona operations independent of Discord interactions."""

    def __init__(self, sessions: SessionFactory) -> None:
        self.sessions = sessions

    async def ensure_guild(self, discord_guild_id: int, guild_name: str) -> Guild:
        async with session_scope(self.sessions) as session:
            return await repo.get_or_create_guild(session, discord_guild_id, guild_name)

    async def create(
        self,
        discord_guild_id: int,
        guild_name: str,
        creator_id: int,
        details: PersonaDetails,
    ) -> Persona:
        try:
            async with session_scope(self.sessions) as session:
                guild = await repo.get_or_create_guild(
                    session, discord_guild_id, guild_name
                )
                if await repo.get_persona_by_name(session, guild.id, details.name):
                    raise DuplicatePersonaNameError(details.name)
                return await repo.create_persona(
                    session,
                    guild.id,
                    name=details.name,
                    public_desc=details.public_desc,
                    personality=details.personality,
                    goals=details.goals,
                    secrets=details.secrets or None,
                    knowledge_tags=[],
                    created_by=creator_id,
                )
        except IntegrityError as error:
            # The pre-check gives the common path a friendly response; the
            # constraint remains authoritative if two creates race.
            raise DuplicatePersonaNameError(details.name) from error

    async def get(
        self, discord_guild_id: int, guild_name: str, name: str
    ) -> tuple[Guild, Persona | None]:
        async with session_scope(self.sessions) as session:
            guild = await repo.get_or_create_guild(
                session, discord_guild_id, guild_name
            )
            return guild, await repo.get_persona_by_name(session, guild.id, name)

    async def list_personas(
        self, discord_guild_id: int, guild_name: str
    ) -> list[Persona]:
        async with session_scope(self.sessions) as session:
            guild = await repo.get_or_create_guild(
                session, discord_guild_id, guild_name
            )
            return await repo.list_personas(session, guild.id)

    async def autocomplete(
        self, discord_guild_id: int, guild_name: str, current: str
    ) -> list[str]:
        async with session_scope(self.sessions) as session:
            guild = await repo.get_or_create_guild(
                session, discord_guild_id, guild_name
            )
            personas = await repo.list_personas(session, guild.id, status="active")
        needle = current.casefold()
        return [
            persona.name for persona in personas if needle in persona.name.casefold()
        ][:25]

    async def set_avatar(
        self, discord_guild_id: int, guild_name: str, name: str, url: str
    ) -> Persona | None:
        return await self._update(discord_guild_id, guild_name, name, avatar_url=url)

    async def set_tags(
        self, discord_guild_id: int, guild_name: str, name: str, tags: list[str]
    ) -> Persona | None:
        return await self._update(
            discord_guild_id, guild_name, name, knowledge_tags=tags
        )

    async def retire(
        self, discord_guild_id: int, guild_name: str, name: str
    ) -> Persona | None:
        return await self._update(discord_guild_id, guild_name, name, status="retired")

    async def _update(
        self,
        discord_guild_id: int,
        guild_name: str,
        name: str,
        **values: object,
    ) -> Persona | None:
        async with session_scope(self.sessions) as session:
            guild = await repo.get_or_create_guild(
                session, discord_guild_id, guild_name
            )
            persona = await repo.get_persona_by_name(session, guild.id, name)
            if persona is None:
                return None
            for key, value in values.items():
                setattr(persona, key, value)
            await session.flush()
            return persona


class CreateNpcModal(discord.ui.Modal, title="Create NPC"):
    """Collect the five core persona attributes supported by Discord."""

    name: discord.ui.TextInput[CreateNpcModal] = discord.ui.TextInput(
        label="Name", max_length=255
    )
    public_desc: discord.ui.TextInput[CreateNpcModal] = discord.ui.TextInput(
        label="Public description", style=discord.TextStyle.paragraph
    )
    personality: discord.ui.TextInput[CreateNpcModal] = discord.ui.TextInput(
        label="Personality", style=discord.TextStyle.paragraph
    )
    goals: discord.ui.TextInput[CreateNpcModal] = discord.ui.TextInput(
        label="Goals", style=discord.TextStyle.paragraph
    )
    secrets: discord.ui.TextInput[CreateNpcModal] = discord.ui.TextInput(
        label="Secrets",
        style=discord.TextStyle.paragraph,
        required=False,
    )

    def __init__(self, cog: Npc) -> None:
        super().__init__()
        self.cog = cog

    async def on_submit(self, interaction: Interaction) -> None:
        details = PersonaDetails(
            name=str(self.name).strip(),
            public_desc=str(self.public_desc).strip(),
            personality=str(self.personality).strip(),
            goals=str(self.goals).strip(),
            secrets=str(self.secrets).strip(),
        )
        await self.cog.create_from_modal(interaction, details)


class Npc(commands.GroupCog, group_name="npc"):
    """Commands for creating and maintaining NPC personas."""

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
        self.service = NpcService(sessions)

    async def cog_unload(self) -> None:
        if self._engine is not None:
            await self._engine.dispose()

    @app_commands.command(name="create", description="Create a new NPC.")
    async def create(self, interaction: Interaction) -> None:
        await interaction.response.send_modal(CreateNpcModal(self))

    async def create_from_modal(
        self, interaction: Interaction, details: PersonaDetails
    ) -> None:
        guild = interaction.guild
        if guild is None:
            await interaction.response.send_message(
                "NPC commands can only be used in a server.", ephemeral=True
            )
            return
        try:
            persona = await self.service.create(
                guild.id, guild.name, interaction.user.id, details
            )
        except DuplicatePersonaNameError:
            await interaction.response.send_message(
                f'An NPC named "{details.name}" already exists in this server.',
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "NPC created. Use `/npc set-avatar "
            f"{persona.name} <url>` and `/npc set-tags {persona.name} <tags>` "
            "to fill in the rest.",
            embed=persona_embed(persona, include_secrets=True),
            ephemeral=True,
        )

    async def _guild_or_reply(self, interaction: Interaction) -> discord.Guild | None:
        if interaction.guild is None:
            await interaction.response.send_message(
                "NPC commands can only be used in a server.", ephemeral=True
            )
            return None
        return interaction.guild

    async def _autocomplete(
        self, interaction: Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        guild = interaction.guild
        if guild is None:
            return []
        names = await self.service.autocomplete(guild.id, guild.name, current)
        return [app_commands.Choice(name=name, value=name) for name in names]

    @app_commands.command(name="set-avatar", description="Set an NPC avatar URL.")
    @app_commands.describe(name="NPC name", url="Publicly reachable image URL")
    @app_commands.autocomplete(name=_autocomplete)
    async def set_avatar(self, interaction: Interaction, name: str, url: str) -> None:
        guild = await self._guild_or_reply(interaction)
        if guild is None:
            return
        persona = await self.service.set_avatar(guild.id, guild.name, name, url)
        if persona is None:
            await interaction.response.send_message(
                f'No NPC named "{name}" was found.', ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Updated {persona.name}'s avatar.", ephemeral=True
        )

    @app_commands.command(name="set-tags", description="Set an NPC's lore tags.")
    @app_commands.describe(name="NPC name", tags="Comma-separated lore tags")
    @app_commands.autocomplete(name=_autocomplete)
    async def set_tags(self, interaction: Interaction, name: str, tags: str) -> None:
        guild = await self._guild_or_reply(interaction)
        if guild is None:
            return
        persona = await self.service.set_tags(
            guild.id, guild.name, name, parse_tags(tags)
        )
        if persona is None:
            await interaction.response.send_message(
                f'No NPC named "{name}" was found.', ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Updated {persona.name}'s tags.", ephemeral=True
        )

    @app_commands.command(name="list", description="List this server's NPCs.")
    async def list_npcs(self, interaction: Interaction) -> None:
        guild = await self._guild_or_reply(interaction)
        if guild is None:
            return
        personas = await self.service.list_personas(guild.id, guild.name)
        if not personas:
            await interaction.response.send_message(
                "No NPCs have been created yet.", ephemeral=True
            )
            return
        lines = [f"• **{persona.name}** — {persona.status}" for persona in personas]
        await interaction.response.send_message("\n".join(lines), ephemeral=True)

    @app_commands.command(name="view", description="View an NPC.")
    @app_commands.describe(name="NPC name")
    @app_commands.autocomplete(name=_autocomplete)
    async def view(self, interaction: Interaction, name: str) -> None:
        guild = await self._guild_or_reply(interaction)
        if guild is None:
            return
        db_guild, persona = await self.service.get(guild.id, guild.name, name)
        if persona is None:
            await interaction.response.send_message(
                f'No NPC named "{name}" was found.', ephemeral=True
            )
            return
        role_ids = {role.id for role in getattr(interaction.user, "roles", ())}
        include_secrets = may_view_secrets(
            user_id=interaction.user.id,
            owner_id=guild.owner_id,
            role_ids=role_ids,
            dm_role_id=db_guild.dm_role_id,
        )
        await interaction.response.send_message(
            embed=persona_embed(persona, include_secrets=include_secrets),
            ephemeral=True,
        )

    @app_commands.command(name="retire", description="Retire an NPC.")
    @app_commands.describe(name="NPC name")
    @app_commands.autocomplete(name=_autocomplete)
    async def retire(self, interaction: Interaction, name: str) -> None:
        guild = await self._guild_or_reply(interaction)
        if guild is None:
            return
        persona = await self.service.retire(guild.id, guild.name, name)
        if persona is None:
            await interaction.response.send_message(
                f'No NPC named "{name}" was found.', ephemeral=True
            )
            return
        await interaction.response.send_message(
            f"Retired {persona.name}. Its campaign history was preserved.",
            ephemeral=True,
        )


async def setup(bot: commands.Bot) -> None:
    """Load the NPC command group into a configured bot."""
    settings = getattr(bot, "settings", None)
    if not isinstance(settings, Settings):
        raise RuntimeError("the bot must expose validated settings before loading cogs")
    await bot.add_cog(Npc(bot, settings))
