"""The bot must ask for what it needs and shut down cleanly."""

import pkgutil

import discord
import pytest

import bot.commands
from bot.config import Settings
from bot.main import DndHelperBot


@pytest.fixture
def settings() -> Settings:
    return Settings(_env_file=None, DISCORD_TOKEN="not-a-real-token", DEV_GUILD_ID=1)


def test_requests_message_content_intent(settings: Settings) -> None:
    """The failure this guards is silent: without the intent Discord connects
    normally and delivers empty `message.content`, so every trigger rule in the
    router stops matching with nothing in the logs to explain it."""
    client = DndHelperBot(settings)
    assert client.intents.message_content is True


def test_cog_discovery_finds_every_command_module(settings: Settings) -> None:
    """Cogs are auto-loaded, so adding a command file needs no registration
    edit. If discovery silently found nothing, the bot would start clean and
    answer no commands at all."""
    prefix = f"{bot.commands.__name__}."
    discovered = {
        module.name
        for module in pkgutil.iter_modules(bot.commands.__path__, prefix)
        if not module.name.rsplit(".", maxsplit=1)[-1].startswith("_")
    }
    assert discovered, "no command modules discovered"
    assert f"{prefix}meta" in discovered


def test_privileged_intents_error_is_recognised() -> None:
    """run() maps this exception to a message naming the Developer Portal
    toggle. Pin the symbol so a discord.py rename surfaces here rather than as
    an unhandled traceback on someone's first launch."""
    assert issubclass(discord.PrivilegedIntentsRequired, discord.ClientException)


def test_token_is_not_exposed_by_repr(settings: Settings) -> None:
    """Settings holds the token as a SecretStr. Logging the settings object —
    or a traceback rendering it — must not leak the token."""
    assert "not-a-real-token" not in repr(settings)
    assert "not-a-real-token" not in str(settings.discord_token)
