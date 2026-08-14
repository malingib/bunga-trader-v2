"""Dispatch circuit-breaker tests (C2): after N consecutive dispatch failures
the breaker trips, halts execution, and can be manually reset.
"""
import asyncio
import pytest
from unittest.mock import AsyncMock

from fastapi import HTTPException

from core_backend.approval_service import (
    approve_signal_by_id,
    dispatch_circuit_open,
    record_dispatch_failure,
    reset_dispatch_circuit,
)
from core_backend.models import ParsedSignal, SignalStatus

from tests._async_helpers import install_async_db, get_one


@pytest.fixture
def circuit_db(tmp_path, monkeypatch):
    return install_async_db(tmp_path, monkeypatch, "circuit_test.db")


def _make_signal(**kw) -> ParsedSignal:
    defaults = dict(
        action="BUY", symbol="XAUUSD", raw_text="[Strategy] t",
        status=SignalStatus.PENDING.value, entry_price=2000.0,
        sl=1990.0, tp=2020.0, risk_percent=1.0,
    )
    defaults.update(kw)
    return ParsedSignal(**defaults)


@pytest.fixture(autouse=True)
def _reset_breaker():
    reset_dispatch_circuit()
    yield
    reset_dispatch_circuit()


async def test_breaker_trips_after_consecutive_failures(circuit_db, monkeypatch):
    from core_backend import approval_service as svc

    async def fake_dispatch(signal):
        return {"broker": "fake", "error": "broker down"}

    monkeypatch.setattr(svc, "_dispatch_via_broker", fake_dispatch)
    monkeypatch.setattr(svc, "DISPATCH_CIRCUIT_MAX_FAILURES", 3)

    # Three failed broker dispatches should trip the breaker.
    for _ in range(3):
        record_dispatch_failure()
    assert dispatch_circuit_open() is True


async def test_breaker_halts_approval_when_open(circuit_db, monkeypatch):
    from core_backend import approval_service as svc

    # Force the breaker open via the real trip path. The real
    # _dispatch_via_broker checks dispatch_circuit_open() and returns an
    # error without touching the broker.
    for _ in range(svc.DISPATCH_CIRCUIT_MAX_FAILURES):
        record_dispatch_failure()
    assert dispatch_circuit_open() is True

    async with circuit_db() as db:
        sig = _make_signal()
        db.add(sig)
        await db.commit()
        await db.refresh(sig)
        sig_id = sig.id

        # Approve → dispatch returns "circuit open" error → 503, signal stays APPROVED.
        with pytest.raises(HTTPException) as exc:
            await approve_signal_by_id(sig_id, 10000.0, db)
        assert exc.value.status_code == 503

    async with circuit_db() as db:
        refreshed = await get_one(db, ParsedSignal, id=sig_id)
        # Safety invariant: the trade was NOT executed while halted.
        assert refreshed.status != SignalStatus.EXECUTED.value


async def test_breaker_resets(circuit_db, monkeypatch):
    record_dispatch_failure()
    record_dispatch_failure()
    record_dispatch_failure()
    assert dispatch_circuit_open() is True
    reset_dispatch_circuit()
    assert dispatch_circuit_open() is False
