"""Prompt assembly and structured NPC reply generation."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from bot.db.models import LoreEntry, Persona, SceneMessage
from bot.engine.llm import LLMEngine, Tier
from bot.engine.schemas import Message, NpcReply


class ScenePrompt(Protocol):
    """Scene data loaded by the caller for prompt assembly."""

    content_rating: str
    location: str | None
    on_stage: Sequence[str]
    summary: str
    messages: Sequence[SceneMessage]


def retrieve_lore(persona: Persona, scene: ScenePrompt) -> list[LoreEntry]:
    """Return lore relevant to this NPC and scene.

    Retrieval is intentionally empty until issue #17 implements memory search.
    """
    del persona, scene
    return []


def assemble_prompt(
    persona: Persona,
    scene: ScenePrompt,
    is_dm_context: bool,
) -> tuple[list[str], list[Message]]:
    """Build ordered system blocks and scene dialogue for one NPC turn."""
    lore = retrieve_lore(persona, scene)
    public_lore = [entry for entry in lore if entry.visibility != "dm_only"]
    dm_lore = [entry for entry in lore if entry.visibility == "dm_only"]

    system_blocks = [
        _game_master_block(scene.content_rating),
        _persona_block(persona),
    ]
    if is_dm_context:
        system_blocks.append(_dm_only_block(persona.secrets, dm_lore))
    system_blocks.extend(
        [
            _lore_block(public_lore),
            _scene_block(scene),
        ]
    )

    messages: list[Message] = [
        {
            "role": "assistant" if row.author_type == "npc" else "user",
            "content": f"{row.author_name}: {row.content}",
        }
        for row in scene.messages
    ]
    return system_blocks, messages


async def generate_reply(
    engine: LLMEngine,
    persona: Persona,
    scene: ScenePrompt,
    is_dm_context: bool,
    *,
    tier: Tier = "dialogue",
) -> NpcReply:
    """Generate a schema-validated reply through the provider-neutral engine."""
    system_blocks, messages = assemble_prompt(persona, scene, is_dm_context)
    return await engine.complete(
        "dialogue",
        system_blocks,
        messages,
        NpcReply,
        tier,
    )


def _game_master_block(content_rating: str) -> str:
    return "\n".join(
        [
            "GAME-MASTER INSTRUCTIONS",
            "Stay in character.",
            "Use second- or third-person narration.",
            "Never speak for player characters.",
            "Never invent mechanics or resolve dice; defer those decisions to the DM.",
            f"Respect the campaign content rating: {content_rating}.",
            "Respond in no more than 1–3 short paragraphs.",
        ]
    )


def _persona_block(persona: Persona) -> str:
    return "\n".join(
        [
            "PERSONA CARD",
            f"Name: {persona.name}",
            f"Public description: {persona.public_desc}",
            f"Personality: {persona.personality}",
            f"Goals: {persona.goals}",
        ]
    )


def _dm_only_block(
    secrets: str | None,
    lore: Sequence[LoreEntry],
) -> str:
    lines = ["DM-ONLY MATERIAL", f"Persona secrets: {secrets or 'None'}"]
    lines.extend(f"{entry.title}: {entry.body}" for entry in lore)
    return "\n".join(lines)


def _lore_block(lore: Sequence[LoreEntry]) -> str:
    lines = ["RETRIEVED LORE"]
    lines.extend(f"{entry.title}: {entry.body}" for entry in lore)
    if len(lines) == 1:
        lines.append("None retrieved.")
    return "\n".join(lines)


def _scene_block(scene: ScenePrompt) -> str:
    location = scene.location or "Unknown"
    on_stage = ", ".join(scene.on_stage) or "None"
    summary = scene.summary or "No scene summary yet."
    return "\n".join(
        [
            "SCENE CARD",
            f"Location: {location}",
            f"On stage: {on_stage}",
            f"Summary: {summary}",
        ]
    )
