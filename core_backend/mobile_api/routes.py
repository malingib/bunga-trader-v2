"""Bunga Trader - Mobile API Routes for Android/iOS"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from typing import Optional
from datetime import datetime, date

from ..database import get_db_dependency
from ..approval_service import approve_signal_by_id, reject_signal_by_id
from ..auth import require_api_key
from ..models import ParsedSignal, SignalStatus, TradeLog
from ..config import CONFIG
from ..risk_engine import get_daily_trade_count, get_daily_pnl, get_consecutive_losses
from ..logger import setup_logger
from ..symbols import is_supported_symbol

logger = setup_logger("MobileAPI")

router = APIRouter(prefix="/mobile", tags=["mobile"])


@router.get("/dashboard")
def mobile_dashboard(db: Session = Depends(get_db_dependency)):
    """Single-call dashboard data for mobile apps."""
    today = date.today()
    today_start = datetime.combine(today, datetime.min.time())

    gold_signals = db.query(ParsedSignal).filter(ParsedSignal.symbol == "XAUUSD").all()
    pending = len([s for s in gold_signals if s.status == SignalStatus.PENDING.value])
    approved = len([s for s in gold_signals if s.status == SignalStatus.APPROVED.value])
    executed = len([s for s in gold_signals if s.status == SignalStatus.EXECUTED.value])
    rejected = len([s for s in gold_signals if s.status == SignalStatus.REJECTED.value])

    stats = {
            "pending": pending,
            "approved": approved,
            "executed": executed,
            "rejected": rejected,
            "daily_trades": get_daily_trade_count(),
            "daily_pnl": round(get_daily_pnl(), 2),
            "consecutive_losses": get_consecutive_losses(),
            "max_loss_pct": CONFIG.max_daily_loss_percent,
            "max_consecutive_losses": CONFIG.max_consecutive_losses,
            "daily_profit_target_pct": CONFIG.daily_profit_target_percent,
        }
    return {"stats": stats}


@router.get("/signals")
def mobile_signals(status: Optional[str] = "pending", limit: int = 50, db: Session = Depends(get_db_dependency)):
    """Get signals optimized for mobile display."""
    now = datetime.utcnow()
    query = db.query(ParsedSignal)
    if status:
        query = query.filter(ParsedSignal.status == status)
    signals = query.order_by(ParsedSignal.parsed_at.desc()).limit(limit).all()
    signals = [s for s in signals if is_supported_symbol(s.symbol)]

    return {
        "signals": [
            {
                "id": s.id,
                "action": s.action,
                "symbol": s.symbol,
                "entry": s.entry_price,
                "sl": s.sl,
                "tp": s.tp,
                "tp2": s.tp2,
                "tp3": s.tp3,
                "lot_size": s.lot_size,
                "status": s.status,
                "age_minutes": round((now - s.parsed_at).total_seconds() / 60.0, 1) if s.parsed_at else None,
                "expires_in_minutes": round(CONFIG.signal_max_age_minutes - ((now - s.parsed_at).total_seconds() / 60.0), 1) if s.parsed_at else None,
                "parsed_at": s.parsed_at.isoformat() if s.parsed_at else None,
                "raw_text": s.raw_text[:80] if s.raw_text else "",
            }
            for s in signals
        ],
        "count": len(signals),
    }


@router.post("/signals/{signal_id}/approve")
async def mobile_approve_signal(
    signal_id: int,
    account_balance: Optional[float] = None,
    db: Session = Depends(get_db_dependency),
    _: None = Depends(require_api_key),
):
    """Approve and dispatch a signal from mobile clients."""
    return await approve_signal_by_id(signal_id, account_balance, db)


@router.post("/signals/{signal_id}/reject")
def mobile_reject_signal(
    signal_id: int,
    reason: Optional[str] = None,
    db: Session = Depends(get_db_dependency),
    _: None = Depends(require_api_key),
):
    """Reject a signal from mobile clients."""
    return reject_signal_by_id(signal_id, reason, db)


@router.get("/notifications")
def mobile_notifications(db: Session = Depends(get_db_dependency)):
    """Get unread/pending items for push notification logic."""
    now = datetime.utcnow()
    pending = (
        db.query(ParsedSignal)
        .filter(ParsedSignal.status == SignalStatus.PENDING.value, ParsedSignal.symbol == "XAUUSD")
        .order_by(ParsedSignal.parsed_at.desc())
        .limit(10)
        .all()
    )

    return {
        "has_new": len(pending) > 0,
        "count": len(pending),
        "items": [
            {
                "id": s.id,
                "title": f"{s.action} {s.symbol}",
                "body": f"Entry: {s.entry_price or 'MARKET'} | SL: {s.sl} | TP: {s.tp}",
                "age_minutes": round((now - s.parsed_at).total_seconds() / 60.0, 1) if s.parsed_at else None,
                "timestamp": s.parsed_at.isoformat() if s.parsed_at else None,
            }
            for s in pending
        ],
    }


@router.get("/stats")
def mobile_stats(db: Session = Depends(get_db_dependency)):
    """Quick stats for mobile widget/notifications."""
    pending = db.query(ParsedSignal).filter(
        ParsedSignal.status == SignalStatus.PENDING.value,
        ParsedSignal.symbol == "XAUUSD",
    ).count()
    today = date.today()
    today_start = datetime.combine(today, datetime.min.time())
    trades_today = db.query(TradeLog).filter(TradeLog.executed_at >= today_start).count()

    return {
        "pending_signals": pending,
        "trades_today": trades_today,
        "needs_attention": pending > 0,
    }
