"""Discord command behavior for lore CRUD."""

from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest_asyncio
from discord.ext import commands
from sqlalchemy.ext.asyncio import AsyncEngine

from bot.commands.lore import DM_ONLY, NOT_FOUND, LoreCog
from bot.db import repo
from bot.db.models import Base
from bot.db.session import SessionFactory, create_engine, create_session_factory


@pytest_asyncio.fixture
async def database() -> AsyncIterator[tuple[AsyncEngine, SessionFactory]]:
    engine = create_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    yield engine, factory
    await engine.dispose()


def interaction(*, administrator: bool = False) -> MagicMock:
    value = MagicMock(spec=discord.Interaction)
    value.guild_id = 1001
    value.guild = MagicMock(spec=discord.Guild)
    value.guild.name = "Campaign"
    value.guild.owner_id = 999
    value.user = MagicMock(spec=discord.Member)
    value.user.id = 42
    value.user.guild_permissions.administrator = administrator
    value.user.roles = []
    value.response = MagicMock(spec=discord.InteractionResponse)
    value.response.send_message = AsyncMock()
    value.response.send_modal = AsyncMock()
    return value


def cog(factory: SessionFactory) -> LoreCog:
    return LoreCog(MagicMock(spec=commands.Bot), factory)


async def seed_lore(factory: SessionFactory) -> None:
    async with factory.begin() as session:
        guild = await repo.get_or_create_guild(session, 1001, "Campaign")
        await repo.create_lore_entry(
            session,
            guild.id,
            title="Market Square",
            body="A busy public plaza.",
            category="location",
            tags=["city"],
            visibility="public",
            created_by=42,
        )
        await repo.create_lore_entry(
            session,
            guild.id,
            title="Hidden Throne",
            body="The regent serves an ancient dragon.",
            category="person",
            tags=["secret"],
            visibility="dm_only",
            created_by=42,
        )


async def test_non_dm_cannot_see_dm_only_lore_through_any_read_path(
    database: tuple[AsyncEngine, SessionFactory],
) -> None:
    _, factory = database
    await seed_lore(factory)
    lore = cog(factory)

    list_interaction = interaction()
    await lore.list_entries.callback(lore, list_interaction, None)
    list_embed = list_interaction.response.send_message.await_args.kwargs["embed"]
    assert "Market Square" in list_embed.description
    assert "Hidden Throne" not in list_embed.description

    view_interaction = interaction()
    await lore.view.callback(lore, view_interaction, "Hidden Throne")
    view_interaction.response.send_message.assert_awaited_once_with(
        NOT_FOUND, ephemeral=True
    )

    autocomplete_interaction = interaction()
    choices = await lore._title_autocomplete(autocomplete_interaction, "")
    assert [choice.value for choice in choices] == ["Market Square"]


async def test_dm_can_view_and_list_dm_only_lore(
    database: tuple[AsyncEngine, SessionFactory],
) -> None:
    _, factory = database
    await seed_lore(factory)
    lore = cog(factory)

    list_interaction = interaction(administrator=True)
    await lore.list_entries.callback(lore, list_interaction, None)
    embed = list_interaction.response.send_message.await_args.kwargs["embed"]
    assert "Hidden Throne" in embed.description

    view_interaction = interaction(administrator=True)
    await lore.view.callback(lore, view_interaction, "Hidden Throne")
    embed = view_interaction.response.send_message.await_args.kwargs["embed"]
    assert embed.title == "Hidden Throne"
    assert "ancient dragon" in embed.description


async def test_mutations_require_dm(
    database: tuple[AsyncEngine, SessionFactory],
) -> None:
    _, factory = database
    await seed_lore(factory)
    lore = cog(factory)

    for command, args in (
        (lore.add, ()),
        (lore.edit, ("Market Square",)),
        (lore.remove, ("Market Square",)),
    ):
        denied = interaction()
        await command.callback(lore, denied, *args)
        denied.response.send_message.assert_awaited_once_with(DM_ONLY, ephemeral=True)


async def test_dm_can_remove_lore(
    database: tuple[AsyncEngine, SessionFactory],
) -> None:
    _, factory = database
    await seed_lore(factory)
    lore = cog(factory)
    remove_interaction = interaction(administrator=True)

    await lore.remove.callback(lore, remove_interaction, "Market Square")

    async with factory() as session:
        guild = await repo.get_or_create_guild(session, 1001, "Campaign")
        assert (
            await repo.get_lore_entry_by_title(session, guild.id, "Market Square")
            is None
        )


def test_lore_titles_stay_selectable_and_fields_stay_renderable() -> None:
    """Three ways a valid input produced an entry Discord then refused.

    All three share a shape: the write succeeds and the *render* fails, so the
    row exists while the user only ever sees an error.
    """
    from bot.commands.lore import (
        CHOICE_LIMIT,
        EMBED_FIELD_LIMIT,
        LoreModal,
        _entry_embed,
    )
    from bot.db.models import LoreEntry

    # A title longer than an autocomplete choice is storable but unselectable,
    # which breaks /lore view, /lore edit and /lore remove for that entry.
    assert LoreModal.title_input.max_length == CHOICE_LIMIT

    # Tags come from a text input with no bound of its own and land in an
    # embed field that caps at 1,024.
    entry = LoreEntry(
        guild_id=1,
        title="The Long Road",
        body="It goes on.",
        category="location",
        tags=[f"tag-{index}" for index in range(300)],
        visibility="public",
        source="manual",
        created_by=1,
    )
    embed = _entry_embed(entry)
    for field in embed.fields:
        assert len(field.value or "") <= EMBED_FIELD_LIMIT, field.name


async def test_whitespace_only_title_is_refused(
    database: tuple[AsyncEngine, SessionFactory],
) -> None:
    """Discord counts "   " as a filled-in required field. Stripped, it is an
    empty title — an entry no other command can name."""
    from bot.commands.lore import LoreModal

    _, factory = database
    modal = LoreModal(cog(factory))
    modal.title_input._value = "   "
    modal.body._value = "A body."
    modal.category._value = "location"
    modal.tags._value = ""
    modal.visibility._value = "public"

    submit = interaction(administrator=True)
    await modal.on_submit(submit)

    message = submit.response.send_message.await_args.args[0]
    assert "needs a title" in message

    async with factory() as session:
        guild = await repo.get_or_create_guild(session, 1001, "Campaign")
        assert await repo.list_lore_entries(session, guild.id) == []


async def test_autocomplete_outside_a_guild_returns_no_choices(
    database: tuple[AsyncEngine, SessionFactory],
) -> None:
    """Autocomplete may only answer with choices.

    The server-only path used to reply with `send_message`, which Discord
    rejects for an autocomplete interaction — so instead of a helpful message
    the user got an interaction error and no suggestions.
    """
    _, factory = database
    await seed_lore(factory)
    lore = cog(factory)

    outside = interaction(administrator=True)
    outside.guild = None
    outside.guild_id = None

    assert await lore.title_autocomplete(outside, "") == []
    outside.response.send_message.assert_not_awaited()
