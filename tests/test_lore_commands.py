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
