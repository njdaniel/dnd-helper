"""Ollama implementation of the provider protocol."""

from __future__ import annotations

import json
from collections.abc import Mapping

import ollama

from bot.engine.providers import ProviderError, ProviderResult
from bot.engine.schemas import Message


class OllamaProvider:
    """Use Ollama's constrained decoder to obtain schema-conforming output."""

    name = "ollama"

    def __init__(self, host: str, keep_alive: str) -> None:
        self._client = ollama.AsyncClient(host=host)
        self._keep_alive = keep_alive

    async def complete(
        self,
        *,
        model: str,
        system_blocks: list[str],
        messages: list[Message],
        schema: dict[str, object],
    ) -> ProviderResult:
        ollama_messages: list[Mapping[str, str]] = [
            {"role": "system", "content": block} for block in system_blocks
        ]
        ollama_messages.extend(
            {"role": message["role"], "content": message["content"]}
            for message in messages
        )

        try:
            response = await self._client.chat(
                model=model,
                messages=ollama_messages,
                format=schema,
                keep_alive=self._keep_alive,
            )
        except ollama.RequestError as error:
            raise self._friendly_error(model, retryable=True) from error
        except ollama.ResponseError as error:
            retryable = error.status_code >= 500
            raise self._friendly_error(model, retryable=retryable) from error

        content = response.message.content
        if content is None:
            raise ProviderError("Ollama returned an empty response")
        try:
            payload = json.loads(content)
        except (json.JSONDecodeError, TypeError) as error:
            raise ProviderError(
                "Ollama returned invalid JSON despite constrained decoding"
            ) from error
        if not isinstance(payload, Mapping):
            raise ProviderError("Ollama returned JSON that is not an object")

        return ProviderResult(
            payload=dict(payload),
            input_tokens=response.prompt_eval_count or 0,
            output_tokens=response.eval_count or 0,
        )

    @staticmethod
    def _friendly_error(model: str, *, retryable: bool) -> ProviderError:
        return ProviderError(
            f"Could not use Ollama model {model!r}. Make sure Ollama is running and "
            f"the model is installed with `ollama pull {model}`.",
            retryable=retryable,
        )
