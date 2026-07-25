"""Tests for the provider-neutral model gateway."""

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import Settings
from bot.db.models import Guild, UsageLog
from bot.engine.llm import BudgetExceededError, LLMEngine
from bot.engine.providers import ProviderError
from bot.engine.schemas import NpcReply


def settings() -> Settings:
    return Settings(
        DISCORD_TOKEN="test-token",
        DEV_GUILD_ID=123,
        LLM_PROVIDER="anthropic",
        ANTHROPIC_API_KEY="test-key",
        ANTHROPIC_MODEL_DIALOGUE="configured-dialogue",
    )


async def test_completion_is_validated_routed_and_logged(
    db_session: AsyncSession, fake_provider
) -> None:
    guild = Guild(discord_guild_id=123, name="Test")
    db_session.add(guild)
    await db_session.flush()
    engine = LLMEngine(
        settings=settings(),
        session=db_session,
        guild_id=guild.id,
        provider=fake_provider,
    )

    reply = await engine.complete(
        "dialogue",
        ["static", "dynamic"],
        [{"role": "user", "content": "Who goes there?"}],
        NpcReply,
        "dialogue",
    )

    assert reply.line == "The old road remembers."
    usage = (await db_session.scalars(select(UsageLog))).one()
    assert (
        usage.provider,
        usage.model,
        usage.input_tokens,
        usage.cache_read_tokens,
        usage.output_tokens,
        usage.purpose,
        usage.guild_id,
    ) == ("anthropic", "configured-dialogue", 12, 4, 7, "dialogue", guild.id)


async def test_retry_uses_bounded_exponential_backoff(
    db_session: AsyncSession, fake_provider
) -> None:
    guild = Guild(discord_guild_id=123, name="Test")
    db_session.add(guild)
    await db_session.flush()
    delays: list[float] = []
    fake_provider.failures = [
        ProviderError("rate limited", retryable=True),
        ProviderError("server error", retryable=True),
    ]
    engine = LLMEngine(
        settings=settings(),
        session=db_session,
        guild_id=guild.id,
        provider=fake_provider,
        backoff_seconds=0.25,
        sleep=delays.append,
    )

    await engine.complete("dialogue", [], [], NpcReply, "dialogue")

    assert fake_provider.calls == 3
    assert delays == [0.25, 0.5]
    assert len((await db_session.scalars(select(UsageLog))).all()) == 1


async def test_daily_budget_stops_call_before_inference(
    db_session: AsyncSession, fake_provider
) -> None:
    guild = Guild(discord_guild_id=123, name="Test", daily_reply_budget=0)
    db_session.add(guild)
    await db_session.flush()
    engine = LLMEngine(
        settings=settings(),
        session=db_session,
        guild_id=guild.id,
        provider=fake_provider,
    )

    with pytest.raises(BudgetExceededError, match="spirits are silent"):
        await engine.complete("dialogue", [], [], NpcReply, "dialogue")

    assert fake_provider.calls == 0


async def test_malformed_reply_raises_validation_error(
    db_session: AsyncSession, fake_provider
) -> None:
    guild = Guild(discord_guild_id=123, name="Test")
    db_session.add(guild)
    await db_session.flush()
    fake_provider.payload = {"line": 42, "memory_notes": "not a list"}
    engine = LLMEngine(
        settings=settings(),
        session=db_session,
        guild_id=guild.id,
        provider=fake_provider,
    )

    with pytest.raises(ValidationError):
        await engine.complete("dialogue", [], [], NpcReply, "dialogue")
