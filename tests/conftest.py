"""Pytest configuration: env vars and temp SQLite for risk-engine DB checks."""
import os
from contextlib import contextmanager

# Set required env before any core_backend import (config loads at import time).
os.environ.setdefault("TG_API_ID", "1")
os.environ.setdefault("TG_API_HASH", "x")
os.environ.setdefault("TG_PHONE", "+10000000000")
os.environ.setdefault("SIGNAL_CHANNELS", "test")
os.environ.setdefault("GOOGLE_API_KEY", "test")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core_backend.models import Base


@pytest.fixture
def risk_db_session(tmp_path, monkeypatch):
    """Isolated SQLite DB for risk_engine helpers that query TradeLog."""
    db_path = tmp_path / "risk_test.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    @contextmanager
    def _get_db():
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise

    monkeypatch.setattr("core_backend.risk_engine.get_db", _get_db)
    yield session
    session.close()
    engine.dispose()
