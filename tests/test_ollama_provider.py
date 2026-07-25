"""Tests for the Ollama model provider."""

from __future__ import annotations

import os
from unittest.mock import AsyncMock

import ollama
import pytest

from bot.engine.providers import LLMProvider, ProviderError
from bot.engine.providers.ollama import OllamaProvider
from bot.engine.schemas import NpcReply

SCHEMA = NpcReply.model_json_schema()


def response(
    content: str = '{"line":"The old road remembers."}',
    *,
    input_tokens: int = 12,
    output_tokens: int = 7,
) -> ollama.ChatResponse:
    return ollama.ChatResponse(
        message={"role": "assistant", "content": content},
        prompt_eval_count=input_tokens,
        eval_count=output_tokens,
    )


async def test_passes_schema_and_keep_alive_to_chat() -> None:
    provider = OllamaProvider("http://ollama.example:11434", "45m")
    provider._client.chat = AsyncMock(return_value=response())

    result = await provider.complete(
        model="local-dialogue",
        system_blocks=["static instructions", "dynamic scene"],
        messages=[{"role": "user", "content": "Who goes there?"}],
        schema=SCHEMA,
    )

    provider._client.chat.assert_awaited_once_with(
        model="local-dialogue",
        messages=[
            {"role": "system", "content": "static instructions"},
            {"role": "system", "content": "dynamic scene"},
            {"role": "user", "content": "Who goes there?"},
        ],
        format=SCHEMA,
        keep_alive="45m",
    )
    assert result.payload == {"line": "The old road remembers."}
    assert (result.input_tokens, result.output_tokens, result.cache_read_tokens) == (
        12,
        7,
        0,
    )


async def test_satisfies_provider_protocol() -> None:
    provider: LLMProvider = OllamaProvider("http://localhost:11434", "30m")
    provider._client.chat = AsyncMock(return_value=response())

    result = await provider.complete(
        model="local-dialogue",
        system_blocks=[],
        messages=[],
        schema=SCHEMA,
    )

    assert result.payload == {"line": "The old road remembers."}


@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (ollama.RequestError("connection refused"), True),
        (ollama.ResponseError("model not found", 404), False),
        (ollama.ResponseError("server error", 500), True),
    ],
)
async def test_connection_and_model_errors_are_actionable(
    error: Exception, retryable: bool
) -> None:
    provider = OllamaProvider("http://localhost:11434", "30m")
    provider._client.chat = AsyncMock(side_effect=error)

    with pytest.raises(
        ProviderError,
        match=r"local-dialogue.*ollama pull local-dialogue",
    ) as raised:
        await provider.complete(
            model="local-dialogue",
            system_blocks=[],
            messages=[],
            schema=SCHEMA,
        )

    assert raised.value.retryable is retryable


async def test_rejects_non_object_json() -> None:
    provider = OllamaProvider("http://localhost:11434", "30m")
    provider._client.chat = AsyncMock(return_value=response('["not", "an", "object"]'))

    with pytest.raises(ProviderError, match="not an object"):
        await provider.complete(
            model="local-dialogue",
            system_blocks=[],
            messages=[],
            schema=SCHEMA,
        )


@pytest.mark.live
@pytest.mark.skipif(
    os.environ.get("OLLAMA_LIVE_TEST") != "1",
    reason="set OLLAMA_LIVE_TEST=1 to exercise a local Ollama model",
)
async def test_live_speak_as_npc_conforms_ten_out_of_ten() -> None:
    model = os.environ.get("OLLAMA_MODEL_DIALOGUE", "qwen3.6:27b")
    provider = OllamaProvider(
        os.environ.get("OLLAMA_HOST", "http://localhost:11434"),
        os.environ.get("OLLAMA_KEEP_ALIVE", "30m"),
    )
    system_blocks = [
        "You are a game master portraying an NPC. Stay in character. Never speak "
        "for player characters, invent mechanics, or resolve dice; defer those "
        "decisions to the DM. Respond in one to three short paragraphs.",
        "Portray Mira, a guarded old-road innkeeper who speaks plainly.",
    ]

    for _ in range(10):
        result = await provider.complete(
            model=model,
            system_blocks=system_blocks,
            messages=[
                {
                    "role": "user",
                    "content": "Traveler: Mira, what happened on the old road?",
                }
            ],
            schema=SCHEMA,
        )
        reply = NpcReply.model_validate(result.payload)
        assert reply.line
