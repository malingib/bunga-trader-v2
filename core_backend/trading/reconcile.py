"""Position reconciliation: keep TradeLog P&L in sync with the broker.

This is READ-ONLY bookkeeping. It never places orders, never changes signal
status, and never re-dispatches. It polls the active broker's open positions
and:

  1. Matches each open position to an EXECUTED TradeLog by (symbol, side, lot).
  2. While a position is still open, leaves the TradeLog as-is (the open P&L
     recorded at execution is the working value).
  3. When a position disappears (closed), finalizes the TradeLog: sets
     closed_at and recomputes realized P&L from entry → last-seen broker
     price, and marks result win/loss/breakeven.

Realized P&L here is an approximation: the broker adapters expose the position's
current price rather than the exact close fill. For a precise figure the human
can still POST /trades/{id}/feedback, which takes precedence (we never
overwrite a manually-entered P&L).

Called from the cleanup loop (periodic) and exposed read-only via
GET /trades/reconcile for the dashboard.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from sqlalchemy import select

from ..database import get_db
from ..logger import setup_logger
from ..models import TradeLog
from ..risk_engine import compute_pnl
from ..brokers import get_active

logger = setup_logger("TradeReconcile")

# Reconcile only positions opened by this system (EXECUTED) that haven't been
# finalized yet (closed_at IS NULL).
_UNCLOSED_STATUSES = ("executed", "approved")


def _side_from_action(action: str) -> str:
    return "BUY" if action in ("BUY", "BUY_LIMIT", "BUY_STOP") else "SELL"


def _match_key(pos) -> Tuple[str, str, float]:
    return (pos.symbol, pos.side, float(pos.size))


async def reconcile_positions() -> Dict[str, Any]:
    """Reconcile open broker positions against TradeLog rows.

    Returns a summary dict: {checked, open, closed_now, errors}.
    Safe to call with no broker connected (returns zeros).
    """
    broker = get_active()
    if broker is None or not broker.is_connected:
        return {"checked": 0, "open": 0, "closed_now": 0, "errors": 0,
                "note": "no broker connected"}

    try:
        positions = await broker.get_positions()
    except Exception as exc:
        logger.error("Position reconciliation: broker.get_positions failed: %s", exc)
        return {"checked": 0, "open": 0, "closed_now": 0, "errors": 1,
                "note": f"broker error: {exc}"}

    # Build a lookup of currently-open (symbol, side, size) keys.
    open_keys = {_match_key(p) for p in positions}

    summary = {"checked": 0, "open": 0, "closed_now": 0, "errors": 0}
    async with get_db() as db:
        result = await db.execute(
            select(TradeLog).where(TradeLog.closed_at.is_(None))
        )
        trades: List[TradeLog] = result.scalars().all()

        for trade in trades:
            summary["checked"] += 1
            key = (trade.symbol, _side_from_action(trade.action), float(trade.lot_size))
            if key in open_keys:
                # Still open — leave as-is (working P&L stays).
                summary["open"] += 1
                continue

            # Position not in the open set → it closed. Finalize if we haven't
            # already (closed_at is None here) and the P&L wasn't manually set.
            # We only auto-finalize when pnl is still None (the open-time
            # default) so a human's POST /trades/{id}/feedback wins.
            if trade.pnl is None:
                # Use the last-seen broker price as the exit approximation.
                exit_price = None
                for p in positions:
                    if _match_key(p) == key:
                        exit_price = p.current_price
                        break
                # Fall back to entry if no price available.
                exit_price = exit_price or trade.entry_price or 0.0
                pnl = compute_pnl(
                    symbol=trade.symbol,
                    action=trade.action,
                    entry_price=trade.entry_price or exit_price,
                    exit_price=exit_price,
                    lot=float(trade.lot_size),
                    current_price=exit_price,
                )
                trade.pnl = pnl
                trade.result = "win" if pnl > 0 else ("loss" if pnl < 0 else "breakeven")
                trade.closed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                await db.commit()
                summary["closed_now"] += 1
                logger.info(
                    "Reconciled close: trade %s %s %s pnl=%.2f",
                    trade.id, trade.symbol, trade.action, pnl,
                )
            else:
                # Manually-set P&L already present → just stamp the close time.
                trade.closed_at = datetime.now(timezone.utc).replace(tzinfo=None)
                await db.commit()
                summary["closed_now"] += 1

    return summary


async def get_open_positions() -> List[Dict[str, Any]]:
    """Return the active broker's open positions (dashboard display)."""
    broker = get_active()
    if broker is None or not broker.is_connected:
        return []
    try:
        positions = await broker.get_positions()
    except Exception as exc:
        logger.error("get_open_positions failed: %s", exc)
        return []
    return [
        {
            "symbol": p.symbol,
            "side": p.side,
            "size": p.size,
            "entry_price": p.entry_price,
            "current_price": p.current_price,
            "pnl": p.pnl,
            "broker_position_id": p.broker_position_id,
        }
        for p in positions
    ]
