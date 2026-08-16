"""Position reconciliation tests (A): detects closed broker positions and
finalizes the matching TradeLog with realized P&L, without placing orders.
"""
import asyncio
from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from core_backend.trading.reconcile import reconcile_positions, get_open_positions
from core_backend.models import ParsedSignal, SignalStatus, TradeLog
from core_backend.brokers.base import PositionInfo

from tests._async_helpers import install_async_db, get_one


@pytest.fixture
def rec_db(tmp_path, monkeypatch):
    return install_async_db(tmp_path, monkeypatch, "reconcile_test.db")


def _make_signal(symbol="XAUUSD", action="BUY", entry=2000.0, sl=1990.0, tp=2020.0) -> ParsedSignal:
    return ParsedSignal(
        action=action, symbol=symbol, entry_price=entry, sl=sl, tp=tp,
        raw_text=f"[Strategy] {action} {symbol}", status=SignalStatus.EXECUTED.value,
        lot_size=0.1, risk_percent=1.0, parsed_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )


def _make_trade(symbol="XAUUSD", action="BUY", lot=0.1, entry=2000.0) -> TradeLog:
    return TradeLog(
        parsed_signal_id=1, symbol=symbol, action=action, lot_size=lot,
        entry_price=entry, sl=1990.0, tp=2020.0, result="executed", pnl=None,
        executed_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )


class _FakeBroker:
    def __init__(self, positions):
        self._positions = positions
        self.name = "fake"
        self.is_connected = True

    async def get_positions(self):
        return list(self._positions)


@pytest.fixture(autouse=True)
def _patch_broker(monkeypatch):
    """Intercept get_active() used inside reconcile_positions."""
    from core_backend.trading import reconcile as rec_mod
    state = {"broker": None}

    def _getter():
        return state["broker"]

    monkeypatch.setattr(rec_mod, "get_active", _getter)
    return state


async def _seed(rec_db, sig, trade):
    async with rec_db() as db:
        db.add(sig)
        await db.commit()
        await db.refresh(sig)
        trade.parsed_signal_id = sig.id
        db.add(trade)
        await db.commit()
        return sig.id, trade.id


async def test_no_broker_returns_note(rec_db):
    # _patch_broker leaves state["broker"] = None → no active broker.
    result = await reconcile_positions()
    assert result["checked"] == 0
    assert "no broker" in result["note"]


async def test_open_position_keeps_trade_open(rec_db, _patch_broker):
    sig = _make_signal()
    trade = _make_trade()
    _, trade_id = await _seed(rec_db, sig, trade)

    _patch_broker["broker"] = _FakeBroker([
        PositionInfo(symbol="XAUUSD", side="BUY", size=0.1, entry_price=2000.0,
                     current_price=2010.0, pnl=100.0, broker_position_id="XAUUSD"),
    ])
    result = await reconcile_positions()
    assert result["checked"] == 1
    assert result["open"] == 1
    assert result["closed_now"] == 0

    async with rec_db() as db:
        t = await get_one(db, TradeLog, id=trade_id)
        assert t.closed_at is None  # still open, untouched


async def test_closed_position_finalizes_pnl(rec_db, _patch_broker):
    sig = _make_signal()
    trade = _make_trade()
    _, trade_id = await _seed(rec_db, sig, trade)

    # Position gone → reconciler should finalize with realized P&L.
    _patch_broker["broker"] = _FakeBroker([])
    result = await reconcile_positions()
    assert result["checked"] == 1
    assert result["closed_now"] == 1

    async with rec_db() as db:
        t = await get_one(db, TradeLog, id=trade_id)
        assert t.closed_at is not None
        # P&L computed from entry→last-seen price. Broker gone, so exit=entry
        # fallback → pnl≈0 → breakeven (no crash, sane default).
        assert t.result == "breakeven"
        assert t.pnl is not None


async def test_manual_pnl_not_overwritten(rec_db, _patch_broker):
    sig = _make_signal()
    trade = _make_trade()
    trade.pnl = 123.45
    trade.result = "win"
    _, trade_id = await _seed(rec_db, sig, trade)

    # Position gone, but human already set P&L via /trades/feedback.
    _patch_broker["broker"] = _FakeBroker([])
    await reconcile_positions()

    async with rec_db() as db:
        t = await get_one(db, TradeLog, id=trade_id)
        assert t.pnl == 123.45  # preserved
        assert t.result == "win"
        assert t.closed_at is not None  # close time still stamped


async def test_get_open_positions_reads_broker(rec_db, _patch_broker):
    _patch_broker["broker"] = _FakeBroker([
        PositionInfo(symbol="SP500", side="SELL", size=1.0, entry_price=5000.0,
                     current_price=4950.0, pnl=-50.0, broker_position_id="SP500"),
    ])
    positions = await get_open_positions()
    assert len(positions) == 1
    assert positions[0]["symbol"] == "SP500"
    assert positions[0]["side"] == "SELL"
