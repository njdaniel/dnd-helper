"""Discord bot entrypoint and application lifecycle."""

from __future__ import annotations

import asyncio
import json
import logging
import pkgutil
import signal
from collections.abc import Callable

import discord
from discord.ext import commands
from sqlalchemy.ext.asyncio import AsyncEngine

import bot.commands
from bot.commands.meta import configured_model
from bot.config import Settings
from bot.db.session import SessionFactory, create_engine, create_session_factory

LOGGER = logging.getLogger(__name__)


class DndHelperBot(commands.Bot):
    """Discord client with automatic command discovery and synchronization."""

    def __init__(
        self, settings: Settings, session_factory: SessionFactory | None = None
    ) -> None:
        intents = discord.Intents.default()
        # Without this, message.content arrives empty and every trigger rule
        # fails silently. Requesting it here is only half the job — it must
        # also be enabled in the Developer Portal, and Discord rejects the
        # connection if it is not. That rejection is handled in run().
        intents.message_content = True

        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.settings = settings
        # Extensions are auto-discovered, so a cog that needs a session factory
        # has no other way to reach one. Attaching it here rather than letting
        # each cog build its own engine keeps a single connection pool — and a
        # cog whose setup() raises takes the whole bot down with it, because
        # setup_hook never completes.
        self.session_factory = session_factory

    async def setup_hook(self) -> None:
        """Load every command extension, then publish its slash commands."""
        await self._load_command_extensions()

        dev_guild_id = getattr(self.settings, "dev_guild_id", None)
        if dev_guild_id:
            guild = discord.Object(id=dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            sync_target = str(dev_guild_id)
        else:
            synced = await self.tree.sync()
            sync_target = "global"

        LOGGER.info(
            "Slash commands synced",
            extra={"command_count": len(synced), "guild": sync_target},
        )

    async def _load_command_extensions(self) -> None:
        prefix = f"{bot.commands.__name__}."
        modules = sorted(
            module.name
            for module in pkgutil.iter_modules(bot.commands.__path__, prefix)
            if not module.name.rsplit(".", maxsplit=1)[-1].startswith("_")
        )
        for module in modules:
            await self.load_extension(module)
            LOGGER.info("Command cog loaded", extra={"extension": module})


_RESERVED_LOG_FIELDS = frozenset(
    set(vars(logging.LogRecord("", 0, "", 0, "", None, None)))
    | {"taskName", "message", "asctime"}
)


class JsonFormatter(logging.Formatter):
    """Render records as one JSON object per line, keeping `extra` fields.

    A plain format string drops everything passed through `extra` without
    warning, so context like the synced command count or the shutdown signal
    would be logged as if it had never been attached.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Anything the caller attached via extra= lands in __dict__ alongside
        # LogRecord's own attributes; the difference is what identifies it.
        for key, value in record.__dict__.items():
            if key not in _RESERVED_LOG_FIELDS and not key.startswith("_"):
                payload[key] = value
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def _configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    logging.basicConfig(level=level, handlers=[handler], force=True)


def _install_signal_handlers(
    loop: asyncio.AbstractEventLoop, close: Callable[[], object]
) -> None:
    def request_shutdown(signal_name: str) -> None:
        LOGGER.info("Shutdown requested", extra={"signal": signal_name})
        result = close()
        if asyncio.iscoroutine(result):
            asyncio.create_task(result)

    def request_shutdown_from_signal(signum: int, _frame: object) -> None:
        loop.call_soon_threadsafe(request_shutdown, signal.Signals(signum).name)

    for shutdown_signal in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(
                shutdown_signal, request_shutdown, shutdown_signal.name
            )
        except NotImplementedError:
            signal.signal(shutdown_signal, request_shutdown_from_signal)


async def run(settings: Settings) -> None:
    """Run Discord until shutdown, then release all external resources."""
    engine: AsyncEngine = create_engine(settings.database_url)
    client = DndHelperBot(settings, create_session_factory(engine))
    _install_signal_handlers(asyncio.get_running_loop(), client.close)

    LOGGER.info(
        "Starting bot: provider=%s model=%s guild=%s",
        settings.llm_provider,
        configured_model(settings),
        getattr(settings, "dev_guild_id", None) or "global",
    )

    try:
        await client.start(settings.discord_token.get_secret_value())
    except discord.PrivilegedIntentsRequired:
        LOGGER.critical(
            "Discord rejected Message Content Intent; enable it under "
            "Developer Portal > Bot > Privileged Gateway Intents"
        )
        raise
    finally:
        if not client.is_closed():
            await client.close()
        await engine.dispose()
        await engine.dispose()
        LOGGER.info("Discord client and database engine closed")


def main() -> None:
    """Load configuration and start the bot."""
    settings = Settings()  # type: ignore[call-arg]  # Values come from the environment.
    _configure_logging(settings.log_level)
    asyncio.run(run(settings))


if __name__ == "__main__":
    main()
