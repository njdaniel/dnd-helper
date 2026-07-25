"""Provider selection, routing, retries, accounting, and reply budgets."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import perf_counter
from typing import Literal, TypeVar

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.config import Settings
from bot.db import repo
from bot.db.models import Guild, UsageLog
from bot.engine.providers import LLMProvider, ProviderError
from bot.engine.providers.anthropic import AnthropicProvider
from bot.engine.providers.ollama import OllamaProvider
from bot.engine.schemas import Message

Tier = Literal["dialogue", "utility", "epic"]
ResponseT = TypeVar("ResponseT", bound=BaseModel)


class BudgetExceededError(Exception):
    """The guild has consumed its configured reply allowance for today."""


class ProviderConfigurationError(ValueError):
    """The selected provider cannot be constructed at startup."""


class LLMEngine:
    """Guild-bound model gateway used by provider-agnostic engine code."""

    def __init__(
        self,
        *,
        settings: Settings,
        session: AsyncSession,
        guild_id: int,
        provider: LLMProvider | None = None,
        max_retries: int = 3,
        backoff_seconds: float = 0.5,
        sleep: Callable[[float], Awaitable[None] | None] | None = None,
    ) -> None:
        self._settings = settings
        self._session = session
        self._guild_id = guild_id
        self._provider = provider or _provider_from_settings(settings)
        self._max_retries = max_retries
        self._backoff_seconds = backoff_seconds
        self._sleep = sleep

    async def complete(
        self,
        purpose: str,
        system_blocks: list[str],
        messages: list[Message],
        schema: type[ResponseT],
        tier: Tier,
    ) -> ResponseT:
        """Produce one validated completion and record exactly one usage row."""
        await self._guard_budget()
        model = self._model_for(tier)
        started = perf_counter()

        for attempt in range(self._max_retries + 1):
            try:
                result = await self._provider.complete(
                    model=model,
                    system_blocks=system_blocks,
                    messages=messages,
                    schema=schema.model_json_schema(),
                )
                break
            except ProviderError as error:
                if not error.retryable or attempt == self._max_retries:
                    raise
                delay = self._backoff_seconds * (2**attempt)
                if self._sleep is None:
                    await asyncio.sleep(delay)
                else:
                    sleep_result = self._sleep(delay)
                    if sleep_result is not None:
                        await sleep_result

        latency_ms = round((perf_counter() - started) * 1000)
        await repo.create_usage_log(
            self._session,
            self._guild_id,
            provider=self._provider.name,
            model=model,
            tier=tier,
            input_tokens=result.input_tokens,
            cache_read_tokens=result.cache_read_tokens,
            cache_creation_tokens=result.cache_creation_tokens,
            output_tokens=result.output_tokens,
            latency_ms=latency_ms,
            purpose=purpose,
        )
        return schema.model_validate(result.payload)

    async def _guard_budget(self) -> None:
        guild = await self._session.scalar(
            select(Guild).where(Guild.id == self._guild_id)
        )
        if guild is None:
            raise ValueError(f"guild {self._guild_id} does not exist")

        today = datetime.now(UTC).date()
        used = await self._session.scalar(
            select(func.count(UsageLog.id)).where(
                UsageLog.guild_id == self._guild_id,
                func.date(UsageLog.created_at) == today.isoformat(),
            )
        )
        if (used or 0) >= guild.daily_reply_budget:
            raise BudgetExceededError("the spirits are silent (daily budget reached)")

    def _model_for(self, tier: Tier) -> str:
        prefix = self._provider.name.replace("-", "_")
        attribute = f"{prefix}_model_{tier}"
        model = getattr(self._settings, attribute, None)
        if not isinstance(model, str) or not model:
            raise ProviderConfigurationError(
                f"no configured {tier} model for provider {self._provider.name!r}"
            )
        return model


def _provider_from_settings(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "anthropic":
        key = settings.anthropic_api_key
        if key is None:
            raise ProviderConfigurationError("ANTHROPIC_API_KEY is required")
        return AnthropicProvider(key.get_secret_value())
    if settings.llm_provider == "ollama":
        return OllamaProvider(settings.ollama_host, settings.ollama_keep_alive)
    raise ProviderConfigurationError(
        f"unknown LLM_PROVIDER value: {settings.llm_provider!r}"
    )
