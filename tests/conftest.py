"""Pytest configuration: env vars and temp SQLite for risk-engine DB checks."""
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

# Make backtests/ importable (shared backtest engine lives there).
sys_path = str(Path(__file__).resolve().parent.parent / "backtests")
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)

# Set required env before any core_backend import (config loads at import time).
os.environ.setdefault("TG_API_ID", "1")
os.environ.setdefault("TG_API_HASH", "x")
os.environ.setdefault("TG_PHONE", "+100****0000")
os.environ.setdefault("SIGNAL_CHANNELS", "test")
os.environ.setdefault("GOOGLE_API_KEY", "test")

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from core_backend import database as db_module
from core_backend.models import Base


@pytest.fixture
def risk_db_session(tmp_path, monkeypatch):
    """Isolated async SQLite DB for risk_engine helpers that query TradeLog."""
    from sqlalchemy.ext.asyncio import create_async_engine

    db_path = tmp_path / "risk_test.db"
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    from sqlalchemy import create_engine as _sync_create

    sync_engine = _sync_create(
        f"sqlite:///{db_path}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(sync_engine)
    sync_engine.dispose()
    SessionLocal = async_sessionmaker(
        bind=engine, class_=AsyncSession, autoflush=False, expire_on_commit=False
    )
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", SessionLocal)

    @asynccontextmanager
    async def _get_db() -> AsyncIterator[AsyncSession]:
        async with SessionLocal() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    monkeypatch.setattr("core_backend.risk_engine.get_db", _get_db)
    yield _get_db
