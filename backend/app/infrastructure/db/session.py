"""Async SQLAlchemy engine + session maker."""
from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings


def make_engine():
    s = get_settings()
    return create_async_engine(
        s.db.url,
        pool_size=s.db.pool_size,
        max_overflow=s.db.max_overflow,
        echo=s.db.echo,
        pool_pre_ping=True,
    )


def make_session_factory(engine=None) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine or make_engine(), expire_on_commit=False)
