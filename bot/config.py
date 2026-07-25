"""Application configuration loaded from environment variables."""

from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

LLMProvider = Literal["ollama", "anthropic"]
LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]


class Settings(BaseSettings):
    """Validated application settings loaded from ``.env`` and the environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,
        extra="ignore",
    )

    discord_token: SecretStr = Field(validation_alias="DISCORD_TOKEN")
    dev_guild_id: int = Field(validation_alias="DEV_GUILD_ID")

    llm_provider: LLMProvider = Field(default="ollama", validation_alias="LLM_PROVIDER")

    ollama_host: str = Field(
        default="http://localhost:11434", validation_alias="OLLAMA_HOST"
    )
    ollama_model_dialogue: str = Field(
        default="qwen3.6:27b", validation_alias="OLLAMA_MODEL_DIALOGUE"
    )
    ollama_model_utility: str = Field(
        default="qwen3.6:27b", validation_alias="OLLAMA_MODEL_UTILITY"
    )
    ollama_model_epic: str = Field(
        default="qwen3.6:27b", validation_alias="OLLAMA_MODEL_EPIC"
    )
    ollama_keep_alive: str = Field(default="30m", validation_alias="OLLAMA_KEEP_ALIVE")

    anthropic_api_key: SecretStr | None = Field(
        default=None, validation_alias="ANTHROPIC_API_KEY"
    )
    anthropic_model_dialogue: str = Field(
        default="claude-sonnet-5", validation_alias="ANTHROPIC_MODEL_DIALOGUE"
    )
    anthropic_model_utility: str = Field(
        default="claude-haiku-4-5", validation_alias="ANTHROPIC_MODEL_UTILITY"
    )
    anthropic_model_epic: str = Field(
        default="claude-opus-5", validation_alias="ANTHROPIC_MODEL_EPIC"
    )

    database_url: str = Field(
        default="sqlite+aiosqlite:///./dnd_helper.db",
        validation_alias="DATABASE_URL",
    )
    log_level: LogLevel = Field(default="INFO", validation_alias="LOG_LEVEL")

    @model_validator(mode="after")
    def require_anthropic_key_for_anthropic(self) -> "Settings":
        """Require metered-provider credentials only when that provider is selected."""
        if self.llm_provider == "anthropic" and self.anthropic_api_key is None:
            raise ValueError(
                "ANTHROPIC_API_KEY is required when LLM_PROVIDER=anthropic"
            )
        return self
