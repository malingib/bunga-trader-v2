"""Bunga Trader v2 — Strategy Source Integration.

Feeds signals from the strategy engine into the Bunga pipeline
without going through the Telegram listener.

Strategy signals bypass parser and AI validation (they already have
quality scores and machine-readable structure). They go directly
to the approval service → risk engine → trade dispatcher.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import List, Optional

from ..logger import setup_logger
from ..models import ParsedSignal, SignalStatus
from ..database import get_db
from ..config import CONFIG
from ..strategies.engine import QuadaptEngine, StrategySignal
from ..strategies.config import QUADAPT_CFG

logger = setup_logger("StrategySource")


def _signal_to_db(signal: StrategySignal) -> int:
    """Write a strategy signal to the database as a ParsedSignal.

    Returns the parsed_signal.id.
    """
    ps = ParsedSignal(
        raw_signal_id=0,  # No Telegram raw signal — strategy-generated
        action=signal.action,
        symbol=signal.symbol,
        entry_price=signal.entry_price,
        sl=signal.sl,
        tp=signal.tp,
        tp2=signal.tp2,
        tp3=signal.tp3,
        raw_text=(
            f"[Strategy] {signal.signal_source}: {signal.action} {signal.symbol} "
            f"@{signal.entry_price} | SL: {signal.sl} TP: {signal.tp} "
            f"| Score: {signal.quality_score}"
        ),
        parsed_at=datetime.utcnow(),
        status=SignalStatus.PENDING.value,
        ai_score=signal.quality_score / 100.0,
        lot_size=0.0,
        risk_percent=CONFIG.default_risk_percent,
    )

    db = next(get_db())
    try:
        db.add(ps)
        db.commit()
        db.refresh(ps)
        logger.info(f"Strategy signal written to DB: id={ps.id} {signal.symbol} {signal.action}")
        return ps.id
    except Exception as e:
        db.rollback()
        logger.error(f"Failed to write strategy signal to DB: {e}")
        return 0
    finally:
        db.close()


class StrategyPoller:
    """Periodically polls the Quadapt engine and pushes signals into the pipeline."""

    def __init__(self) -> None:
        self.engine = QuadaptEngine()
        self._running = False

    async def poll_once(self) -> List[StrategySignal]:
        """Run one evaluation cycle for all configured symbols."""
        logger.info("Strategy poll cycle starting...")
        signals = self.engine.run_poll()

        for signal in signals:
            # Write to DB so the approval flow picks it up
            db_id = _signal_to_db(signal)
            if db_id:
                logger.info(
                    f"Signal queued: {signal.symbol} {signal.action} "
                    f"@ {signal.entry_price} (score={signal.quality_score})"
                )
            else:
                logger.warning(
                    f"Failed to queue signal: {signal.symbol} {signal.action}"
                )

        return signals

    async def run_loop(self) -> None:
        """Run continuous polling loop.

        Call via: asyncio.create_task(strategy_poller.run_loop())
        """
        self._running = True
        interval = QUADAPT_CFG.market_data.poll_interval_seconds

        logger.info(f"Strategy poller started (interval={interval}s)")

        while self._running:
            try:
                signals = await asyncio.to_thread(self.poll_once)
                if signals:
                    logger.info(f"Poll cycle produced {len(signals)} signal(s)")
                else:
                    logger.debug("Poll cycle produced no signals")
            except Exception as e:
                logger.error(f"Strategy poll error: {e}")

            await asyncio.sleep(interval)

    def stop(self) -> None:
        """Stop the polling loop."""
        self._running = False
        logger.info("Strategy poller stopped")
