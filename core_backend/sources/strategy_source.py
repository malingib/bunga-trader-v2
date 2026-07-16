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


def _signal_to_db(signal: StrategySignal) -> int:
    """Write a strategy signal to the database as a ParsedSignal.

    Returns the parsed_signal.id.
    """
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
        parsed_at=datetime.now(timezone.utc).replace(tzinfo=None),
        status=SignalStatus.PENDING.value,
        ai_score=signal.quality_score / 100.0,
        lot_size=0.0,
        risk_percent=CONFIG.default_risk_percent,
    )

    with get_db() as db:
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


def _auto_approve(signal_id: int) -> None:
    """Auto-approve a strategy signal immediately (no manual gate)."""
    try:
        with get_db() as db:
            signal = db.query(ParsedSignal).filter(ParsedSignal.id == signal_id).first()
            if not signal:
                logger.warning(f"Auto-approve: signal {signal_id} not found")
                return
            if signal.status != SignalStatus.PENDING.value:
                return  # already processed

            balance = CONFIG.demo_balance
            from ..risk_engine import calculate_lot_size, validate_signal_risk

            valid, reason = validate_signal_risk(signal, balance)
            if not valid:
                signal.status = SignalStatus.REJECTED.value
                db.commit()
                logger.warning(f"Auto-approve: signal {signal_id} rejected by risk: {reason}")
                return

            lot, lot_err = calculate_lot_size(
                symbol=signal.symbol,
                entry_price=signal.entry_price,
                sl_price=signal.sl,
                account_balance=balance,
            )
            if lot_err or lot <= 0:
                signal.status = SignalStatus.REJECTED.value
                db.commit()
                logger.warning(f"Auto-approve: signal {signal_id} lot sizing failed: {lot_err}")
                return

            signal.lot_size = lot
            signal.risk_percent = CONFIG.default_risk_percent
            signal.status = SignalStatus.APPROVED.value
            db.commit()

            # Attempt broker dispatch
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(_dispatch_signal(signal_id))
            except RuntimeError:
                pass  # no running loop, dispatch in sync mode

            logger.info(
                f"Signal {signal_id} auto-approved: {signal.action} {signal.symbol} "
                f"lot={lot} @ {signal.entry_price}"
            )

    except Exception as e:
        logger.error(f"Auto-approve failed for signal {signal_id}: {e}")


async def _dispatch_signal(signal_id: int) -> None:
    """Dispatch an approved signal through the active broker."""
    from ..brokers import get_active

    with get_db() as db:
        signal = db.query(ParsedSignal).filter(ParsedSignal.id == signal_id).first()
        if not signal or signal.status != SignalStatus.APPROVED.value:
            return

        broker = get_active()
        if broker is None or not broker.is_connected:
            signal.status = SignalStatus.APPROVED.value  # leave approved, will dispatch when broker connects
            db.commit()
            logger.info(f"Signal {signal_id} approved — no broker connected, waiting")
            return

        try:
            result = await broker.place_order(
                action=signal.action,
                symbol=signal.symbol,
                entry_price=signal.entry_price,
                sl=signal.sl,
                tp=signal.tp,
                tp2=signal.tp2,
                tp3=signal.tp3,
                lot=signal.lot_size or 0.0,
            )
            if result.success:
                from datetime import datetime
                signal.status = SignalStatus.EXECUTED.value
                signal.executed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                signal.execution_result = f"broker:{broker.name} order:{result.order_id}"
                from ..models import TradeLog
                trade = TradeLog(
                    parsed_signal_id=signal.id,
                    symbol=signal.symbol,
                    action=signal.action,
                    lot_size=signal.lot_size or 0.0,
                    entry_price=result.fill_price or signal.entry_price,
                    sl=signal.sl,
                    tp=signal.tp,
                    result="executed",
                    executed_at=signal.executed_at,
                )
                db.add(trade)
                db.commit()
                logger.info(
                    f"Signal {signal_id} EXECUTED via {broker.name}: "
                    f"order={result.order_id} fill={result.fill_price}"
                )
            else:
                logger.warning(
                    f"Signal {signal_id} broker rejected: {result.error}"
                )
        except Exception as e:
            logger.error(f"Signal {signal_id} broker dispatch error: {e}")


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
            # Write to DB so the approval flow picks it up
            db_id = _signal_to_db(signal)
            if db_id:
                logger.info(
                    f"Signal queued: {signal.symbol} {signal.action} "
                    f"@ {signal.entry_price} (score={signal.quality_score})"
                )
                # Auto-approve immediately — no manual gate
                _auto_approve(db_id)
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
