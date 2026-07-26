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
    """Requesting the intent is what makes a missing portal toggle *loud*.

    Discord rejects the connection with `PrivilegedIntentsRequired` when the
    code asks for an intent the application is not approved for, and `run()`
    turns that into a log line naming the toggle. Drop this line and the
    failure changes shape entirely: the bot connects fine and delivers empty
    `message.content`, so every trigger rule stops matching with nothing in
    the logs to explain it."""
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


def test_log_formatter_preserves_structured_fields() -> None:
    """A plain format string drops `extra` silently. Three call sites in
    main.py attach operational context that way, and CLAUDE.md requires JSON
    logging — so losing it is both a lost diagnostic and a convention breach."""
    import io
    import json
    import logging

    from bot.main import JsonFormatter

    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    logger = logging.getLogger("test_json_formatter")
    logger.handlers = [handler]
    logger.setLevel(logging.INFO)
    logger.propagate = False

    logger.info("Slash commands synced", extra={"command_count": 7, "guild": "123"})

    payload = json.loads(stream.getvalue())
    assert payload["command_count"] == 7
    assert payload["guild"] == "123"
    assert payload["message"] == "Slash commands synced"
    assert payload["level"] == "INFO"
    # LogRecord internals must not leak into the log line.
    assert not {"args", "msg", "levelno", "pathname"} & set(payload)


async def test_every_command_extension_loads_against_a_real_bot(
    settings: Settings,
) -> None:
    """A cog whose setup() raises takes the whole bot down.

    Extensions are auto-discovered, so `setup_hook` loads all of them and a
    single missing dependency stops the bot from starting at all — not just
    that one command group. `LoreCog` required `bot.session_factory`, which
    `run()` never attached, so the engine it built stayed a local variable and
    every command became unavailable. Tests passed throughout: nothing else
    exercised startup.
    """
    import importlib
    import pkgutil
    from unittest.mock import AsyncMock, MagicMock

    import bot.commands
    from bot.db.models import Base
    from bot.db.session import create_engine, create_session_factory

    engine = create_engine("sqlite+aiosqlite://")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    fake_bot = MagicMock()
    fake_bot.settings = settings
    fake_bot.session_factory = create_session_factory(engine)
    fake_bot.add_cog = AsyncMock()

    modules = [
        name
        for _, name, _ in pkgutil.iter_modules(
            bot.commands.__path__, f"{bot.commands.__name__}."
        )
    ]
    assert modules, "no command extensions discovered"

    for name in modules:
        module = importlib.import_module(name)
        setup = getattr(module, "setup", None)
        if setup is None:
            continue
        await setup(fake_bot)

    assert fake_bot.add_cog.await_count == len(
        [m for m in modules if hasattr(importlib.import_module(m), "setup")]
    )
    await engine.dispose()
