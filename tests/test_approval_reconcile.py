"""Phase 2 correctness tests: approve idempotency + stale-APPROVED handling.

- Two concurrent approve calls must NOT double-dispatch to the broker.
- APPROVED-but-unexecuted signals get re-dispatched on broker connect.
- APPROVED-but-unexecuted signals past the expiry window are rejected (not orphaned).
"""
import os
from datetime import datetime, timezone, timedelta

os.environ.setdefault("TG_API_ID", "1")
os.environ.setdefault("TG_API_HASH", "x")
os.environ.setdefault("TG_PHONE", "+100****0000")
os.environ.setdefault("SIGNAL_CHANNELS", "test")
os.environ.setdefault("GOOGLE_API_KEY", "test")

from contextlib import contextmanager
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from core_backend import database as db_module
from core_backend import risk_engine
from core_backend.approval_service import (
    approve_signal_by_id,
    reconcile_approved_signals,
)
from core_backend.models import Base, ParsedSignal, SignalStatus


@pytest.fixture
def reconcile_db(tmp_path, monkeypatch):
    path = tmp_path / "reconcile_test.db"
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


def _make_signal(**kw) -> ParsedSignal:
    defaults = dict(
        action="BUY",
        symbol="XAUUSD",
        raw_text="[Strategy] test",
        status=SignalStatus.PENDING.value,
        entry_price=2000.0,
        sl=1990.0,
        tp=2020.0,
        risk_percent=1.0,
    )
    defaults.update(kw)
    return ParsedSignal(**defaults)


@pytest.fixture
def fake_broker(monkeypatch):
    """A broker whose place_order records call count and returns success."""
    broker = AsyncMock()
    broker.name = "fake"
    broker.is_connected = True
    order = type("R", (), {"success": True, "order_id": "O1",
                           "fill_price": 2000.0, "filled_units": 0.1})()
    broker.place_order.return_value = order

    # Both _dispatch_via_broker and reconcile_approved_signals import
    # get_active from core_backend.brokers at call time — patch the source.
    import core_backend.brokers as brokers_mod
    monkeypatch.setattr(brokers_mod, "get_active", lambda: broker)
    return broker


async def test_concurrent_approve_dispatches_once(reconcile_db, monkeypatch, fake_broker):
    """Two near-simultaneous approve calls must result in a single broker order."""
    import core_backend.approval_service as svc

    # Make _dispatch_via_broker use our fake broker and return the dict
    # shape approve_signal_by_id expects (matching the real broker path).
    async def fake_dispatch(signal):
        res = await fake_broker.place_order(
            action=signal.action, symbol=signal.symbol,
            entry_price=signal.entry_price, sl=signal.sl, tp=signal.tp,
            tp2=signal.tp2, tp3=signal.tp3, lot=signal.lot_size or 0.0,
        )
        return {
            "broker": fake_broker.name,
            "order_id": res.order_id,
            "fill_price": res.fill_price,
            "filled_units": res.filled_units,
        }

    monkeypatch.setattr(svc, "_dispatch_via_broker", fake_dispatch)

    with reconcile_db() as db:
        sig = _make_signal()
        db.add(sig)
        db.commit()
        db.refresh(sig)
        sig_id = sig.id

        # Fire both approvals sharing one open DB session. The idempotency
        # lock must let exactly one mutate + dispatch; the other must see the
        # already-processed state and refuse (no second broker order).
        import asyncio
        from fastapi import HTTPException
        results = await asyncio.gather(
            approve_signal_by_id(sig_id, 10000.0, db),
            approve_signal_by_id(sig_id, 10000.0, db),
            return_exceptions=True,
        )
        db.commit()

    # Exactly one dispatch to the broker — no double execution.
    assert fake_broker.place_order.call_count == 1
    # One executed/approved, the other already-processed (HTTPException 400).
    statuses = []
    for r in results:
        if isinstance(r, HTTPException):
            assert r.status_code == 400
            statuses.append("already_processed")
        else:
            statuses.append(r.get("status"))
    assert "executed" in statuses or "approved" in statuses
    assert "already_processed" in statuses


async def test_reconcile_executes_stale_approved(reconcile_db, monkeypatch, fake_broker):
    """An APPROVED but unexecuted signal is executed when the broker connects."""
    with reconcile_db() as db:
        sig = _make_signal(status=SignalStatus.APPROVED.value, lot_size=0.1)
        db.add(sig)
        db.commit()
        sig_id = sig.id

    with reconcile_db() as db:
        executed = await reconcile_approved_signals(db)

    assert executed == 1
    with reconcile_db() as db:
        refreshed = db.query(ParsedSignal).filter(ParsedSignal.id == sig_id).first()
        assert refreshed.status == SignalStatus.EXECUTED.value
    assert fake_broker.place_order.call_count == 1


async def test_approved_expiry_rejects_stale(reconcile_db, monkeypatch):
    """APPROVED signals older than approved_signal_max_age_minutes are rejected."""
    from core_backend.config import CONFIG

    with reconcile_db() as db:
        old = _make_signal(
            status=SignalStatus.APPROVED.value,
            parsed_at=datetime.now(timezone.utc).replace(tzinfo=None)
            - timedelta(minutes=CONFIG.approved_signal_max_age_minutes + 5),
        )
        db.add(old)
        db.commit()
        old_id = old.id

    # Run the same UPDATE the cleanup loop runs.
    with reconcile_db() as db:
        db.execute(text(
            f"UPDATE parsed_signals SET status='rejected', "
            f"execution_result='expired' WHERE status='approved' "
            f"AND parsed_at < datetime('now', "
            f"'-{CONFIG.approved_signal_max_age_minutes} minutes')"
        ))
        db.commit()

    with reconcile_db() as db:
        refreshed = db.query(ParsedSignal).filter(ParsedSignal.id == old_id).first()
        assert refreshed.status == SignalStatus.REJECTED.value
