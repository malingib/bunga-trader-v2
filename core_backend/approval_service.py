"""Shared signal approval/rejection workflow used by web and mobile APIs."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .ai_engine import ai_validate_signal
from .config import CONFIG
from .logger import setup_logger
from .models import ParsedSignal, SignalStatus
from .risk_engine import calculate_lot_size, validate_signal_risk
from .symbols import is_supported_symbol
from .trade_dispatcher import manager

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


def _reset_dispatch_state(signal: ParsedSignal) -> None:
    signal.status = SignalStatus.PENDING.value
    signal.lot_size = None
    signal.risk_percent = None
    signal.executed_at = None
    signal.execution_result = None


def _signal_age_minutes(signal: ParsedSignal) -> float:
    if not signal.parsed_at:
        return 0.0
    delta = datetime.utcnow() - signal.parsed_at
    return delta.total_seconds() / 60.0


async def approve_signal_by_id(
    signal_id: int,
    account_balance: Optional[float],
    db: Session,
) -> Dict[str, Any]:
    signal = db.query(ParsedSignal).filter(ParsedSignal.id == signal_id).first()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")

    if signal.status != SignalStatus.PENDING.value:
        raise HTTPException(status_code=400, detail=f"Already processed: {signal.status}")

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

    bridge_status = await manager.get_status()
    if bridge_status.get("connected_bridges", 0) <= 0:
        raise HTTPException(
            status_code=503,
            detail="Bridge offline; trade not dispatched",
        )

    ai_approved, ai_score, ai_reason = await ai_validate_signal(signal)
    if not ai_approved:
        return _reject_signal(
            signal,
            db,
            "AI",
            ai_reason or "Rejected by AI",
            {"ai_score": ai_score},
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

    trade_payload = {
        "type": "new_trade",
        "action": signal.action,
        "symbol": signal.symbol,
        "entry": signal.entry_price,
        "sl": signal.sl,
        "tp": signal.tp,
        "tp2": signal.tp2,
        "tp3": signal.tp3,
        "lot": signal.lot_size,
        "signal_id": signal.id,
        "timestamp": datetime.utcnow().isoformat(),
    }

    try:
        dispatch_result = await manager.broadcast_trade(trade_payload)
    except Exception as exc:
        logger.error("Broadcast failed for signal %s: %s", signal.id, exc)
        _reset_dispatch_state(signal)
        db.commit()
        raise HTTPException(
            status_code=503,
            detail="Bridge dispatch failed; signal left pending",
        ) from exc

    if dispatch_result.get("sent_count", 0) <= 0:
        logger.warning("No bridge received signal %s; rolling back approval", signal.id)
        _reset_dispatch_state(signal)
        db.commit()
        raise HTTPException(
            status_code=503,
            detail="Bridge dispatch failed; signal left pending",
        )

    return {
        "status": "approved",
        "signal_id": signal.id,
        "lot_size": lot,
        "ai_score": ai_score,
        "dispatch": dispatch_result,
    }


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
