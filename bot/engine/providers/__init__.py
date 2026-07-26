"""Provider-neutral language model types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from bot.engine.schemas import Message


@dataclass(frozen=True, slots=True)
class ProviderResult:
    """A structured completion plus the provider's accounting metadata."""

    payload: object
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0


class ProviderError(Exception):
    """A normalized provider failure that the engine can retry safely."""

    def __init__(self, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.retryable = retryable


class LLMProvider(Protocol):
    """Contract implemented by every inference backend."""

    name: str

    async def complete(
        self,
        *,
        model: str,
        system_blocks: list[str],
        messages: list[Message],
        schema: dict[str, object],
    ) -> ProviderResult:
        """Return a schema-shaped payload and usage metadata."""
