"""Shared test fixtures."""

from collections.abc import AsyncIterator

import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession

from bot.db.models import Base
from bot.db.session import create_engine, create_session_factory


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
