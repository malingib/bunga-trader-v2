"""Bunga Trader - Risk Engine"""
from typing import Optional, Tuple
from datetime import datetime, date
from .database import get_db
from .models import TradeLog, ParsedSignal
from .config import CONFIG
from .logger import setup_logger

logger = setup_logger("RiskEngine")

PIP_SIZES = {
    "FOREX": 0.0001,
    "JPY": 0.01,
    "GOLD": 0.01,
    "SILVER": 0.001,
    "CRYPTO": 0.01,
    "INDICES": 1.0,
}

def get_instrument_type(symbol: str) -> str:
    symbol_upper = symbol.upper()
    if symbol_upper in ("XAUUSD", "GOLD"):
        return "GOLD"
    elif symbol_upper in ("XAGUSD", "SILVER"):
        return "SILVER"
    elif symbol_upper in ("BTCUSD", "ETHUSD", "LTCUSD", "XRPUSD"):
        return "CRYPTO"
    elif symbol_upper in ("SP500", "NAS100", "US30", "US100", "US500", "DE40", "UK100", "JP225"):
        return "INDICES"
    elif "JPY" in symbol_upper:
        return "JPY"
    else:
        return "FOREX"

def get_pip_size(symbol: str) -> float:
    inst_type = get_instrument_type(symbol)
    return PIP_SIZES.get(inst_type, 0.0001)

def get_pip_value_per_lot(symbol: str, current_price: Optional[float] = None) -> float:
    symbol_upper = symbol.upper()
    inst_type = get_instrument_type(symbol)
    if inst_type == "GOLD":
        return 1.0
    elif inst_type == "SILVER":
        return 5.0
    elif inst_type == "CRYPTO":
        return 1.0
    elif inst_type == "INDICES":
        return 50.0
    elif inst_type == "JPY":
        if current_price and current_price > 0:
            return (0.01 / current_price) * 100_000
        return 7.0
    else:
        if symbol_upper.endswith("USD"):
            return 10.0
        elif symbol_upper.startswith("USD"):
            if current_price and current_price > 0:
                return (0.0001 / current_price) * 100_000
            return 10.0
        else:
            if current_price and current_price > 0:
                return (0.0001 / current_price) * 100_000
            return 10.0

def get_daily_trade_count() -> int:
    today = date.today()
    today_start = datetime.combine(today, datetime.min.time())
    with get_db() as db:
        return db.query(TradeLog).filter(TradeLog.executed_at >= today_start).count()

def get_daily_pnl() -> float:
    today = date.today()
    today_start = datetime.combine(today, datetime.min.time())
    with get_db() as db:
        trades = db.query(TradeLog).filter(TradeLog.executed_at >= today_start).all()
        return sum(t.pnl or 0 for t in trades)


def get_consecutive_losses() -> int:
    """Count how many consecutive losing trades (P&L < 0) in today's trades, from most recent backward."""
    today = date.today()
    today_start = datetime.combine(today, datetime.min.time())
    with get_db() as db:
        trades = (
            db.query(TradeLog)
            .filter(TradeLog.executed_at >= today_start)
            .order_by(TradeLog.executed_at.desc())
            .all()
        )
    count = 0
    for t in trades:
        if t.pnl is not None and t.pnl < 0:
            count += 1
        else:
            break
    return count


def get_daily_pnl_percent(account_balance: float) -> float:
    if account_balance <= 0:
        return 0.0
    pnl = get_daily_pnl()
    return (pnl / account_balance) * 100.0


def check_daily_limits(account_balance: float) -> Tuple[bool, Optional[str]]:
    if account_balance <= 0:
        return False, "Invalid account balance"

    pnl_percent = get_daily_pnl_percent(account_balance)

    if pnl_percent <= -CONFIG.max_daily_loss_percent:
        return False, (
            f"Daily loss limit hit ({pnl_percent:.2f}% / -{CONFIG.max_daily_loss_percent:.2f}%)"
        )

    if pnl_percent >= CONFIG.daily_profit_target_percent:
        return False, (
            f"Daily profit target hit (+{pnl_percent:.2f}% / {CONFIG.daily_profit_target_percent:.2f}%) — stopping"
        )

    consec = get_consecutive_losses()
    if consec >= CONFIG.max_consecutive_losses:
        return False, (
            f"{consec} consecutive losses reached (limit: {CONFIG.max_consecutive_losses})"
        )

    return True, None

def calculate_lot_size(
    symbol: str,
    entry_price: Optional[float],
    sl_price: Optional[float],
    account_balance: float,
    risk_percent: float = CONFIG.default_risk_percent,
    max_lot: float = CONFIG.max_lot,
    current_price: Optional[float] = None,
) -> Tuple[float, Optional[str]]:
    if account_balance <= 0:
        return 0.0, "Invalid account balance"
    if not sl_price or sl_price <= 0:
        return 0.0, "Invalid stop loss price"
    if risk_percent <= 0 or risk_percent > 10:
        return 0.0, f"Risk percent {risk_percent}% out of range (0.1-10%)"
    allowed, reason = check_daily_limits(account_balance)
    if not allowed:
        return 0.0, reason
    effective_entry = entry_price if entry_price else current_price
    if not effective_entry or effective_entry <= 0:
        logger.warning(f"No entry price for {symbol}, using minimum lot")
        return 0.01, None
    pip_size = get_pip_size(symbol)
    sl_distance = abs(effective_entry - sl_price)
    sl_pips = sl_distance / pip_size
    if sl_pips < 1.0:
        return 0.0, f"Stop loss too tight ({sl_pips:.1f} pips, minimum 1 pip)"
    risk_amount = account_balance * (risk_percent / 100.0)
    pip_value = get_pip_value_per_lot(symbol, effective_entry)
    lot = risk_amount / (sl_pips * pip_value)
    lot = max(0.01, lot)
    lot = min(lot, max_lot)
    lot = round(lot, 2)
    logger.info(f"Lot for {symbol}: balance=${account_balance:.2f}, risk={risk_percent}%, SL={sl_pips:.1f}pips, pip_val=${pip_value:.2f}, lot={lot:.2f}")
    return lot, None

def compute_pnl(
    symbol: str,
    action: str,
    entry_price: float,
    exit_price: float,
    lot: float,
    current_price: Optional[float] = None,
) -> float:
    """Signed realized P&L in account currency for a closed trade.

    Direction is taken from the action (BUY/BUY_LIMIT/BUY_STOP = long,
    SELL* = short). This is the SINGLE source of truth for P&L so the
    dashboard per-symbol stats and risk-engine loss-gating agree.
    """
    pip_size = get_pip_size(symbol)
    pip_val = get_pip_value_per_lot(symbol, current_price or exit_price)
    direction = 1.0 if action in ("BUY", "BUY_LIMIT", "BUY_STOP") else -1.0
    points = (exit_price - entry_price) / pip_size if pip_size else 0.0
    return direction * points * pip_val * lot


def validate_signal_risk(signal: ParsedSignal, account_balance: float) -> Tuple[bool, Optional[str]]:
    if not signal.sl:
        return False, "No stop loss defined"
    if signal.action in ("BUY_LIMIT", "SELL_LIMIT", "BUY_STOP", "SELL_STOP") and not signal.entry_price:
        return False, "Pending orders require an entry price"
    if not signal.entry_price and not signal.tp:
        return False, "No entry price or take profit defined"
    if signal.entry_price and signal.sl and signal.tp:
        risk = abs(signal.entry_price - signal.sl)
        reward = abs(signal.tp - signal.entry_price)
        if risk <= 0:
            return False, "Invalid risk distance (SL = Entry)"
        rr_ratio = reward / risk
        if rr_ratio < CONFIG.min_rr_ratio:
            return False, (
                f"R:R ratio {rr_ratio:.2f} below minimum {CONFIG.min_rr_ratio:.2f}"
            )
    return True, None
