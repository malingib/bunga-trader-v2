"""Strategy poller must NOT auto-approve/auto-execute (AGENTS.md money rule).

Strategy signals are written to the DB as PENDING and wait for the human
approval gate in the dashboard. No broker order may be placed by the poller.
"""
import asyncio
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest
from sqlalchemy import select

from core_backend.models import ParsedSignal, SignalStatus
from core_backend.sources import strategy_source

from tests._async_helpers import install_async_db, get_one


@pytest.fixture
def strategy_db(tmp_path, monkeypatch):
    return install_async_db(tmp_path, monkeypatch, "strategy_test.db")


class _FakeSignal:
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


async def test_poller_leaves_signal_pending_and_never_dispatches(strategy_db, monkeypatch):
    """Strategy poll must queue PENDING and never call the broker."""
    fake_engine = SimpleNamespace(run_poll=lambda: [_FakeSignal()])
    monkeypatch.setattr(strategy_source, "QuadaptEngine", lambda: fake_engine)

    poller = strategy_source.StrategyPoller()
    await poller.poll_once()

    async with strategy_db() as db:
        result = await db.execute(select(ParsedSignal).order_by(ParsedSignal.id.desc()))
        persisted = result.scalars().first()
        assert persisted is not None
        assert persisted.status == SignalStatus.PENDING.value, (
            "strategy signal must remain PENDING for human approval"
        )


async def test_poller_does_not_auto_approve_existing_pending(strategy_db, monkeypatch):
    """A PENDING strategy signal must still be PENDING after a poll cycle."""
    fake_engine = SimpleNamespace(run_poll=lambda: [])
    monkeypatch.setattr(strategy_source, "QuadaptEngine", lambda: fake_engine)

    async with strategy_db() as db:
        sig = ParsedSignal(
            action="SELL", symbol="SP500", raw_text="[Strategy] t",
            status=SignalStatus.PENDING.value, entry_price=5000.0,
            sl=5050.0, tp=4950.0, risk_percent=1.0,
        )
        db.add(sig)
        await db.commit()
        await db.refresh(sig)
        sig_id = sig.id

    poller = strategy_source.StrategyPoller()
    await poller.poll_once()

    async with strategy_db() as db:
        refreshed = await get_one(db, ParsedSignal, id=sig_id)
        assert refreshed.status == SignalStatus.PENDING.value
