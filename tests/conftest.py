"""Shared test fixtures."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Base
from bot.db.session import create_engine, create_session_factory
from bot.engine.providers import ProviderResult
from bot.engine.schemas import Message


class FakeProvider:
    """Deterministic provider used by engine and prompt tests."""

    name = "anthropic"

    def __init__(self) -> None:
        self.calls = 0
        self.failures: list[Exception] = []
        self.payload: object = {
            "line": "The old road remembers.",
            "mood": "wary",
            "memory_notes": ["The party asked about the old road."],
        }

    async def complete(
        self,
        *,
        model: str,
        system_blocks: list[str],
        messages: list[Message],
        schema: dict[str, object],
    ) -> ProviderResult:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return ProviderResult(
            payload=self.payload,
            input_tokens=12,
            output_tokens=7,
            cache_read_tokens=4,
        )


@pytest.fixture
def fake_provider() -> FakeProvider:
    return FakeProvider()


@pytest_asyncio.fixture
async def db_session() -> AsyncIterator[AsyncSession]:
    """Provide a fresh, cheap in-memory database for each test."""
    engine = create_engine(
        "sqlite+aiosqlite://",
        echo=False,
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)

    factory = create_session_factory(engine)
    async with factory() as session:
        yield session
        await session.rollback()

    await engine.dispose()
