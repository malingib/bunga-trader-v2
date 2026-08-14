"""Strategy poller must NOT auto-approve/auto-execute (AGENTS.md money rule).

Strategy signals are written to the DB as PENDING and wait for the human
approval gate in the dashboard. No broker order may be placed by the poller.
"""
import os

os.environ.setdefault("TG_API_ID", "1")
os.environ.setdefault("TG_API_HASH", "x")
os.environ.setdefault("TG_PHONE", "+100****0000")
os.environ.setdefault("SIGNAL_CHANNELS", "test")
os.environ.setdefault("GOOGLE_API_KEY", "test")

from contextlib import contextmanager
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core_backend import database as db_module
from core_backend import risk_engine
from core_backend.models import Base, ParsedSignal, SignalStatus
from core_backend.sources import strategy_source


@pytest.fixture
def strategy_db(tmp_path, monkeypatch):
    path = tmp_path / "strategy_test.db"
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


class _FakeSignal:
    """Minimal stand-in for strategies.engine.StrategySignal."""

    def __init__(self, action="BUY", symbol="XAUUSD", entry_price=2000.0,
                 sl=1990.0, tp=2020.0, tp2=None, tp3=None,
                 quality_score=80.0, signal_source="envelope"):
        self.action = action
        self.symbol = symbol
        self.entry_price = entry_price
        self.sl = sl
        self.tp = tp
        self.tp2 = tp2
        self.tp3 = tp3
        self.quality_score = quality_score
        self.signal_source = signal_source
        self.generated_at = datetime.now(timezone.utc).replace(tzinfo=None)


def test_poller_leaves_signal_pending_and_never_dispatches(strategy_db, monkeypatch):
    """Strategy poll must queue PENDING and never call the broker."""
    fake_engine = SimpleNamespace(run_poll=lambda: [_FakeSignal()])
    monkeypatch.setattr(strategy_source, "QuadaptEngine", lambda: fake_engine)

    poller = strategy_source.StrategyPoller()
    # One poll cycle used to auto-approve + dispatch. It must now only queue.
    asyncio.run(poller.poll_once())

    with strategy_db() as db:
        persisted = db.query(ParsedSignal).order_by(ParsedSignal.id.desc()).first()
        assert persisted is not None
        assert persisted.status == SignalStatus.PENDING.value, (
            "strategy signal must remain PENDING for human approval"
        )


def test_poller_does_not_auto_approve_existing_pending(strategy_db, monkeypatch):
    """A PENDING strategy signal must still be PENDING after a poll cycle
    (i.e. the poller does not flip it to APPROVED/EXECUTED)."""
    fake_engine = SimpleNamespace(run_poll=lambda: [])
    monkeypatch.setattr(strategy_source, "QuadaptEngine", lambda: fake_engine)

    with strategy_db() as db:
        sig = ParsedSignal(
            action="SELL", symbol="SP500", raw_text="[Strategy] t",
            status=SignalStatus.PENDING.value, entry_price=5000.0,
            sl=5050.0, tp=4950.0, risk_percent=1.0,
        )
        db.add(sig)
        db.commit()
        sig_id = sig.id

    poller = strategy_source.StrategyPoller()
    asyncio.run(poller.poll_once())

    with strategy_db() as db:
        refreshed = db.query(ParsedSignal).filter(ParsedSignal.id == sig_id).first()
        assert refreshed.status == SignalStatus.PENDING.value


import asyncio  # noqa: E402  (imported after use-sites for clarity; resolved at call time)
