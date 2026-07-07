"""Approval flow tests for web and mobile routes."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from core_backend.approval_service import approve_signal_by_id
from core_backend.mobile_api import routes as mobile_routes
from core_backend.models import Base, ParsedSignal, RawSignal, SignalStatus
from core_backend.risk_engine import validate_signal_risk


@pytest.fixture
def approval_db_session(tmp_path):
    db_path = tmp_path / "approval_test.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()

    yield session
    session.close()
    engine.dispose()


def _create_signal(db, **kwargs) -> ParsedSignal:
    raw = RawSignal(
        channel_id="test",
        message_id=1,
        text="BUY GOLD 2350.00 SL 2335.00 TP 2380.00",
        processed=1,
    )
    db.add(raw)
    db.flush()
    defaults = {
        "raw_signal_id": raw.id,
        "action": "BUY",
        "symbol": "XAUUSD",
        "raw_text": raw.text,
        "status": SignalStatus.PENDING.value,
    }
    defaults.update(kwargs)
    signal = ParsedSignal(**defaults)
    db.add(signal)
    db.commit()
    db.refresh(signal)
    return signal


def test_pending_order_without_entry_is_rejected(risk_db_session):
    signal = ParsedSignal(
        raw_signal_id=1,
        action="BUY_LIMIT",
        symbol="XAUUSD",
        entry_price=None,
        sl=1.0950,
        tp=1.1100,
        raw_text="BUY LIMIT GOLD SL 2335.00 TP 2380.00",
    )

    valid, reason = validate_signal_risk(signal, 10_000.0)

    assert valid is False
    assert reason is not None
    assert "entry price" in reason.lower()


def test_approve_rolls_back_when_no_bridge_receives_trade(approval_db_session, monkeypatch):
    signal = _create_signal(
        approval_db_session,
        action="BUY",
        symbol="XAUUSD",
        entry_price=2350.0,
        sl=2335.0,
        tp=2380.0,
    )

    async def fake_get_status():
        return {"connected_bridges": 1, "status": "healthy"}

    async def fake_broadcast(_trade_payload):
        return {"sent_count": 0, "failed_count": 1, "total": 1}

    monkeypatch.setattr("core_backend.approval_service.manager.get_status", fake_get_status)
    monkeypatch.setattr("core_backend.approval_service.manager.broadcast_trade", fake_broadcast)

    with pytest.raises(HTTPException) as excinfo:
        asyncio.run(approve_signal_by_id(signal.id, None, approval_db_session))

    assert excinfo.value.status_code == 503

    refreshed = approval_db_session.query(ParsedSignal).filter(ParsedSignal.id == signal.id).first()
    assert refreshed is not None
    assert refreshed.status == SignalStatus.PENDING.value
    assert refreshed.lot_size is None
    assert refreshed.execution_result is None


def test_mobile_routes_delegate_to_shared_approval_helpers(approval_db_session, monkeypatch):
    signal = _create_signal(
        approval_db_session,
        action="BUY",
        symbol="XAUUSD",
        entry_price=2350.0,
        sl=2335.0,
        tp=2380.0,
    )

    async def fake_approve(signal_id, account_balance, db):
        return {"status": "approved", "signal_id": signal_id, "lot_size": 0.1}

    def fake_reject(signal_id, reason, db):
        return {"status": "rejected", "signal_id": signal_id}

    monkeypatch.setattr(mobile_routes, "approve_signal_by_id", fake_approve)
    monkeypatch.setattr(mobile_routes, "reject_signal_by_id", fake_reject)

    approve_result = asyncio.run(mobile_routes.mobile_approve_signal(signal.id, None, approval_db_session, None))
    reject_result = mobile_routes.mobile_reject_signal(signal.id, "manual", approval_db_session, None)

    assert approve_result["status"] == "approved"
    assert approve_result["signal_id"] == signal.id
    assert reject_result["status"] == "rejected"
    assert reject_result["signal_id"] == signal.id


def test_non_gold_signal_is_rejected_before_dispatch(approval_db_session, monkeypatch):
    signal = _create_signal(
        approval_db_session,
        action="BUY",
        symbol="USDJPY",
        entry_price=150.00,
        sl=149.50,
        tp=151.00,
        raw_text="BUY USDJPY 150.00 SL 149.50 TP 151.00",
    )

    async def fake_get_status():
        return {"connected_bridges": 1, "status": "healthy"}

    async def fake_broadcast(_trade_payload):
        raise AssertionError("Broadcast should not be called for unsupported symbols")

    monkeypatch.setattr("core_backend.approval_service.manager.get_status", fake_get_status)
    monkeypatch.setattr("core_backend.approval_service.manager.broadcast_trade", fake_broadcast)

    result = asyncio.run(approve_signal_by_id(signal.id, None, approval_db_session))

    assert result["status"] == "rejected"
    assert "Unsupported symbol" in result["reason"]
    refreshed = approval_db_session.query(ParsedSignal).filter(ParsedSignal.id == signal.id).first()
    assert refreshed is not None
    assert refreshed.status == SignalStatus.REJECTED.value

def test_expired_signal_is_rejected_before_dispatch(approval_db_session, monkeypatch):
    signal = _create_signal(
        approval_db_session,
        action="BUY",
        symbol="XAUUSD",
        entry_price=2350.0,
        sl=2335.0,
        tp=2380.0,
        parsed_at=datetime.utcnow() - timedelta(days=1),
    )

    async def fake_get_status():
        return {"connected_bridges": 1, "status": "healthy"}

    async def fake_broadcast(_trade_payload):
        raise AssertionError("Broadcast should not be called for expired signals")

    monkeypatch.setattr("core_backend.approval_service.manager.get_status", fake_get_status)
    monkeypatch.setattr("core_backend.approval_service.manager.broadcast_trade", fake_broadcast)

    result = asyncio.run(approve_signal_by_id(signal.id, None, approval_db_session))

    assert result["status"] == "rejected"
    assert "Expiry" in result["reason"]
    refreshed = approval_db_session.query(ParsedSignal).filter(ParsedSignal.id == signal.id).first()
    assert refreshed is not None
    assert refreshed.status == SignalStatus.REJECTED.value
