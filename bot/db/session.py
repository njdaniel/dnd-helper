"""Async database engine and session construction."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

SessionFactory = async_sessionmaker[AsyncSession]


def create_engine(database_url: str, *, echo: bool = False) -> AsyncEngine:
    """Create a non-blocking SQLAlchemy engine."""
    return create_async_engine(database_url, echo=echo)


def create_session_factory(engine: AsyncEngine) -> SessionFactory:
    """Create sessions that retain loaded values after commits."""
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def session_scope(factory: SessionFactory) -> AsyncIterator[AsyncSession]:
    """Yield a transactional session, rolling back if its caller fails."""
    async with factory() as session, session.begin():
        yield session
