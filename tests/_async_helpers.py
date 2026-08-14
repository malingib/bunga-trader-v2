"""Shared async-DB test helpers.

The production code now uses an async SQLAlchemy engine
(`sqlalchemy.ext.asyncio`). These helpers build an isolated temp SQLite DB
with an async engine and redirect the module-level ``engine`` / ``SessionLocal``
so the real ``database.get_db`` (an async context manager) yields sessions on
the temp DB. Every test that touches the DB uses ``async with db():`` and
``await db.execute(select(...))`` — mirroring the production call sites.

This keeps tests fully isolated from the live data/bunga.db.
"""
import os

os.environ.setdefault("TG_API_ID", "1")
os.environ.setdefault("TG_API_HASH", "x")
os.environ.setdefault("TG_PHONE", "+100****0000")
os.environ.setdefault("SIGNAL_CHANNELS", "test")
os.environ.setdefault("GOOGLE_API_KEY", "test")

from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from core_backend import database as db_module
from core_backend import risk_engine
from core_backend.models import Base


def install_async_db(tmp_path, monkeypatch, name: str = "test.db"):
    """Redirect core_backend DB at a temp async SQLite file.

    Returns an async context-manager factory ``db()`` that yields a committed
    AsyncSession, exactly like ``database.get_db``.
    """
    path = tmp_path / name
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{path}",
        connect_args={"check_same_thread": False},
    )
    # Build schema on a transient SYNC engine bound to the same file. Using
    # engine.sync_engine for DDL triggers async IO and fails; a plain sync
    # create_engine is the supported path for one-shot table creation.
    from sqlalchemy import create_engine

    sync_engine = create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()

    SessionLocal = async_sessionmaker(
        bind=engine, class_=AsyncSession, autoflush=False, expire_on_commit=False
    )

    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", SessionLocal)

    @asynccontextmanager
    async def _session() -> AsyncIterator[AsyncSession]:
        async with SessionLocal() as s:
            try:
                yield s
                await s.commit()
            except Exception:
                await s.rollback()
                raise

    return _session


async def get_one(db: AsyncSession, model, **filters):
    """Fetch a single row by equality filters, or None."""
    stmt = select(model)
    for key, value in filters.items():
        stmt = stmt.where(getattr(model, key) == value)
    result = await db.execute(stmt)
    return result.scalar_one_or_none()


async def get_all(db: AsyncSession, model, **filters):
    """Fetch all rows matching equality filters."""
    stmt = select(model)
    for key, value in filters.items():
        stmt = stmt.where(getattr(model, key) == value)
    result = await db.execute(stmt)
    return list(result.scalars().all())
