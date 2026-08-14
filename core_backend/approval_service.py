"""Shared signal approval/rejection workflow used by web and mobile APIs."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .config import CONFIG
from .logger import setup_logger
from .models import ParsedSignal, SignalStatus, TradeLog
from .risk_engine import calculate_lot_size, validate_signal_risk, compute_pnl
from .symbols import is_supported_symbol

logger = setup_logger("ApprovalService")

PENDING_ORDER_ACTIONS = {"BUY_LIMIT", "SELL_LIMIT", "BUY_STOP", "SELL_STOP"}
SIGNAL_MAX_AGE_MINUTES = CONFIG.signal_max_age_minutes


def _reject_signal(signal: ParsedSignal, db: Session, prefix: str, reason: str, extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    signal.status = SignalStatus.REJECTED.value
    db.commit()
    response: Dict[str, Any] = {
        "status": "rejected",
        "signal_id": signal.id,
        "reason": f"{prefix}: {reason}",
    }
    if extra:
        response.update(extra)
    return response


async def _with_approve_lock(db: Session, signal_id: int, coro_factory):
    """Guard an approve against a concurrent double-approval.

    Re-checks status inside a refreshed DB read before mutating, so two
    near-simultaneous Approve clicks cannot both pass the PENDING check and
    double-dispatch to the broker. Raises 400 if already processed.
    """
    # Expire + re-read to get the latest committed state.
    db.expire_all()
    signal = db.query(ParsedSignal).filter(ParsedSignal.id == signal_id).first()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    if signal.status != SignalStatus.PENDING.value:
        raise HTTPException(status_code=400, detail=f"Already processed: {signal.status}")
    return await coro_factory(signal)


async def reconcile_approved_signals(db: Session) -> int:
    """Re-dispatch APPROVED-but-unexecuted signals whose broker was offline.

    Called on broker connect (and opportunistically by the cleanup loop).
    Returns the number of signals that were executed during reconciliation.
    """
    from .brokers import get_active

    broker = get_active()
    if broker is None or not broker.is_connected:
        return 0

    approved = (
        db.query(ParsedSignal)
        .filter(ParsedSignal.status == SignalStatus.APPROVED.value)
        .all()
    )
    executed = 0
    for signal in approved:
        # Re-check still approved (another task may have just executed it).
        db.expire_all()
        signal = db.query(ParsedSignal).filter(ParsedSignal.id == signal.id).first()
        if not signal or signal.status != SignalStatus.APPROVED.value:
            continue
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
        except Exception as exc:  # broker error → leave approved for next sweep
            logger.error("Reconcile dispatch error for signal %s: %s", signal.id, exc)
            continue
        if result.success:
            signal.executed_at = datetime.now(timezone.utc).replace(tzinfo=None)
            signal.execution_result = f"broker:{broker.name} order:{result.order_id}"
            signal.status = SignalStatus.EXECUTED.value
            _create_trade_log(signal, db, {
                "broker": broker.name,
                "order_id": result.order_id,
                "fill_price": result.fill_price,
                "filled_units": result.filled_units,
            })
            db.commit()
            executed += 1
            logger.info("Reconciled + executed signal %s via %s", signal.id, broker.name)
        else:
            logger.warning("Reconcile: broker rejected signal %s: %s", signal.id, result.error)
    return executed


def _reset_dispatch_state(signal: ParsedSignal) -> None:
    signal.status = SignalStatus.PENDING.value
    signal.lot_size = None
    signal.risk_percent = None
    signal.executed_at = None
    signal.execution_result = None


def _signal_age_minutes(signal: ParsedSignal) -> float:
    if not signal.parsed_at:
        return 0.0
    delta = datetime.now(timezone.utc).replace(tzinfo=None) - signal.parsed_at
    return delta.total_seconds() / 60.0


async def _dispatch_via_broker(signal: ParsedSignal) -> Optional[Dict[str, Any]]:
    """Attempt to execute the approved signal through the active broker.
    Returns None if no active broker is connected, or a result dict."""
    from .brokers import get_active

    broker = get_active()
    if broker is None or not broker.is_connected:
        return None

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
            logger.info(
                "Broker %s executed signal %s: id=%s fill=%s",
                broker.name, signal.id, result.order_id, result.fill_price,
            )
            return {
                "broker": broker.name,
                "order_id": result.order_id,
                "fill_price": result.fill_price,
                "filled_units": result.filled_units,
            }
        else:
            logger.warning(
                "Broker %s rejected signal %s: %s",
                broker.name, signal.id, result.error,
            )
            return {
                "broker": broker.name,
                "error": result.error,
            }
    except Exception as exc:
        logger.error("Broker %s error for signal %s: %s", broker.name, signal.id, exc)
        return {"broker": broker.name, "error": str(exc)}


def _create_trade_log(signal: ParsedSignal, db: Session, broker_result: Optional[Dict[str, Any]] = None) -> TradeLog:
    """Create a TradeLog entry after successful execution."""
    fill = (
        broker_result.get("fill_price") or signal.entry_price
        if broker_result else signal.entry_price
    )
    lot = signal.lot_size or 0.0
    # P&L computed at execution by the single source of truth (risk_engine).
    pnl = compute_pnl(
        symbol=signal.symbol,
        action=signal.action,
        entry_price=signal.entry_price or fill,
        exit_price=fill,
        lot=lot,
    )
    outcome = "win" if pnl > 0 else ("loss" if pnl < 0 else "breakeven")
    trade = TradeLog(
        parsed_signal_id=signal.id,
        symbol=signal.symbol,
        action=signal.action,
        lot_size=lot,
        entry_price=fill,
        sl=signal.sl,
        tp=signal.tp,
        result=outcome,
        pnl=pnl,
        executed_at=datetime.now(timezone.utc).replace(tzinfo=None),
    )
    db.add(trade)
    db.flush()
    return trade


async def approve_signal_by_id(
    signal_id: int,
    account_balance: Optional[float],
    db: Session,
) -> Dict[str, Any]:
    async def _do_approve(signal: ParsedSignal) -> Dict[str, Any]:
        if not is_supported_symbol(signal.symbol):
            return _reject_signal(signal, db, "Symbol", f"Unsupported symbol {signal.symbol}")

        age_minutes = _signal_age_minutes(signal)
        if age_minutes > SIGNAL_MAX_AGE_MINUTES:
            return _reject_signal(
                signal,
                db,
                "Expiry",
                f"Signal is {age_minutes:.1f} minutes old; max age is {SIGNAL_MAX_AGE_MINUTES} minutes",
                {"age_minutes": round(age_minutes, 2)},
            )

        balance = account_balance or CONFIG.demo_balance

        if signal.action in PENDING_ORDER_ACTIONS and not signal.entry_price:
            return _reject_signal(signal, db, "Risk", "Pending orders require an entry price")

        valid, risk_reason = validate_signal_risk(signal, balance)
        if not valid:
            return _reject_signal(signal, db, "Risk", risk_reason or "Risk validation failed")

        lot, lot_error = calculate_lot_size(
            symbol=signal.symbol,
            entry_price=signal.entry_price,
            sl_price=signal.sl,
            account_balance=balance,
        )

        if lot_error:
            return _reject_signal(signal, db, "Lot", lot_error)

        if lot <= 0:
            return _reject_signal(signal, db, "Lot", "Zero lot")

        signal.lot_size = lot
        signal.risk_percent = CONFIG.default_risk_percent
        signal.status = SignalStatus.APPROVED.value
        db.commit()

        now = datetime.now(timezone.utc).replace(tzinfo=None)

        # Attempt broker dispatch
        broker_result = await _dispatch_via_broker(signal)

        if broker_result is not None and "error" not in broker_result:
            # Broker executed successfully → mark as executed, log trade
            fill_price = broker_result.get("fill_price") or signal.entry_price
            signal.executed_at = now
            signal.execution_result = f"broker:{broker_result['broker']} order:{broker_result.get('order_id','?')}"
            signal.status = SignalStatus.EXECUTED.value
            _create_trade_log(signal, db, broker_result)
            db.commit()

            return {
                "status": "executed",
                "signal_id": signal.id,
                "lot_size": lot,
                "broker": broker_result,
                "fill_price": fill_price,
            }
        elif broker_result is not None and "error" in broker_result:
            # Broker rejected → rollback approval state
            logger.warning(
                "Broker dispatch failed for signal %s: %s; rolling back",
                signal.id, broker_result["error"],
            )
            _reset_dispatch_state(signal)
            db.commit()
            raise HTTPException(
                status_code=503,
                detail=f"Broker dispatch failed: {broker_result['error']}",
            )

        # No active broker → leave approved, will dispatch when broker connects
        logger.info("Signal %s approved — no broker connected, waiting", signal.id)
        return {
            "status": "approved",
            "signal_id": signal.id,
            "lot_size": lot,
        }

    # Idempotency: re-check PENDING inside a refreshed read before mutating,
    # so two concurrent Approve clicks cannot both dispatch to the broker.
    return await _with_approve_lock(db, signal_id, _do_approve)


def reject_signal_by_id(signal_id: int, reason: Optional[str], db: Session) -> Dict[str, Any]:
    signal = db.query(ParsedSignal).filter(ParsedSignal.id == signal_id).first()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")

    if signal.status != SignalStatus.PENDING.value:
        raise HTTPException(status_code=400, detail="Already processed")

    signal.status = SignalStatus.REJECTED.value
    db.commit()
    logger.info("Signal %s rejected: %s", signal_id, reason or "manual")
    return {"status": "rejected", "signal_id": signal.id}
