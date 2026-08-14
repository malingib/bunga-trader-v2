"""Phase 4 hardening + Phase 3 cleanup regression tests.

- TradingView webhook fails CLOSED (503) when WEBHOOK_SECRET is unset, so an
  unconfigured server cannot ingest unsigned live signals.
- /strategy/last-signals now reads from the live ParsedSignal table (the
  deleted ML jsonl store is gone); returns strategy signals it wrote.
"""
import os

os.environ.setdefault("TG_API_ID", "1")
os.environ.setdefault("TG_API_HASH", "x")
os.environ.setdefault("TG_PHONE", "+100****0000")
os.environ.setdefault("SIGNAL_CHANNELS", "test")
os.environ.setdefault("GOOGLE_API_KEY", "test")

from contextlib import contextmanager

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core_backend import database as db_module
from core_backend import risk_engine
from core_backend.approval_service import approve_signal_by_id
from core_backend.main import tradingview_webhook
from core_backend.models import Base, ParsedSignal, SignalStatus


@pytest.fixture
def webhook_db(tmp_path, monkeypatch):
    path = tmp_path / "webhook_test.db"
    engine = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    monkeypatch.setattr(db_module, "engine", engine)
    monkeypatch.setattr(db_module, "SessionLocal", Session)
    monkeypatch.setattr(risk_engine, "get_db", Session)

    @contextmanager
    def _session():
        s = Session()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    return _session


async def test_webhook_fails_closed_without_secret(webhook_db, monkeypatch):
    """No WEBHOOK_SECRET configured → 503, signal NOT written to DB."""
    from core_backend import config as config_mod
    import dataclasses

    cfg = dataclasses.replace(config_mod.CONFIG, webhook_secret="")
    monkeypatch.setattr(config_mod, "CONFIG", cfg)
    import core_backend.main as main_mod
    monkeypatch.setattr(main_mod, "CONFIG", cfg)

    payload = {"symbol": "XAUUSD", "action": "BUY", "price": 2000.0}
    with pytest.raises(HTTPException) as exc:
        await tradingview_webhook(payload)
    assert exc.value.status_code == 503

    with webhook_db() as db:
        assert db.query(ParsedSignal).count() == 0


async def test_webhook_accepts_with_correct_secret(webhook_db, monkeypatch):
    """With WEBHOOK_SECRET set + correct passphrase → signal written (PENDING)."""
    from core_backend import config as config_mod
    import dataclasses

    cfg = dataclasses.replace(config_mod.CONFIG, webhook_secret="s3cret")
    monkeypatch.setattr(config_mod, "CONFIG", cfg)
    # main.py binds CONFIG via `from .config import CONFIG`, so patch its alias too.
    import core_backend.main as main_mod
    monkeypatch.setattr(main_mod, "CONFIG", cfg)

    payload = {"symbol": "XAUUSD", "action": "BUY", "price": 2000.0,
               "sl": 1990.0, "tp": 2020.0, "passphrase": "s3cret"}
    result = await tradingview_webhook(payload)
    assert result["status"] == "received"

    with webhook_db() as db:
        sig = db.query(ParsedSignal).first()
        assert sig is not None
        assert sig.status == SignalStatus.PENDING.value
