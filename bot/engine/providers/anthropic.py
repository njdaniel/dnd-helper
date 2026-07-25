"""Anthropic implementation of the provider protocol."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import anthropic
from anthropic.types import MessageParam, TextBlockParam, ToolParam

from bot.engine.providers import ProviderError, ProviderResult
from bot.engine.schemas import Message


class AnthropicProvider:
    """Use a forced tool call to obtain schema-conforming output."""

    name = "anthropic"

    def __init__(self, api_key: str) -> None:
        # Engine-level retry policy is authoritative.
        self._client = anthropic.AsyncAnthropic(api_key=api_key, max_retries=0)

    async def complete(
        self,
        *,
        model: str,
        system_blocks: list[str],
        messages: list[Message],
        schema: dict[str, object],
    ) -> ProviderResult:
        system: list[TextBlockParam] = []
        for index, block in enumerate(system_blocks):
            system_block: TextBlockParam = {"type": "text", "text": block}
            if index == len(system_blocks) - 1:
                system_block["cache_control"] = {"type": "ephemeral"}
            system.append(system_block)
        try:
            response = await self._client.messages.create(
                model=model,
                max_tokens=2048,
                system=system,
                messages=cast(list[MessageParam], messages),
                tools=[
                    cast(
                        ToolParam,
                        {
                            "name": "speak_as_npc",
                            "description": "Return the requested structured result.",
                            "input_schema": schema,
                        },
                    )
                ],
                tool_choice={"type": "tool", "name": "speak_as_npc"},
            )
        except anthropic.APIStatusError as error:
            raise ProviderError(
                str(error),
                retryable=error.status_code == 429 or error.status_code >= 500,
            ) from error
        except anthropic.APIConnectionError as error:
            raise ProviderError(str(error), retryable=True) from error

        tool_block = next(
            (block for block in response.content if block.type == "tool_use"), None
        )
        if tool_block is None or not isinstance(tool_block.input, Mapping):
            raise ProviderError("Anthropic did not return the forced tool payload")

        cache_read = getattr(response.usage, "cache_read_input_tokens", 0) or 0
        return ProviderResult(
            payload=dict(tool_block.input),
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            cache_read_tokens=cache_read,
        )
