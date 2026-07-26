"""Prompt ordering, dialogue rendering, and structured reply tests."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import Settings
from bot.db.models import Guild, Persona, SceneMessage
from bot.engine.llm import LLMEngine
from bot.engine.persona import assemble_prompt, generate_reply
from bot.engine.schemas import NpcReply


@dataclass
class PromptScene:
    content_rating: str
    location: str | None
    on_stage: list[str]
    summary: str
    messages: list[SceneMessage]


def _persona(*, secrets: str | None = "Knows where the crown is buried.") -> Persona:
    return Persona(
        guild_id=1,
        name="Mira",
        public_desc="A road-worn cartographer.",
        personality="Careful and dryly funny.",
        goals="Map the vanished road.",
        secrets=secrets,
        knowledge_tags=["roads"],
        created_by=10,
    )


def _scene() -> PromptScene:
    return PromptScene(
        content_rating="pg13",
        location="The Crooked Lantern",
        on_stage=["Mira", "Tovin"],
        summary="The party is seeking a road erased from every map.",
        messages=[
            SceneMessage(
                guild_id=1,
                scene_id=2,
                discord_message_id=100,
                author_type="player",
                author_name="Aria",
                content="Have you seen this symbol?",
            ),
            SceneMessage(
                guild_id=1,
                scene_id=2,
                discord_message_id=101,
                author_type="npc",
                author_name="Mira",
                persona_id=3,
                content="Perhaps. Where did you find it?",
            ),
        ],
    )


def test_assemble_prompt_orders_static_blocks_before_dynamic_context() -> None:
    blocks, messages = assemble_prompt(_persona(), _scene(), is_dm_context=True)

    assert [block.splitlines()[0] for block in blocks] == [
        "GAME-MASTER INSTRUCTIONS",
        "PERSONA CARD",
        "DM-ONLY MATERIAL",
        "RETRIEVED LORE",
        "SCENE CARD",
    ]
    assert messages == [
        {"role": "user", "content": "Aria: Have you seen this symbol?"},
        {
            "role": "assistant",
            "content": "Mira: Perhaps. Where did you find it?",
        },
    ]


async def test_generate_reply_uses_engine_structured_output(
    db_session: AsyncSession, fake_provider
) -> None:
    guild = Guild(discord_guild_id=123, name="Test")
    db_session.add(guild)
    await db_session.flush()
    engine = LLMEngine(
        settings=Settings(
            DISCORD_TOKEN="test-token",
            DEV_GUILD_ID=123,
            LLM_PROVIDER="anthropic",
            ANTHROPIC_API_KEY="test-key",
            ANTHROPIC_MODEL_DIALOGUE="configured-dialogue",
        ),
        session=db_session,
        guild_id=guild.id,
        provider=fake_provider,
    )

    reply = await generate_reply(engine, _persona(), _scene(), False)

    assert isinstance(reply, NpcReply)
    assert reply.line == "The old road remembers."
    assert fake_provider.calls == 1
