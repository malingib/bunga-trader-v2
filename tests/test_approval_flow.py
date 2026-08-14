"""Approval-flow tests: approve_signal_by_id / reject_signal_by_id.

Covers the human-confirmation gate that sits in front of the broker.
Mirrors conftest.risk_db_session but builds the FULL schema (ParsedSignal +
TradeLog) because approval runs risk checks that query TradeLog, and uses a
temp SQLite engine so nothing touches the live data/bunga.db.

No broker is connected under test, so approve_signal_by_id leaves a valid
signal in APPROVED (not EXECUTED) — exactly the "wait for human / broker"
state the money-safety rules require.
"""
import os

os.environ.setdefault("TG_API_ID", "1")
os.environ.setdefault("TG_API_HASH", "x")
os.environ.setdefault("TG_PHONE", "+10000000000")
os.environ.setdefault("SIGNAL_CHANNELS", "test")
os.environ.setdefault("GOOGLE_API_KEY", "test")

from contextlib import contextmanager

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core_backend import database as db_module
from core_backend import risk_engine
from core_backend.models import Base, ParsedSignal, SignalStatus, TradeLog
from core_backend.approval_service import (
    approve_signal_by_id,
    reject_signal_by_id,
)
from core_backend.risk_engine import calculate_lot_size, validate_signal_risk


@pytest.fixture
def approval_db(tmp_path, monkeypatch):
    """Temp SQLite with full schema; redirects engine + risk get_db."""
    path = tmp_path / "approval_test.db"
    engine = create_engine(
        f"sqlite:///{path}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)

    # Point both the main engine and risk_engine's get_db at the temp DB.
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
        raw_text="[Strategy] test signal",
        status=SignalStatus.PENDING.value,
        entry_price=2000.0,
        sl=1990.0,  # 10 below entry → valid risk
        tp=2020.0,  # 20 above → R:R 2.0 (>= min 1.0)
    )
    defaults.update(kw)
    return ParsedSignal(**defaults)


async def test_approve_valid_signal_sets_lot_and_approved(approval_db):
    with approval_db() as db:
        sig = _make_signal()
        db.add(sig)
        db.commit()
        db.refresh(sig)
        sig_id = sig.id

        result = await approve_signal_by_id(sig_id, account_balance=10000.0, db=db)

    assert result["status"] == "approved"
    assert result["signal_id"] == sig_id
    # Lot must be sized by risk_engine, not left at default.
    assert result["lot_size"] > 0
    with approval_db() as db:
        refreshed = db.query(ParsedSignal).filter(ParsedSignal.id == sig_id).first()
        assert refreshed.status == SignalStatus.APPROVED.value
        assert refreshed.lot_size == result["lot_size"]


async def test_approve_rejects_bad_rr_ratio(approval_db):
    with approval_db() as db:
        # TP only 2 above entry, SL 10 below → R:R 0.2 < min_rr_ratio (1.0)
        sig = _make_signal(tp=2002.0)
        db.add(sig)
        db.commit()
        db.refresh(sig)
        sig_id = sig.id

        result = await approve_signal_by_id(sig_id, account_balance=10000.0, db=db)

    assert result["status"] == "rejected"
    assert "R:R" in result["reason"]
    with approval_db() as db:
        refreshed = db.query(ParsedSignal).filter(ParsedSignal.id == sig_id).first()
        assert refreshed.status == SignalStatus.REJECTED.value


async def test_approve_rejects_unsupported_symbol(approval_db):
    with approval_db() as db:
        sig = _make_signal(symbol="NOTREAL")
        db.add(sig)
        db.commit()
        db.refresh(sig)
        sig_id = sig.id

        result = await approve_signal_by_id(sig_id, account_balance=10000.0, db=db)

    assert result["status"] == "rejected"
    assert "Unsupported" in result["reason"]


async def test_approve_missing_signal_raises(approval_db):
    from fastapi import HTTPException

    with approval_db() as db:
        with pytest.raises(HTTPException) as exc:
            await approve_signal_by_id(99999, account_balance=10000.0, db=db)
    assert exc.value.status_code == 404


async def test_approve_already_processed_raises(approval_db):
    from fastapi import HTTPException

    with approval_db() as db:
        sig = _make_signal(status=SignalStatus.APPROVED.value)
        db.add(sig)
        db.commit()
        db.refresh(sig)
        sig_id = sig.id

        with pytest.raises(HTTPException) as exc:
            await approve_signal_by_id(sig_id, account_balance=10000.0, db=db)
    assert exc.value.status_code == 400


def test_reject_signal_marks_rejected(approval_db):
    with approval_db() as db:
        sig = _make_signal()
        db.add(sig)
        db.commit()
        db.refresh(sig)
        sig_id = sig.id

        result = reject_signal_by_id(sig_id, reason="manual review", db=db)

    assert result["status"] == "rejected"
    with approval_db() as db:
        refreshed = db.query(ParsedSignal).filter(ParsedSignal.id == sig_id).first()
        assert refreshed.status == SignalStatus.REJECTED.value


def test_reject_already_processed_raises(approval_db):
    from fastapi import HTTPException

    with approval_db() as db:
        sig = _make_signal(status=SignalStatus.REJECTED.value)
        db.add(sig)
        db.commit()
        db.refresh(sig)
        sig_id = sig.id

        with pytest.raises(HTTPException) as exc:
            reject_signal_by_id(sig_id, reason="x", db=db)
    assert exc.value.status_code == 400


def test_risk_functions_consistent_with_approval(approval_db):
    """Guard rail sanity: validate_signal_risk + calculate_lot_size agree
    with what approve_signal_by_id uses (single source of truth)."""
    sig = _make_signal()
    valid, reason = validate_signal_risk(sig, 10000.0)
    assert valid, reason
    lot, err = calculate_lot_size(
        symbol=sig.symbol,
        entry_price=sig.entry_price,
        sl_price=sig.sl,
        account_balance=10000.0,
    )
    assert err is None and lot > 0
