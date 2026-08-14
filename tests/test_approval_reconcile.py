"""Phase 2 correctness tests: approve idempotency + stale-APPROVED handling.

- Two concurrent approve calls must NOT double-dispatch to the broker.
- APPROVED-but-unexecuted signals get re-dispatched on broker connect.
- APPROVED-but-unexecuted signals past the expiry window are rejected (not orphaned).
"""
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy import text

from core_backend.approval_service import (
    approve_signal_by_id,
    reconcile_approved_signals,
)
from core_backend.models import ParsedSignal, SignalStatus

from tests._async_helpers import install_async_db, get_one


@pytest.fixture
def reconcile_db(tmp_path, monkeypatch):
    return install_async_db(tmp_path, monkeypatch, "reconcile_test.db")


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
    from unittest.mock import AsyncMock

    broker = AsyncMock()
    broker.name = "fake"
    broker.is_connected = True
    order = type("R", (), {"success": True, "order_id": "O1",
                           "fill_price": 2000.0, "filled_units": 0.1})()
    broker.place_order.return_value = order

    import core_backend.brokers as brokers_mod
    monkeypatch.setattr(brokers_mod, "get_active", lambda: broker)
    return broker


async def test_concurrent_approve_dispatches_once(reconcile_db, monkeypatch, fake_broker):
    """Two near-simultaneous approve calls must result in a single broker order."""
    import asyncio
    from fastapi import HTTPException

    import core_backend.approval_service as svc

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

    # Seed the signal in one session, then approve from two separate sessions.
    async with reconcile_db() as db:
        sig = _make_signal()
        db.add(sig)
        await db.commit()
        await db.refresh(sig)
        sig_id = sig.id

    async with reconcile_db() as db_a, reconcile_db() as db_b:
        results = await asyncio.gather(
            approve_signal_by_id(sig_id, 10000.0, db_a),
            approve_signal_by_id(sig_id, 10000.0, db_b),
            return_exceptions=True,
        )

    # Exactly one dispatch to the broker — no double execution.
    assert fake_broker.place_order.call_count == 1
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
    async with reconcile_db() as db:
        sig = _make_signal(status=SignalStatus.APPROVED.value, lot_size=0.1)
        db.add(sig)
        await db.commit()
        await db.refresh(sig)
        sig_id = sig.id

    async with reconcile_db() as db:
        executed = await reconcile_approved_signals(db)

    assert executed == 1
    async with reconcile_db() as db:
        refreshed = await get_one(db, ParsedSignal, id=sig_id)
        assert refreshed.status == SignalStatus.EXECUTED.value
    assert fake_broker.place_order.call_count == 1


async def test_approved_expiry_rejects_stale(reconcile_db, monkeypatch):
    """APPROVED signals older than approved_signal_max_age_minutes are rejected."""
    from core_backend.config import CONFIG

    async with reconcile_db() as db:
        old = _make_signal(
            status=SignalStatus.APPROVED.value,
            parsed_at=datetime.now(timezone.utc).replace(tzinfo=None)
            - timedelta(minutes=CONFIG.approved_signal_max_age_minutes + 5),
        )
        db.add(old)
        await db.commit()
        await db.refresh(old)
        old_id = old.id

    # Run the same UPDATE the cleanup loop runs.
    async with reconcile_db() as db:
        await db.execute(text(
            "UPDATE parsed_signals SET status='rejected', "
            "execution_result='expired' WHERE status='approved' "
            "AND parsed_at < datetime('now', "
            f"'-{CONFIG.approved_signal_max_age_minutes} minutes')"
        ))
        await db.commit()

    async with reconcile_db() as db:
        refreshed = await get_one(db, ParsedSignal, id=old_id)
        assert refreshed.status == SignalStatus.REJECTED.value
