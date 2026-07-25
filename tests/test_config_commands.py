"""Tests for guild configuration and usage reporting."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import discord
import pytest_asyncio

from bot.commands.config import ConfigCog
from bot.config import Settings
from bot.db.models import Base, Guild, UsageLog
from bot.db.session import (
    SessionFactory,
    create_engine,
    create_session_factory,
)


def _settings(provider: str = "ollama") -> Settings:
    values: dict[str, object] = {
        "DISCORD_TOKEN": "test-token",
        "DEV_GUILD_ID": 123,
        "LLM_PROVIDER": provider,
    }
    if provider == "anthropic":
        values["ANTHROPIC_API_KEY"] = "test-key"
    return Settings(**values)


@pytest_asyncio.fixture
async def session_factory() -> AsyncIterator[SessionFactory]:
    engine = create_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = create_session_factory(engine)
    yield factory
    await engine.dispose()


def _interaction(
    *,
    user_id: int = 10,
    owner_id: int = 10,
    roles: list[object] | None = None,
) -> SimpleNamespace:
    guild = SimpleNamespace(id=123, name="Test Guild", owner_id=owner_id)
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.roles = roles or []
    return SimpleNamespace(
        guild=guild,
        user=user,
        response=SimpleNamespace(send_message=AsyncMock()),
    )


async def _invoke(command, cog: ConfigCog, *args, **kwargs) -> None:
    await command.callback(cog, *args, **kwargs)


async def test_config_sets_all_supported_values(
    session_factory: SessionFactory,
) -> None:
    cog = ConfigCog(session_factory, _settings())
    interaction = _interaction()
    dm_role = SimpleNamespace(id=77, mention="<@&77>")

    await _invoke(
        ConfigCog.config,
        cog,
        interaction,
        content_rating="mature",
        dm_role=dm_role,
        daily_reply_budget=42,
    )

    async with session_factory() as session:
        guild = await session.get(Guild, 1)
        assert guild is not None
        assert (
            guild.content_rating,
            guild.dm_role_id,
            guild.daily_reply_budget,
        ) == ("mature", 77, 42)
    interaction.response.send_message.assert_awaited_once()
    assert interaction.response.send_message.await_args.kwargs["ephemeral"] is True


async def test_config_allows_dm_role_and_rejects_other_members(
    session_factory: SessionFactory,
) -> None:
    async with session_factory.begin() as session:
        session.add(
            Guild(
                discord_guild_id=123,
                name="Test Guild",
                dm_role_id=77,
            )
        )
    cog = ConfigCog(session_factory, _settings())
    dm_role = SimpleNamespace(id=77)

    allowed = _interaction(user_id=20, owner_id=10, roles=[dm_role])
    await _invoke(
        ConfigCog.config,
        cog,
        allowed,
        content_rating="teen",
    )

    denied = _interaction(user_id=30, owner_id=10)
    await _invoke(
        ConfigCog.config,
        cog,
        denied,
        daily_reply_budget=1,
    )

    async with session_factory() as session:
        guild = await session.get(Guild, 1)
        assert guild is not None
        assert guild.content_rating == "teen"
        assert guild.daily_reply_budget == 200
    denied_message = denied.response.send_message.await_args.args[0]
    assert "Only the configured DM role or server owner" in denied_message


async def test_usage_reports_monthly_provider_tier_totals_and_budget(
    session_factory: SessionFactory,
) -> None:
    async with session_factory.begin() as session:
        guild = Guild(
            discord_guild_id=123,
            name="Test Guild",
            daily_reply_budget=5,
        )
        session.add(guild)
        await session.flush()
        session.add_all(
            [
                UsageLog(
                    guild_id=guild.id,
                    provider="ollama",
                    model="local",
                    tier="dialogue",
                    input_tokens=100,
                    cache_read_tokens=20,
                    output_tokens=30,
                    latency_ms=1000,
                    purpose="dialogue",
                ),
                UsageLog(
                    guild_id=guild.id,
                    provider="ollama",
                    model="local",
                    tier="dialogue",
                    input_tokens=200,
                    cache_read_tokens=0,
                    output_tokens=50,
                    latency_ms=3000,
                    purpose="dialogue",
                ),
                UsageLog(
                    guild_id=guild.id,
                    provider="ollama",
                    model="local",
                    tier="utility",
                    input_tokens=10,
                    cache_read_tokens=0,
                    output_tokens=5,
                    latency_ms=500,
                    purpose="recap",
                ),
            ]
        )

    interaction = _interaction()
    await _invoke(
        ConfigCog.usage,
        ConfigCog(session_factory, _settings()),
        interaction,
    )

    message = interaction.response.send_message.await_args.args[0]
    assert "ollama / dialogue" in message
    assert "2 calls, 400 tokens, 2,000 ms mean latency" in message
    assert "ollama / utility" in message
    assert "3 calls, 415 tokens, 1,500 ms mean latency" in message
    assert "Daily budget remaining** — 2 / 5" in message
    assert "spend" not in message.lower()


async def test_usage_estimates_anthropic_spend(
    session_factory: SessionFactory,
) -> None:
    async with session_factory.begin() as session:
        guild = Guild(discord_guild_id=123, name="Test Guild")
        session.add(guild)
        await session.flush()
        session.add(
            UsageLog(
                guild_id=guild.id,
                provider="anthropic",
                model="claude-sonnet-5",
                tier="dialogue",
                input_tokens=1_000_000,
                cache_read_tokens=1_000_000,
                cache_creation_tokens=1_000_000,
                output_tokens=1_000_000,
                latency_ms=100,
                purpose="dialogue",
                created_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )

    interaction = _interaction()
    await _invoke(
        ConfigCog.usage,
        ConfigCog(session_factory, _settings("anthropic")),
        interaction,
    )

    message = interaction.response.send_message.await_args.args[0]
    # claude-sonnet-5 at $3/$15 per Mtok, one million tokens of each kind:
    #   input        1.00 x 3.00  =  3.00
    #   cache read   1.00 x 0.30  =  0.30   (10% of input)
    #   cache write  1.00 x 3.75  =  3.75   (125% of input)
    #   output       1.00 x 15.00 = 15.00
    #                              = 22.05
    assert "Estimated Anthropic spend** — $22.0500" in message


async def test_usage_prices_by_model_not_tier(
    session_factory: SessionFactory,
) -> None:
    """Tiers are configurable, so pricing must follow the recorded model.

    Pointing ANTHROPIC_MODEL_DIALOGUE at Opus and billing it at Sonnet rates
    understates spend by 67% on every dialogue call.
    """
    async with session_factory.begin() as session:
        guild = Guild(discord_guild_id=123, name="Test Guild")
        session.add(guild)
        await session.flush()
        session.add(
            UsageLog(
                guild_id=guild.id,
                provider="anthropic",
                model="claude-opus-5",  # an Opus model on the dialogue tier
                tier="dialogue",
                input_tokens=1_000_000,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                output_tokens=0,
                latency_ms=100,
                purpose="dialogue",
                created_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )

    interaction = _interaction()
    await _invoke(
        ConfigCog.usage,
        ConfigCog(session_factory, _settings("anthropic")),
        interaction,
    )

    message = interaction.response.send_message.await_args.args[0]
    # Opus 5 input is $5/Mtok. Pricing by tier would have charged Sonnet's $3.
    assert "Estimated Anthropic spend** — $5.0000" in message


async def test_usage_shows_past_spend_after_switching_to_local(
    session_factory: SessionFactory,
) -> None:
    """Money already spent must not vanish when the provider changes.

    The rows stay in usage_log; gating the report on the *current* provider
    hid real spend the moment you flipped LLM_PROVIDER for a comparison.
    """
    async with session_factory.begin() as session:
        guild = Guild(discord_guild_id=123, name="Test Guild")
        session.add(guild)
        await session.flush()
        session.add(
            UsageLog(
                guild_id=guild.id,
                provider="anthropic",
                model="claude-sonnet-5",
                tier="dialogue",
                input_tokens=1_000_000,
                cache_read_tokens=0,
                cache_creation_tokens=0,
                output_tokens=0,
                latency_ms=100,
                purpose="dialogue",
                created_at=datetime.now(UTC).replace(tzinfo=None),
            )
        )

    interaction = _interaction()
    await _invoke(
        ConfigCog.usage,
        ConfigCog(session_factory, _settings("ollama")),  # switched to local
        interaction,
    )

    message = interaction.response.send_message.await_args.args[0]
    assert "Estimated Anthropic spend** — $3.0000" in message
