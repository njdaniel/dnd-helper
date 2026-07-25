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


async def test_only_one_active_scene_per_channel(
    db_session: AsyncSession,
) -> None:
    guild = await repo.get_or_create_guild(db_session, 1001, "First")
    await repo.create_scene(db_session, guild.id, channel_id=55)
    await repo.create_scene(db_session, guild.id, channel_id=55, status="ended")

    with pytest.raises(IntegrityError):
        await repo.create_scene(db_session, guild.id, channel_id=55)
