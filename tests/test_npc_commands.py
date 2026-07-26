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


def test_persona_fields_fit_discord_embed_limits() -> None:
    """An over-long field made creation commit and then fail to render.

    Modal paragraph inputs allow 4,000 characters; embed field values cap at
    1,024. Without capping both ends the NPC exists in the database while the
    user sees an error — the worst of both outcomes.
    """
    from bot.commands.npc import EMBED_FIELD_LIMIT, persona_embed
    from bot.db.models import Persona

    persona = Persona(
        guild_id=1,
        name="Verbose Vera",
        public_desc="x" * 5000,
        personality="y" * 5000,
        goals="z" * 5000,
        secrets="s" * 5000,
        # Tags are the one field a user can overrun without going through a
        # modal: `/npc set-tags` takes a plain string option, so the joined
        # value has no upper bound of its own.
        knowledge_tags=[f"tag-{index}" for index in range(300)],
        status="active",
        created_by=1,
    )
    embed = persona_embed(persona, include_secrets=True)
    for field in embed.fields:
        assert len(field.value or "") <= EMBED_FIELD_LIMIT, field.name
    assert len(embed.description or "") <= EMBED_FIELD_LIMIT


def test_avatar_url_is_rejected_before_it_reaches_discord() -> None:
    """A stored bad URL breaks `/npc view` and the NPC's webhook face, and the
    error surfaces far from the command that caused it. Refuse it at input."""
    from bot.commands.npc import is_usable_image_url

    assert is_usable_image_url("https://example.com/vera.png")
    assert is_usable_image_url("  http://example.com/vera.png  ")

    assert not is_usable_image_url("/home/nick/portraits/vera.png")
    assert not is_usable_image_url("file:///home/nick/portraits/vera.png")
    assert not is_usable_image_url("example.com/vera.png")
    assert not is_usable_image_url("")


def test_modal_inputs_cannot_exceed_what_an_embed_can_render() -> None:
    """Cap at the source too, so the truncation above is a safety net rather
    than the mechanism — a user should not silently lose text they typed."""
    import discord

    from bot.commands.npc import EMBED_FIELD_LIMIT, CreateNpcModal

    paragraphs = [
        item
        for item in CreateNpcModal.__dict__.values()
        if isinstance(item, discord.ui.TextInput)
        and item.style is discord.TextStyle.paragraph
    ]
    assert paragraphs, "no paragraph inputs found"
    for item in paragraphs:
        assert item.max_length == EMBED_FIELD_LIMIT, item.label
