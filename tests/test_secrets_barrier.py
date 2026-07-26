"""The player-facing prompt must be structurally free of DM-only material."""

from bot.db.models import LoreEntry
from bot.engine import persona as persona_engine

from .test_prompt_assembly import _persona, _scene

SENTINEL = "SENTINEL-EMBER-VAULT-7391"


def _dm_lore() -> LoreEntry:
    return LoreEntry(
        guild_id=1,
        title="The Ember Vault",
        body=f"The hidden passphrase is {SENTINEL}.",
        category="location",
        tags=["vault"],
        visibility="dm_only",
        source="manual",
        created_by=10,
    )


def test_dm_only_material_is_absent_from_player_prompt(monkeypatch) -> None:
    npc = _persona(secrets=f"Mira guards {SENTINEL}.")
    monkeypatch.setattr(
        persona_engine,
        "retrieve_lore",
        lambda persona, scene: [_dm_lore()],
    )

    player_blocks, player_messages = persona_engine.assemble_prompt(
        npc, _scene(), is_dm_context=False
    )
    player_prompt = "\n".join(
        [*player_blocks, *(message["content"] for message in player_messages)]
    )

    assert "DM-ONLY MATERIAL" not in player_prompt
    assert SENTINEL not in player_prompt


def test_dm_only_material_is_present_in_dm_prompt(monkeypatch) -> None:
    npc = _persona(secrets=f"Mira guards {SENTINEL}.")
    monkeypatch.setattr(
        persona_engine,
        "retrieve_lore",
        lambda persona, scene: [_dm_lore()],
    )

    dm_blocks, dm_messages = persona_engine.assemble_prompt(
        npc, _scene(), is_dm_context=True
    )
    dm_prompt = "\n".join(
        [*dm_blocks, *(message["content"] for message in dm_messages)]
    )

    assert "DM-ONLY MATERIAL" in dm_prompt
    assert SENTINEL in dm_prompt
