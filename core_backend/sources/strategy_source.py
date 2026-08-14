"""Bunga Trader v2 — Strategy Source Integration.

Feeds signals from the strategy engine into the Bunga pipeline
without going through the Telegram listener.

Strategy signals bypass parser and AI validation (they already have
quality scores and machine-readable structure). They go directly
to the approval service → risk engine → trade dispatcher.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import List, Optional

from ..logger import setup_logger
from ..models import ParsedSignal, SignalStatus
from ..database import get_db
from ..config import CONFIG
from ..strategies.engine import QuadaptEngine, StrategySignal
from ..strategies.config import QUADAPT_CFG
from ..approval_service import approve_signal_by_id

logger = setup_logger("StrategySource")


async def _signal_to_db(signal: StrategySignal) -> int:
    """Write a strategy signal to the database as a ParsedSignal.

    Returns the parsed_signal.id.
    """
    # Use the engine's generated_at as the reconciliation key so a later
    # trade-close can be matched back to this signal by (symbol, generated_at).
    gen_at = signal.generated_at
    ps = ParsedSignal(
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
        parsed_at=gen_at,
        status=SignalStatus.PENDING.value,
        ai_score=signal.quality_score / 100.0,
        lot_size=0.0,
        risk_percent=CONFIG.default_risk_percent,
        strategy_generated_at=gen_at.isoformat(),
    )

    async with get_db() as db:
        try:
            db.add(ps)
            await db.commit()
            await db.refresh(ps)
            logger.info(f"Strategy signal written to DB: id={ps.id} {signal.symbol} {signal.action}")
            return ps.id
        except Exception as e:
            await db.rollback()
            logger.error(f"Failed to write strategy signal to DB: {e}")
            return 0


# Module-level poller instance for external callers
_strategy_poller_instance: Optional["StrategyPoller"] = None


def get_strategy_poller() -> "StrategyPoller":
    global _strategy_poller_instance
    if _strategy_poller_instance is None:
        _strategy_poller_instance = StrategyPoller()
    return _strategy_poller_instance


class StrategyPoller:
    """Periodically polls the Quadapt engine and pushes signals into the pipeline."""

    def __init__(self) -> None:
        self.engine = QuadaptEngine()
        self._running = False
        self.paused = False

    async def poll_once(self) -> List[StrategySignal]:
        """Run one evaluation cycle for all configured symbols."""
        logger.info("Strategy poll cycle starting...")
        signals = self.engine.run_poll()

        for signal in signals:
            # Write to DB as PENDING so the human approval gate picks it up.
            # Strategy signals MUST NOT auto-approve/auto-execute (AGENTS.md:
            # explicit human confirmation required before any broker order).
            db_id = await _signal_to_db(signal)
            if db_id:
                logger.info(
                    f"Signal queued for human approval: {signal.symbol} {signal.action} "
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
                if self.paused:
                    logger.debug("Strategy poller paused — skipping cycle")
                else:
                    signals = await self.poll_once()
                    if signals:
                        logger.info(f"Poll cycle produced {len(signals)} signal(s)")
                    else:
                        logger.debug("Poll cycle produced no signals")
            except Exception as e:
                logger.error(f"Strategy poll error: {e}")

            await asyncio.sleep(interval)

    def pause(self) -> None:
        """Pause the polling loop."""
        self.paused = True
        logger.info("Strategy poller paused")

    def resume(self) -> None:
        """Resume the polling loop."""
        self.paused = False
        logger.info("Strategy poller resumed")

    def stop(self) -> None:
        """Stop the polling loop."""
        self._running = False
        logger.info("Strategy poller stopped")
