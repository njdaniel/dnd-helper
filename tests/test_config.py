"""Tests for environment-backed application configuration."""

from typing import Any

import pytest
from pydantic import ValidationError

from bot.config import Settings

ENVIRONMENT_VARIABLES = {
    "DISCORD_TOKEN",
    "DEV_GUILD_ID",
    "LLM_PROVIDER",
    "OLLAMA_HOST",
    "OLLAMA_MODEL_DIALOGUE",
    "OLLAMA_MODEL_UTILITY",
    "OLLAMA_MODEL_EPIC",
    "OLLAMA_KEEP_ALIVE",
    "ANTHROPIC_API_KEY",
    "ANTHROPIC_MODEL_DIALOGUE",
    "ANTHROPIC_MODEL_UTILITY",
    "ANTHROPIC_MODEL_EPIC",
    "DATABASE_URL",
    "LOG_LEVEL",
}


@pytest.fixture(autouse=True)
def clear_config_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests independent from the developer's shell configuration."""
    for name in ENVIRONMENT_VARIABLES:
        monkeypatch.delenv(name, raising=False)


def make_settings(**values: Any) -> Settings:
    """Build settings without reading a developer's local .env file."""
    return Settings(_env_file=None, **values)


def test_reads_every_variable_from_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    environment = {
        "DISCORD_TOKEN": "discord-test-token",
        "DEV_GUILD_ID": "123456789",
        "LLM_PROVIDER": "anthropic",
        "OLLAMA_HOST": "http://ollama.example:11434",
        "OLLAMA_MODEL_DIALOGUE": "dialogue-local",
        "OLLAMA_MODEL_UTILITY": "utility-local",
        "OLLAMA_MODEL_EPIC": "epic-local",
        "OLLAMA_KEEP_ALIVE": "45m",
        "ANTHROPIC_API_KEY": "anthropic-test-key",
        "ANTHROPIC_MODEL_DIALOGUE": "dialogue-api",
        "ANTHROPIC_MODEL_UTILITY": "utility-api",
        "ANTHROPIC_MODEL_EPIC": "epic-api",
        "DATABASE_URL": "sqlite+aiosqlite:///./test.db",
        "LOG_LEVEL": "DEBUG",
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    settings = make_settings()

    assert settings.discord_token.get_secret_value() == "discord-test-token"
    assert settings.dev_guild_id == 123456789
    assert settings.llm_provider == "anthropic"
    assert settings.ollama_host == "http://ollama.example:11434"
    assert settings.ollama_model_dialogue == "dialogue-local"
    assert settings.ollama_model_utility == "utility-local"
    assert settings.ollama_model_epic == "epic-local"
    assert settings.ollama_keep_alive == "45m"
    assert settings.anthropic_api_key is not None
    assert settings.anthropic_api_key.get_secret_value() == "anthropic-test-key"
    assert settings.anthropic_model_dialogue == "dialogue-api"
    assert settings.anthropic_model_utility == "utility-api"
    assert settings.anthropic_model_epic == "epic-api"
    assert settings.database_url == "sqlite+aiosqlite:///./test.db"
    assert settings.log_level == "DEBUG"


def test_ollama_is_default_and_does_not_require_anthropic_key() -> None:
    settings = make_settings(
        DISCORD_TOKEN="discord-test-token",
        DEV_GUILD_ID=123456789,
    )

    assert settings.llm_provider == "ollama"
    assert settings.anthropic_api_key is None


def test_llm_provider_rejects_unknown_value() -> None:
    with pytest.raises(ValidationError) as error:
        make_settings(
            DISCORD_TOKEN="discord-test-token",
            DEV_GUILD_ID=123456789,
            LLM_PROVIDER="unknown",
        )

    assert "LLM_PROVIDER" in str(error.value)


def test_missing_required_config_names_variables() -> None:
    with pytest.raises(ValidationError) as error:
        make_settings()

    message = str(error.value)
    assert "DISCORD_TOKEN" in message
    assert "DEV_GUILD_ID" in message


def test_anthropic_provider_requires_api_key() -> None:
    with pytest.raises(ValidationError) as error:
        make_settings(
            DISCORD_TOKEN="discord-test-token",
            DEV_GUILD_ID=123456789,
            LLM_PROVIDER="anthropic",
        )

    assert "ANTHROPIC_API_KEY" in str(error.value)
