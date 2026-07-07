"""Handle trade execution feedback from the MT5 bridge WebSocket."""
import json
from datetime import datetime
from typing import Any, Dict

from .database import get_db
from .logger import setup_logger
from .models import ParsedSignal, SignalStatus, TradeLog

logger = setup_logger("WSFeedback")


def process_trade_feedback_message(raw: str) -> bool:
    """Parse bridge feedback JSON and persist trade outcome. Returns True if handled."""
    try:
        msg = json.loads(raw)
    except json.JSONDecodeError:
        return False

    if msg.get("type") != "trade_feedback":
        return False

    _apply_trade_feedback(msg)
    return True


def _apply_trade_feedback(msg: Dict[str, Any]) -> None:
    signal_id = msg.get("signal_id")
    if not signal_id:
        logger.warning("trade_feedback missing signal_id")
        return

    success = bool(msg.get("success", False))
    now = datetime.utcnow()

    with get_db() as db:
        signal = db.query(ParsedSignal).filter(ParsedSignal.id == signal_id).first()
        if not signal:
            logger.warning("trade_feedback for unknown signal_id=%s", signal_id)
            return

        if success:
            signal.status = SignalStatus.EXECUTED.value
            signal.executed_at = now
            signal.execution_result = json.dumps(
                {"ticket": msg.get("ticket"), "price": msg.get("price")}
            )
            result_status = "executed"
            error_message = None
        else:
            signal.status = SignalStatus.FAILED.value
            signal.executed_at = now
            error_message = msg.get("error") or "Execution failed"
            signal.execution_result = error_message
            result_status = "failed"

        trade = TradeLog(
            parsed_signal_id=signal.id,
            symbol=signal.symbol,
            action=signal.action,
            lot_size=signal.lot_size or 0.01,
            entry_price=msg.get("price") or signal.entry_price,
            sl=signal.sl,
            tp=signal.tp,
            result=result_status,
            pnl=None,
            executed_at=now,
            error_message=error_message,
        )
        db.add(trade)
        db.flush()

    logger.info(
        "Signal %s marked %s (trade_log created)",
        signal_id,
        result_status,
    )
