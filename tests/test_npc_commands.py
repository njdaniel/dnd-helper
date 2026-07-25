"""Logic tests for NPC command behavior."""

import discord
import pytest
from discord.ui import TextInput
from sqlalchemy.ext.asyncio import AsyncSession

from bot.commands.npc import (
    CreateNpcModal,
    DuplicatePersonaNameError,
    NpcService,
    PersonaDetails,
    may_view_secrets,
    parse_tags,
    persona_embed,
)
from bot.db import repo
from bot.db.session import create_session_factory


def test_create_modal_has_exactly_five_expected_fields() -> None:
    modal = CreateNpcModal(None)  # type: ignore[arg-type]

    assert len(modal.children) == 5
    assert all(isinstance(item, TextInput) for item in modal.children)
    assert [item.label for item in modal.children if isinstance(item, TextInput)] == [
        "Name",
        "Public description",
        "Personality",
        "Goals",
        "Secrets",
    ]


def test_parse_tags_normalizes_and_deduplicates() -> None:
    assert parse_tags(" harbor, Guild ,harbor, , GUILD ") == ["harbor", "Guild"]


def test_secret_visibility_is_limited_to_owner_or_dm_role() -> None:
    assert may_view_secrets(user_id=1, owner_id=1, role_ids=set(), dm_role_id=9)
    assert may_view_secrets(user_id=2, owner_id=1, role_ids={9}, dm_role_id=9)
    assert not may_view_secrets(user_id=2, owner_id=1, role_ids={8}, dm_role_id=9)
    assert not may_view_secrets(user_id=2, owner_id=1, role_ids=set(), dm_role_id=None)


async def test_service_rejects_duplicate_and_retirement_hides_autocomplete(
    db_session: AsyncSession,
) -> None:
    service = NpcService(create_session_factory(db_session.bind))  # type: ignore[arg-type]
    details = PersonaDetails(
        name="Mira",
        public_desc="A cartographer",
        personality="Careful",
        goals="Map the ruins",
        secrets="Knows the hidden road",
    )

    created = await service.create(1001, "Campaign", 10, details)
    assert created.name == "Mira"
    assert await service.autocomplete(1001, "Campaign", "mir") == ["Mira"]

    with pytest.raises(DuplicatePersonaNameError):
        await service.create(1001, "Campaign", 10, details)

    retired = await service.retire(1001, "Campaign", "Mira")
    assert retired is not None
    assert retired.status == "retired"
    assert await service.autocomplete(1001, "Campaign", "") == []

    _, preserved = await service.get(1001, "Campaign", "Mira")
    assert preserved is not None
    assert preserved.status == "retired"


async def test_avatar_tags_and_guild_isolation(
    db_session: AsyncSession,
) -> None:
    service = NpcService(create_session_factory(db_session.bind))  # type: ignore[arg-type]
    details = PersonaDetails("Mira", "A cartographer", "Careful", "Map", "")
    await service.create(1001, "First", 10, details)

    assert (
        await service.set_avatar(2002, "Second", "Mira", "https://example.com/mira.png")
        is None
    )
    updated = await service.set_tags(1001, "First", "Mira", ["ruins", "roads"])

    assert updated is not None
    assert updated.knowledge_tags == ["ruins", "roads"]


async def test_persona_embed_hides_secrets(
    db_session: AsyncSession,
) -> None:
    guild = await repo.get_or_create_guild(db_session, 1001, "Campaign")
    persona = await repo.create_persona(
        db_session,
        guild.id,
        name="Mira",
        public_desc="A cartographer",
        personality="Careful",
        goals="Map the ruins",
        secrets="SENTINEL SECRET",
        knowledge_tags=[],
        created_by=10,
    )

    public_embed = persona_embed(persona, include_secrets=False)
    dm_embed = persona_embed(persona, include_secrets=True)

    assert "SENTINEL SECRET" not in str(public_embed.to_dict())
    assert "SENTINEL SECRET" in str(dm_embed.to_dict())
    assert isinstance(public_embed, discord.Embed)
