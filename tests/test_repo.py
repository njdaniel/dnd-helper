"""Database model and repository behavior."""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db import repo


async def test_get_or_create_guild_is_idempotent(db_session: AsyncSession) -> None:
    first = await repo.get_or_create_guild(db_session, 1001, "The First Campaign")
    second = await repo.get_or_create_guild(db_session, 1001, "Renamed on Discord")

    assert first.id == second.id
    assert second.name == "The First Campaign"


async def test_personas_are_isolated_by_guild(db_session: AsyncSession) -> None:
    first_guild = await repo.get_or_create_guild(db_session, 1001, "First")
    second_guild = await repo.get_or_create_guild(db_session, 2002, "Second")
    await repo.create_persona(
        db_session,
        first_guild.id,
        name="Mira",
        public_desc="A cartographer",
        personality="Careful",
        goals="Map the ruins",
        knowledge_tags=["ruins"],
        created_by=10,
    )
    await repo.create_persona(
        db_session,
        second_guild.id,
        name="Voss",
        public_desc="A sailor",
        personality="Blunt",
        goals="Find a crew",
        knowledge_tags=["harbor"],
        created_by=20,
    )

    personas = await repo.list_personas(db_session, first_guild.id)

    assert [persona.name for persona in personas] == ["Mira"]


async def test_persona_name_is_unique_within_guild(
    db_session: AsyncSession,
) -> None:
    guild = await repo.get_or_create_guild(db_session, 1001, "First")
    values = {
        "name": "Mira",
        "public_desc": "A cartographer",
        "personality": "Careful",
        "goals": "Map the ruins",
        "knowledge_tags": [],
        "created_by": 10,
    }
    await repo.create_persona(db_session, guild.id, **values)

    with pytest.raises(IntegrityError):
        await repo.create_persona(db_session, guild.id, **values)


async def test_lore_crud_is_guild_and_visibility_scoped(
    db_session: AsyncSession,
) -> None:
    first = await repo.get_or_create_guild(db_session, 1001, "First")
    second = await repo.get_or_create_guild(db_session, 2002, "Second")
    public = await repo.create_lore_entry(
        db_session,
        first.id,
        title="Old Harbor",
        body="Ships shelter here.",
        category="location",
        tags=["coast"],
        visibility="public",
        created_by=10,
    )
    secret = await repo.create_lore_entry(
        db_session,
        first.id,
        title="The Hidden Fleet",
        body="It waits below the cliffs.",
        category="faction",
        tags=["coast"],
        visibility="dm_only",
        created_by=10,
    )
    await repo.create_lore_entry(
        db_session,
        second.id,
        title="Other Harbor",
        body="Not part of the first campaign.",
        category="location",
        tags=[],
        visibility="public",
        created_by=20,
    )

    player_entries = await repo.list_lore_entries(
        db_session, first.id, visible_to="public"
    )
    locations = await repo.list_lore_entries(
        db_session, first.id, category="location", visible_to="public"
    )

    assert [entry.title for entry in player_entries] == ["Old Harbor"]
    assert [entry.title for entry in locations] == ["Old Harbor"]
    assert (
        await repo.get_lore_entry_by_title(
            db_session, first.id, secret.title, visible_to="public"
        )
        is None
    )
    assert (
        await repo.get_lore_entry(db_session, first.id, secret.id, visible_to="public")
        is None
    )

    updated = await repo.update_lore_entry(
        db_session,
        first.id,
        public.id,
        visible_to="public",
        body="Ships and fishing boats shelter here.",
    )
    assert updated is not None
    assert updated.body == "Ships and fishing boats shelter here."
    assert (
        await repo.update_lore_entry(
            db_session,
            second.id,
            public.id,
            visible_to="public",
            body="Cross-guild overwrite",
        )
        is None
    )
    assert not await repo.delete_lore_entry(
        db_session, first.id, secret.id, visible_to="public"
    )
    assert await repo.delete_lore_entry(db_session, first.id, public.id)
    assert await repo.get_lore_entry(db_session, first.id, public.id) is None


async def test_only_one_active_scene_per_channel(
    db_session: AsyncSession,
) -> None:
    guild = await repo.get_or_create_guild(db_session, 1001, "First")
    await repo.create_scene(db_session, guild.id, channel_id=55)
    await repo.create_scene(db_session, guild.id, channel_id=55, status="ended")

    with pytest.raises(IntegrityError):
        await repo.create_scene(db_session, guild.id, channel_id=55)


async def test_lore_title_is_unique_within_guild(db_session: AsyncSession) -> None:
    """Titles address entries in every /lore command, so a duplicate would
    make one entry permanently unreachable and show two identical
    autocomplete choices. The database refuses it rather than the lookup
    silently picking the older row."""
    guild = await repo.get_or_create_guild(db_session, 700, "Test Guild")
    await repo.create_lore_entry(
        db_session,
        guild.id,
        title="The Ford",
        body="First.",
        category="location",
        tags=[],
        visibility="public",
        source="manual",
        created_by=1,
    )
    with pytest.raises(IntegrityError):
        await repo.create_lore_entry(
            db_session,
            guild.id,
            title="The Ford",
            body="Second.",
            category="location",
            tags=[],
            visibility="public",
            source="manual",
            created_by=1,
        )


async def test_same_lore_title_allowed_in_a_different_guild(
    db_session: AsyncSession,
) -> None:
    """Uniqueness is per guild — two campaigns may each have 'The Ford'."""
    first = await repo.get_or_create_guild(db_session, 801, "Campaign A")
    second = await repo.get_or_create_guild(db_session, 802, "Campaign B")
    for guild in (first, second):
        await repo.create_lore_entry(
            db_session,
            guild.id,
            title="The Ford",
            body="Shared name, separate campaigns.",
            category="location",
            tags=[],
            visibility="public",
            source="manual",
            created_by=1,
        )
    await db_session.flush()
