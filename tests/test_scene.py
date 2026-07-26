"""Scene lifecycle and transcript tests."""

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

    assert await service.log_player_message(
        1001, "Campaign", 55, 9001, "Player", "We wait and listen."
    )
    assert not await service.log_player_message(
        1001, "Campaign", 99, 9002, "Player", "Elsewhere."
    )

    guild = await repo.get_or_create_guild(db_session, 1001, "Campaign")
    scene = await repo.get_active_scene(db_session, guild.id, 55)
    assert scene is not None
    messages = await repo.list_scene_messages(db_session, guild.id, scene.id)
    assert [(row.author_type, row.author_name, row.content) for row in messages] == [
        ("player", "Player", "We wait and listen.")
    ]
