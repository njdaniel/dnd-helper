"""Discord commands for guild-scoped lore management."""

from __future__ import annotations

from collections.abc import Sequence

import discord
from discord import app_commands
from discord.ext import commands
from sqlalchemy.exc import IntegrityError

from bot.db import repo
from bot.db.models import Guild, LoreEntry
from bot.db.session import SessionFactory

LORE_CATEGORIES = ("location", "faction", "person", "event", "item", "rule", "other")
LORE_VISIBILITIES = ("public", "dm_only")
PAGE_SIZE = 10
NOT_FOUND = "No lore entry with that title was found."
DM_ONLY = "Only a DM can use this command."

# Discord's limits, which differ at each end of the same flow. A title is how
# every other `/lore` command addresses an entry, so it has to survive the
# round trip through an autocomplete choice — a title longer than this is
# storable but not selectable, which makes the entry unreachable.
CHOICE_LIMIT = 100
EMBED_FIELD_LIMIT = 1024


def _parse_tags(value: str) -> list[str]:
    return list(dict.fromkeys(tag.strip() for tag in value.split(",") if tag.strip()))


def _fit_field(value: str) -> str:
    """Fit a value into an embed field rather than lose the whole response."""
    if len(value) <= EMBED_FIELD_LIMIT:
        return value
    return value[: EMBED_FIELD_LIMIT - 1] + "…"


def _member_is_dm(interaction: discord.Interaction, guild: Guild) -> bool:
    member = interaction.user
    discord_guild = interaction.guild
    if not isinstance(member, discord.Member) or discord_guild is None:
        return False
    return (
        member.id == discord_guild.owner_id
        or member.guild_permissions.administrator
        or (
            guild.dm_role_id is not None
            and any(role.id == guild.dm_role_id for role in member.roles)
        )
    )


def _entry_embed(entry: LoreEntry) -> discord.Embed:
    embed = discord.Embed(title=entry.title, description=entry.body)
    embed.add_field(name="Category", value=entry.category)
    embed.add_field(name="Visibility", value=entry.visibility)
    embed.add_field(
        name="Tags", value=_fit_field(", ".join(entry.tags) or "None"), inline=False
    )
    return embed


class LoreListView(discord.ui.View):
    """A private, button-driven paginator for lore titles."""

    def __init__(self, entries: Sequence[LoreEntry], owner_id: int) -> None:
        super().__init__(timeout=180)
        self.entries = entries
        self.owner_id = owner_id
        self.page = 0
        self._sync_buttons()

    def embed(self) -> discord.Embed:
        start = self.page * PAGE_SIZE
        page_entries = self.entries[start : start + PAGE_SIZE]
        lines = [
            f"**{entry.title}** — {entry.category}"
            + (" *(DM only)*" if entry.visibility == "dm_only" else "")
            for entry in page_entries
        ]
        pages = max(1, (len(self.entries) + PAGE_SIZE - 1) // PAGE_SIZE)
        return discord.Embed(
            title="Lore",
            description="\n".join(lines) or "No lore entries found.",
        ).set_footer(text=f"Page {self.page + 1}/{pages}")

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id == self.owner_id:
            return True
        await interaction.response.send_message(
            "Only the person who opened this list can change pages.", ephemeral=True
        )
        return False

    def _sync_buttons(self) -> None:
        pages = max(1, (len(self.entries) + PAGE_SIZE - 1) // PAGE_SIZE)
        self.previous.disabled = self.page == 0
        self.next.disabled = self.page >= pages - 1

    @discord.ui.button(label="Previous", style=discord.ButtonStyle.secondary)
    async def previous(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button[LoreListView],
    ) -> None:
        self.page -= 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)

    @discord.ui.button(label="Next", style=discord.ButtonStyle.secondary)
    async def next(
        self,
        interaction: discord.Interaction,
        _button: discord.ui.Button[LoreListView],
    ) -> None:
        self.page += 1
        self._sync_buttons()
        await interaction.response.edit_message(embed=self.embed(), view=self)


class LoreModal(discord.ui.Modal):
    # Capped at the autocomplete limit rather than the column width: a longer
    # title stores fine and then cannot be picked from `/lore view`, `edit`, or
    # `remove`, so the entry exists and is unreachable.
    title_input: discord.ui.TextInput[LoreModal] = discord.ui.TextInput(
        label="Title", max_length=CHOICE_LIMIT
    )
    body: discord.ui.TextInput[LoreModal] = discord.ui.TextInput(
        label="Body", style=discord.TextStyle.paragraph
    )
    category: discord.ui.TextInput[LoreModal] = discord.ui.TextInput(
        label="Category", max_length=20
    )
    tags: discord.ui.TextInput[LoreModal] = discord.ui.TextInput(
        label="Tags (comma-separated)", required=False
    )
    visibility: discord.ui.TextInput[LoreModal] = discord.ui.TextInput(
        label="Visibility (public or dm_only)", default="public", max_length=10
    )

    def __init__(
        self,
        cog: LoreCog,
        *,
        entry: LoreEntry | None = None,
    ) -> None:
        super().__init__(title="Edit lore" if entry else "Add lore")
        self.cog = cog
        self.entry_id = entry.id if entry else None
        if entry is not None:
            self.title_input.default = entry.title
            self.body.default = entry.body
            self.category.default = entry.category
            self.tags.default = ", ".join(entry.tags)
            self.visibility.default = entry.visibility

    async def on_submit(self, interaction: discord.Interaction) -> None:
        category = str(self.category).strip().lower()
        visibility = str(self.visibility).strip().lower()
        if category not in LORE_CATEGORIES or visibility not in LORE_VISIBILITIES:
            await interaction.response.send_message(
                "Category or visibility is invalid.", ephemeral=True
            )
            return
        # Discord counts whitespace towards a required field, so "   " arrives
        # here as a filled-in title and strips to nothing. An entry titled ""
        # cannot be named by any other command and cannot be autocompleted.
        if not str(self.title_input).strip():
            await interaction.response.send_message(
                "A lore entry needs a title — every other `/lore` command finds "
                "it by that name.",
                ephemeral=True,
            )
            return
        guild = await self.cog.guild_for(interaction)
        if guild is None or not _member_is_dm(interaction, guild):
            await interaction.response.send_message(DM_ONLY, ephemeral=True)
            return
        values: dict[str, object] = {
            "title": str(self.title_input).strip(),
            "body": str(self.body).strip(),
            "category": category,
            "tags": _parse_tags(str(self.tags)),
            "visibility": visibility,
        }
        try:
            async with self.cog.session_factory.begin() as session:
                if self.entry_id is None:
                    await repo.create_lore_entry(
                        session,
                        guild.id,
                        **values,
                        source="manual",
                        created_by=interaction.user.id,
                    )
                else:
                    updated = await repo.update_lore_entry(
                        session,
                        guild.id,
                        self.entry_id,
                        title=values["title"],
                        body=values["body"],
                        category=values["category"],
                        tags=values["tags"],
                        visibility=values["visibility"],
                    )
                    if updated is None:
                        await interaction.response.send_message(
                            NOT_FOUND, ephemeral=True
                        )
                        return
        except IntegrityError:
            # A title is how every /lore command addresses an entry, so the
            # duplicate has to be refused rather than accepted and shadowed.
            await interaction.response.send_message(
                f"A lore entry titled **{values['title']}** already exists in "
                "this campaign. Pick a different title, or edit the existing "
                "entry instead.",
                ephemeral=True,
            )
            return
        action = "updated" if self.entry_id is not None else "added"
        await interaction.response.send_message(
            f"Lore entry **{values['title']}** {action}.", ephemeral=True
        )


class LoreCog(commands.GroupCog, group_name="lore"):
    """Create, browse, and maintain campaign lore."""

    def __init__(self, bot: commands.Bot, session_factory: SessionFactory) -> None:
        self.bot = bot
        self.session_factory = session_factory

    async def guild_for(self, interaction: discord.Interaction) -> Guild | None:
        if interaction.guild_id is None or interaction.guild is None:
            return None
        async with self.session_factory.begin() as session:
            return await repo.get_or_create_guild(
                session, interaction.guild_id, interaction.guild.name
            )

    async def _context(
        self, interaction: discord.Interaction
    ) -> tuple[Guild, bool] | None:
        guild = await self.guild_for(interaction)
        if guild is None:
            await interaction.response.send_message(
                "Lore commands can only be used in a server.", ephemeral=True
            )
            return None
        return guild, _member_is_dm(interaction, guild)

    @app_commands.command(name="add", description="Add campaign lore.")
    async def add(self, interaction: discord.Interaction) -> None:
        context = await self._context(interaction)
        if context is None:
            return
        _, is_dm = context
        if not is_dm:
            await interaction.response.send_message(DM_ONLY, ephemeral=True)
            return
        await interaction.response.send_modal(LoreModal(self))

    @app_commands.command(name="list", description="List campaign lore.")
    @app_commands.describe(category="Only show entries in this category")
    async def list_entries(
        self, interaction: discord.Interaction, category: str | None = None
    ) -> None:
        context = await self._context(interaction)
        if context is None:
            return
        guild, is_dm = context
        async with self.session_factory() as session:
            entries = await repo.list_lore_entries(
                session,
                guild.id,
                category=category,
                visible_to=None if is_dm else "public",
            )
        view = LoreListView(entries, interaction.user.id)
        await interaction.response.send_message(
            embed=view.embed(), view=view, ephemeral=True
        )

    @app_commands.command(name="view", description="View a lore entry.")
    async def view(self, interaction: discord.Interaction, title: str) -> None:
        context = await self._context(interaction)
        if context is None:
            return
        guild, is_dm = context
        async with self.session_factory() as session:
            entry = await repo.get_lore_entry_by_title(
                session,
                guild.id,
                title,
                visible_to=None if is_dm else "public",
            )
        if entry is None:
            await interaction.response.send_message(NOT_FOUND, ephemeral=True)
            return
        await interaction.response.send_message(
            embed=_entry_embed(entry), ephemeral=True
        )

    @app_commands.command(name="edit", description="Edit a lore entry.")
    async def edit(self, interaction: discord.Interaction, title: str) -> None:
        context = await self._context(interaction)
        if context is None:
            return
        guild, is_dm = context
        if not is_dm:
            await interaction.response.send_message(DM_ONLY, ephemeral=True)
            return
        async with self.session_factory() as session:
            entry = await repo.get_lore_entry_by_title(session, guild.id, title)
        if entry is None:
            await interaction.response.send_message(NOT_FOUND, ephemeral=True)
            return
        await interaction.response.send_modal(LoreModal(self, entry=entry))

    @app_commands.command(name="remove", description="Remove a lore entry.")
    async def remove(self, interaction: discord.Interaction, title: str) -> None:
        context = await self._context(interaction)
        if context is None:
            return
        guild, is_dm = context
        if not is_dm:
            await interaction.response.send_message(DM_ONLY, ephemeral=True)
            return
        async with self.session_factory.begin() as session:
            entry = await repo.get_lore_entry_by_title(session, guild.id, title)
            removed = (
                await repo.delete_lore_entry(session, guild.id, entry.id)
                if entry is not None
                else False
            )
        if not removed:
            await interaction.response.send_message(NOT_FOUND, ephemeral=True)
            return
        await interaction.response.send_message(
            f"Lore entry **{title}** removed.", ephemeral=True
        )

    async def _title_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        context = await self._context(interaction)
        if context is None:
            return []
        guild, is_dm = context
        async with self.session_factory() as session:
            entries = await repo.list_lore_entries(
                session, guild.id, visible_to=None if is_dm else "public"
            )
        current = current.casefold()
        # New titles are capped at CHOICE_LIMIT on the way in, so this only
        # excludes rows written before that cap or edited directly in the
        # database. Skipping one keeps autocomplete working for every other
        # entry; emitting it makes Discord reject the whole response, and the
        # user gets no suggestions at all.
        return [
            app_commands.Choice(name=entry.title, value=entry.title)
            for entry in entries
            if current in entry.title.casefold() and len(entry.title) <= CHOICE_LIMIT
        ][:25]

    @list_entries.autocomplete("category")
    async def category_autocomplete(
        self, _interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        current = current.casefold()
        return [
            app_commands.Choice(name=category.title(), value=category)
            for category in LORE_CATEGORIES
            if current in category
        ]

    @view.autocomplete("title")
    @edit.autocomplete("title")
    @remove.autocomplete("title")
    async def title_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        return await self._title_autocomplete(interaction, current)


async def setup(bot: commands.Bot) -> None:
    session_factory = getattr(bot, "session_factory", None)
    if session_factory is None:
        raise RuntimeError("Bot must expose session_factory before loading LoreCog")
    await bot.add_cog(LoreCog(bot, session_factory))
