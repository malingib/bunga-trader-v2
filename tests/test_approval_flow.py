"""Approval-flow tests: approve_signal_by_id / reject_signal_by_id.

Covers the human-confirmation gate that sits in front of the broker.
Uses an async temp SQLite so nothing touches the live data/bunga.db.

No broker is connected under test, so approve_signal_by_id leaves a valid
signal in APPROVED (not EXECUTED) — exactly the "wait for human / broker"
state the money-safety rules require.
"""
import pytest
from sqlalchemy import select

from core_backend.models import ParsedSignal, SignalStatus
from core_backend.approval_service import (
    approve_signal_by_id,
    reject_signal_by_id,
)
from core_backend.risk_engine import calculate_lot_size, validate_signal_risk

from tests._async_helpers import install_async_db


@pytest.fixture
def approval_db(tmp_path, monkeypatch):
    return install_async_db(tmp_path, monkeypatch, "approval_test.db")


def _make_signal(**kw) -> ParsedSignal:
    defaults = dict(
        action="BUY",
        symbol="XAUUSD",
        raw_text="[Strategy] test signal",
        status=SignalStatus.PENDING.value,
        entry_price=2000.0,
        sl=1990.0,  # 10 below entry → valid risk
        tp=2020.0,  # 20 above → R:R 2.0 (>= min 1.0)
    )
    defaults.update(kw)
    return ParsedSignal(**defaults)


async def test_approve_valid_signal_sets_lot_and_approved(approval_db):
    async with approval_db() as db:
        sig = _make_signal()
        db.add(sig)
        await db.commit()
        await db.refresh(sig)
        sig_id = sig.id

        result = await approve_signal_by_id(sig_id, account_balance=10000.0, db=db)

    assert result["status"] == "approved"
    assert result["signal_id"] == sig_id
    assert result["lot_size"] > 0
    async with approval_db() as db:
        refreshed = await get_one(db, ParsedSignal, id=sig_id)
        assert refreshed.status == SignalStatus.APPROVED.value
        assert refreshed.lot_size == result["lot_size"]


async def test_approve_rejects_bad_rr_ratio(approval_db):
    async with approval_db() as db:
        sig = _make_signal(tp=2002.0)  # R:R 0.2 < min_rr_ratio (1.0)
        db.add(sig)
        await db.commit()
        await db.refresh(sig)
        sig_id = sig.id

        result = await approve_signal_by_id(sig_id, account_balance=10000.0, db=db)

    assert result["status"] == "rejected"
    assert "R:R" in result["reason"]
    async with approval_db() as db:
        refreshed = await get_one(db, ParsedSignal, id=sig_id)
        assert refreshed.status == SignalStatus.REJECTED.value


async def test_approve_rejects_unsupported_symbol(approval_db):
    async with approval_db() as db:
        sig = _make_signal(symbol="NOTREAL")
        db.add(sig)
        await db.commit()
        await db.refresh(sig)
        sig_id = sig.id

        result = await approve_signal_by_id(sig_id, account_balance=10000.0, db=db)

    assert result["status"] == "rejected"
    assert "Unsupported" in result["reason"]


async def test_approve_missing_signal_raises(approval_db):
    from fastapi import HTTPException

    async with approval_db() as db:
        with pytest.raises(HTTPException) as exc:
            await approve_signal_by_id(99999, account_balance=10000.0, db=db)
    assert exc.value.status_code == 404


async def test_approve_already_processed_raises(approval_db):
    from fastapi import HTTPException

    async with approval_db() as db:
        sig = _make_signal(status=SignalStatus.APPROVED.value)
        db.add(sig)
        await db.commit()
        await db.refresh(sig)
        sig_id = sig.id

        with pytest.raises(HTTPException) as exc:
            await approve_signal_by_id(sig_id, account_balance=10000.0, db=db)
    assert exc.value.status_code == 400


async def test_reject_signal_marks_rejected(approval_db):
    async with approval_db() as db:
        sig = _make_signal()
        db.add(sig)
        await db.commit()
        await db.refresh(sig)
        sig_id = sig.id

        result = await reject_signal_by_id(sig_id, reason="manual review", db=db)

    assert result["status"] == "rejected"
    async with approval_db() as db:
        refreshed = await get_one(db, ParsedSignal, id=sig_id)
        assert refreshed.status == SignalStatus.REJECTED.value


async def test_reject_already_processed_raises(approval_db):
    from fastapi import HTTPException

    async with approval_db() as db:
        sig = _make_signal(status=SignalStatus.REJECTED.value)
        db.add(sig)
        await db.commit()
        await db.refresh(sig)
        sig_id = sig.id

        with pytest.raises(HTTPException) as exc:
            await reject_signal_by_id(sig_id, reason="x", db=db)
    assert exc.value.status_code == 400


async def test_risk_functions_consistent_with_approval(approval_db):
    """Guard rail sanity: validate_signal_risk + calculate_lot_size agree
    with what approve_signal_by_id uses (single source of truth)."""
    sig = _make_signal()
    valid, reason = validate_signal_risk(sig, 10000.0)
    assert valid, reason
    lot, err = await calculate_lot_size(
        symbol=sig.symbol,
        entry_price=sig.entry_price,
        sl_price=sig.sl,
        account_balance=10000.0,
    )
    assert err is None and lot > 0


from tests._async_helpers import get_one  # noqa: E402
