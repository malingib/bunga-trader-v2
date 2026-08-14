"""
Bunga Trader - FastAPI Main Application v2
REST API + Web Dashboard
"""
import asyncio
import json
import re
import time
import hmac
from contextlib import asynccontextmanager
from datetime import datetime, timezone, date
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from .database import engine, get_db_dependency, get_db, apply_migrations
from .models import Base, ParsedSignal, SignalStatus, TradeLog
from .approval_service import (
    approve_signal_by_id,
    reject_signal_by_id,
    reconcile_approved_signals,
    dispatch_circuit_open,
    reset_dispatch_circuit,
)
from .risk_engine import get_daily_pnl, get_consecutive_losses
from .config import CONFIG
from .logger import setup_logger
from .symbols import is_supported_symbol
from .sources.strategy_source import StrategyPoller

logger = setup_logger("MainAPI")

_approve_all_last_at: float = 0.0
_APPROVE_ALL_COOLDOWN_SEC = 60.0
_SIGNAL_MAX_AGE_MINUTES = CONFIG.signal_max_age_minutes


async def cleanup_loop():
    logger.info("Cleanup background task started")
    while True:
        try:
            async with get_db() as db:
                from .models import ParsedSignal
                # Clean old executed/rejected signals (>7 days)
                await db.execute(text("""
                    DELETE FROM parsed_signals 
                    WHERE status != 'pending' 
                    AND parsed_at < datetime('now', '-7 days')
                """))
                # Clean expired pending signals (older than max age)
                max_age = CONFIG.signal_max_age_minutes
                await db.execute(text("""
                    DELETE FROM parsed_signals 
                    WHERE status = 'pending' 
                    AND parsed_at < datetime('now', :max_age || ' minutes')
                """).bindparams(max_age=max_age))
                # Expire APPROVED-but-unexecuted signals that were never
                # dispatched (broker never reconnected within the window).
                approved_max_age = CONFIG.approved_signal_max_age_minutes
                await db.execute(text("""
                    UPDATE parsed_signals
                    SET status = 'rejected',
                        execution_result = 'expired: approved but not executed within ' || :approved_max_age || 'm'
                    WHERE status = 'approved'
                    AND parsed_at < datetime('now', :approved_max_age || ' minutes')
                """).bindparams(approved_max_age=approved_max_age))
                await db.commit()
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Bunga Trader API v2 starting...")
    # Create tables + run migrations BEFORE accepting traffic.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await apply_migrations()

    # Purge expired pending signals immediately on startup
    try:
        async with get_db() as db:
            max_age = CONFIG.signal_max_age_minutes
            await db.execute(text("""
                DELETE FROM parsed_signals 
                WHERE status = 'pending' 
                AND parsed_at < datetime('now', :max_age || ' minutes')
            """).bindparams(max_age=max_age))
            await db.commit()
    except Exception as e:
        logger.error(f"Startup cleanup error: {e}")

    global _strategy_poller
    strategy_poller = StrategyPoller()
    _strategy_poller = strategy_poller
    tasks = [
        asyncio.create_task(cleanup_loop()),
        asyncio.create_task(strategy_poller.run_loop()),
    ]
    yield
    logger.info("Bunga Trader API v2 shutting down...")
    strategy_poller.stop()
    for task in tasks:
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass


# Create main app
app = FastAPI(
    title="Bunga Trader API v2",
    description="Local-first automated trading with free LLM fallback",
    version="2.0.0",
    lifespan=lifespan,
)

_cors_origins = [
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://localhost:3000",
    "http://127.0.0.1:3000",
]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Optional dashboard shared-secret (W1). When CONFIG.dashboard_token is set,
# every mutating request (POST/PUT/PATCH/DELETE) must carry a matching
# `X-Dashboard-Token` header. GET/HEAD/OPTIONS stay open so the local dashboard
# can still render on loopback. Leave DASHBOARD_TOKEN unset for the default
# local-only deployment where loopback binding is the only control needed.
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse

_MUTATING_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


async def _dashboard_auth_middleware(request, call_next):
    if CONFIG.dashboard_token and request.method in _MUTATING_METHODS:
        provided = request.headers.get("X-Dashboard-Token", "")
        if not provided or not hmac.compare_digest(provided, CONFIG.dashboard_token):
            return JSONResponse(
                status_code=403,
                content={"detail": "Missing or invalid X-Dashboard-Token"},
            )
    return await call_next(request)


if CONFIG.dashboard_token:
    app.add_middleware(BaseHTTPMiddleware, dispatch=_dashboard_auth_middleware)

# =============================================================================
# API ROUTES (must come BEFORE static files)
# =============================================================================

@app.get("/health")
def health_check():
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), "version": "2.0.0"}


@app.get("/status")
async def system_status(db: AsyncSession = Depends(get_db_dependency)):
    result = await db.execute(select(ParsedSignal))
    all_signals = result.scalars().all()
    pending_count = len([s for s in all_signals if s.status == SignalStatus.PENDING.value])
    approved_count = len([s for s in all_signals if s.status == SignalStatus.APPROVED.value])
    executed_count = len([s for s in all_signals if s.status == SignalStatus.EXECUTED.value])

    today = date.today()
    today_start = datetime.combine(today, datetime.min.time())
    daily_result = await db.execute(
        select(TradeLog).where(TradeLog.executed_at >= today_start)
    )
    daily_trades = len(daily_result.scalars().all())

    daily_pnl = await get_daily_pnl()
    consec = await get_consecutive_losses()

    return {
        "signals": {
            "pending_approval": pending_count,
            "approved": approved_count,
            "executed": executed_count,
        },
        "trading": {
            "daily_trades": daily_trades,
            "daily_pnl": daily_pnl,
            "consecutive_losses": consec,
            "max_daily_loss_pct": CONFIG.max_daily_loss_percent,
            "max_consecutive_losses": CONFIG.max_consecutive_losses,
            "daily_profit_target_pct": CONFIG.daily_profit_target_percent,
            "trading_halted": dispatch_circuit_open(),
        },
    }

_last_prices: dict = {}
_last_price_fetch: float = 0
_PRICE_CACHE_SEC = 10


@app.get("/market/live")
def market_live():
    """Return latest prices for configured symbols, cached 10s."""
    global _last_prices, _last_price_fetch
    now = time.monotonic()
    if now - _last_price_fetch < _PRICE_CACHE_SEC and _last_prices:
        return _last_prices

    try:
        from .strategies.market_data import fetch_market_data
        prices = {}
        symbols = ["XAUUSD", "SP500", "NAS100"]
        for sym in symbols:
            snap = fetch_market_data(sym, count=2)
            if snap and snap.closes and len(snap.closes) >= 2:
                price = snap.closes[-1]
                prev = snap.closes[-2]
                prices[sym] = {
                    "price": round(price, 2),
                    "change": round(price - prev, 2),
                    "decimals": 2,
                }
        _last_prices = prices
        _last_price_fetch = now
        return prices
    except Exception as e:
        logger.warning("market/live fetch failed: %s", e)
        return {}


@app.get("/signals/pending")
async def list_pending(db: AsyncSession = Depends(get_db_dependency)):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    result = await db.execute(
        select(ParsedSignal)
        .where(ParsedSignal.status == SignalStatus.PENDING.value)
        .order_by(ParsedSignal.parsed_at.desc())
    )
    pending = result.scalars().all()
    pending = [p for p in pending if is_supported_symbol(p.symbol)]
    return {
        "count": len(pending),
        "signals": [
            {
                "id": p.id,
                "action": p.action,
                "symbol": p.symbol,
                "entry": p.entry_price,
                "sl": p.sl,
                "tp": p.tp,
                "tp2": p.tp2,
                "tp3": p.tp3,
                "age_minutes": round((now - p.parsed_at).total_seconds() / 60.0, 1) if p.parsed_at else None,
                "expires_in_minutes": round(_SIGNAL_MAX_AGE_MINUTES - ((now - p.parsed_at).total_seconds() / 60.0), 1) if p.parsed_at else None,
                "raw_text": p.raw_text[:120] if p.raw_text else "",
                "parsed_at": p.parsed_at.isoformat() if p.parsed_at else None,
            }
            for p in pending
        ]
    }


@app.get("/signals/{signal_id}")
async def get_signal(signal_id: int, db: AsyncSession = Depends(get_db_dependency)):
    result = await db.execute(select(ParsedSignal).where(ParsedSignal.id == signal_id))
    signal = result.scalar_one_or_none()
    if not signal:
        raise HTTPException(status_code=404, detail="Signal not found")
    return {
        "id": signal.id,
        "action": signal.action,
        "symbol": signal.symbol,
        "entry": signal.entry_price,
        "sl": signal.sl,
        "tp": signal.tp,
        "tp2": signal.tp2,
        "tp3": signal.tp3,
        "status": signal.status,
        "lot_size": signal.lot_size,
        "raw_text": signal.raw_text,
        "parsed_at": signal.parsed_at.isoformat() if signal.parsed_at else None,
    }


@app.post("/signals/{signal_id}/approve")
async def approve_signal(
    signal_id: int,
    account_balance: Optional[float] = None,
    db: AsyncSession = Depends(get_db_dependency),
):
    return await approve_signal_by_id(signal_id, account_balance, db)


@app.post("/signals/{signal_id}/reject")
async def reject_signal(
    signal_id: int,
    reason: Optional[str] = None,
    db: AsyncSession = Depends(get_db_dependency),
):
    return await reject_signal_by_id(signal_id, reason, db)


@app.post("/signals/approve-all")
async def approve_all(
    account_balance: Optional[float] = None,
    db: AsyncSession = Depends(get_db_dependency),
):
    global _approve_all_last_at
    now = time.monotonic()
    if now - _approve_all_last_at < _APPROVE_ALL_COOLDOWN_SEC:
        raise HTTPException(
            status_code=429,
            detail=f"approve-all rate limited; wait {_APPROVE_ALL_COOLDOWN_SEC:.0f}s",
        )
    _approve_all_last_at = now

    result = await db.execute(
        select(ParsedSignal).where(ParsedSignal.status == SignalStatus.PENDING.value)
    )
    pending = result.scalars().all()
    results = []
    for signal in pending:
        try:
            result = await approve_signal(signal.id, account_balance, db)
            results.append({"signal_id": signal.id, "result": result})
        except Exception as e:
            results.append({"signal_id": signal.id, "error": str(e)})
    approved = len([r for r in results if r.get("result", {}).get("status") == "approved"])
    return {"approved": approved, "results": results}


@app.get("/trades")
async def list_trades(limit: int = 20, offset: int = 0, status: Optional[str] = None, db: AsyncSession = Depends(get_db_dependency)):
    query = select(TradeLog).order_by(TradeLog.executed_at.desc())
    if status:
        query = query.where(TradeLog.result == status)
    total_result = await db.execute(select(TradeLog))
    total = len(total_result.scalars().all())
    paged = await db.execute(query.offset(offset).limit(limit))
    trades = paged.scalars().all()
    return {
        "total": total,
        "count": len(trades),
        "offset": offset,
        "limit": limit,
        "trades": [
            {
                "id": t.id,
                "symbol": t.symbol,
                "action": t.action,
                "lot": t.lot_size,
                "result": t.result,
                "pnl": t.pnl,
                "executed_at": t.executed_at.isoformat() if t.executed_at else None,
                "error": t.error_message,
            }
            for t in trades
        ]
    }


@app.post("/trades/{trade_id}/feedback")
async def trade_feedback(trade_id: int, pnl: float, status: str, db: AsyncSession = Depends(get_db_dependency)):
    result = await db.execute(select(TradeLog).where(TradeLog.id == trade_id))
    trade = result.scalar_one_or_none()
    if not trade:
        raise HTTPException(status_code=404, detail="Not found")
    trade.pnl = pnl
    trade.result = status
    await db.commit()
    logger.info(f"Trade {trade_id} feedback: PnL=${pnl:.2f}")
    return {"status": "updated"}


# =============================================================================
# BROKER ENDPOINTS
# =============================================================================


@app.get("/broker/status")
async def broker_status():
    """List available brokers and show active broker connection state."""
    from .brokers import list_available, get_active

    available = list_available()
    active = get_active()
    balance = None
    if active and active.is_connected:
        try:
            balance = await active.get_balance()
        except Exception as exc:
            logger.warning("Broker balance fetch failed: %s", exc)

    return {
        "available": available,
        "active": active.name if active else None,
        "connected": active.is_connected if active else False,
        "balance": balance,
    }


@app.post("/broker/switch")
async def broker_switch(name: str = ""):
    """Switch to a different broker. Pass empty string to disconnect."""
    from .brokers import switch_broker

    target = name.strip().lower() or None
    try:
        instance = await switch_broker(target)
        return {
            "status": "ok",
            "active": instance.name if instance else None,
            "connected": instance.is_connected if instance else False,
        }
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


@app.post("/broker/connect")
async def broker_reconnect():
    """Reconnect the current (or default) broker."""
    from .brokers import get_active, switch_broker, list_available
    from .approval_service import reconcile_approved_signals

    current = get_active()
    if current is None:
        available = list_available()
        if not available:
            raise HTTPException(status_code=400, detail="No brokers registered")
        # try first available
        instance = await switch_broker(next(iter(available)))
    else:
        instance = await switch_broker(current.name)

    # On (re)connect, re-dispatch any APPROVED-but-unexecuted signals whose
    # broker was previously offline, so they don't stay orphaned forever.
    reconciled = 0
    if instance and instance.is_connected:
        async with get_db() as db:
            reconciled = await reconcile_approved_signals(db)

    return {
        "status": "ok",
        "active": instance.name if instance else None,
        "connected": instance.is_connected if instance else False,
        "reconciled": reconciled,
    }


@app.post("/broker/reset-circuit")
async def broker_reset_circuit():
    """Manually reset the dispatch circuit breaker (dashboard 'Resume trading').

    Use after investigating a tripped breaker (N consecutive broker dispatch
    failures). Execution remains blocked until this is called.
    """
    reset_dispatch_circuit()
    return {"status": "ok", "trading_halted": dispatch_circuit_open()}


# =============================================================================
# STRATEGY ENGINE ENDPOINTS
# =============================================================================


_strategy_poller = None

_strategy_engine_instance = None


def _get_strategy_engine():
    """Lazy-init and return the strategy engine singleton."""
    global _strategy_engine_instance
    if _strategy_engine_instance is None:
        from .strategies.engine import QuadaptEngine
        _strategy_engine_instance = QuadaptEngine()
    return _strategy_engine_instance


@app.get("/strategy/status")
def strategy_status():
    """Returns current strategy engine configuration and state."""
    engine = _get_strategy_engine()
    cfg = engine.cfg
    return {
        "enabled": cfg.enabled,
        "name": cfg.name,
        "symbols": cfg.market_data.symbols,
        "signal_mode": cfg.envelopes.signal_mode,
        "quality_threshold": cfg.quality.min_quality_score,
        "sl_method": cfg.risk.sl_method,
        "tp_method": cfg.risk.tp_method,
        "mlma_enabled": cfg.mlma.enabled,
        "supertrend_enabled": cfg.supertrend.enabled,
        "stoch_rsi_enabled": cfg.stoch_rsi.enabled,
        "squeeze_enabled": cfg.ttm.enabled,
        "order_blocks_enabled": cfg.order_blocks.enabled,
        "poll_interval_seconds": cfg.market_data.poll_interval_seconds,
    }


@app.post("/strategy/poll")
async def strategy_poll():
    """Force a one-shot strategy evaluation cycle."""
    engine = _get_strategy_engine()
    signals = await asyncio.to_thread(engine.run_poll)
    return {
        "count": len(signals),
        "signals": [s.to_dict() for s in signals],
    }


@app.get("/strategy/last-signals")
async def strategy_last_signals(limit: int = 20, db: AsyncSession = Depends(get_db_dependency)):
    """Return the most recent strategy-generated signals from the live DB.

    Replaces the old ML-data-store reader (training_data.jsonl), which no
    longer exists — the ParsedSignal table is now the single source of truth
    for every signal that reached the pipeline.
    """
    from .models import SignalStatus

    result = await db.execute(
        select(ParsedSignal)
        .where(ParsedSignal.strategy_generated_at.isnot(None))
        .order_by(ParsedSignal.parsed_at.desc())
        .limit(limit)
    )
    rows = result.scalars().all()
    signals = [
        {
            "id": r.id,
            "symbol": r.symbol,
            "action": r.action,
            "entry": r.entry_price,
            "sl": r.sl,
            "tp": r.tp,
            "status": r.status,
            "strategy_generated_at": r.strategy_generated_at,
        }
        for r in rows
    ]
    return {"count": len(signals), "signals": signals}


@app.post("/strategy/toggle")
def strategy_toggle(paused: bool = True):
    """Pause or resume the strategy polling loop."""
    global _strategy_poller
    poller = _strategy_poller
    if poller is None:
        raise HTTPException(status_code=503, detail="Strategy poller not initialized")
    if paused:
        poller.pause()
    else:
        poller.resume()
    return {"paused": poller.paused}


@app.post("/strategy/config")
def strategy_config(
    quality_threshold: Optional[float] = None,
    momentum_enabled: Optional[bool] = None,
    trend_gate_enabled: Optional[bool] = None,
    trigger_mode: Optional[str] = None,
):
    """Update strategy configuration at runtime."""
    from .strategies.config import QUADAPT_CFG

    updated = []
    if quality_threshold is not None:
        if not 0 <= quality_threshold <= 100:
            raise HTTPException(status_code=400, detail="quality_threshold must be 0-100")
        QUADAPT_CFG.quality.min_quality_score = quality_threshold
        updated.append("quality_threshold")
    if momentum_enabled is not None:
        QUADAPT_CFG.momentum.enabled = momentum_enabled
        updated.append("momentum_enabled")
    if trend_gate_enabled is not None:
        QUADAPT_CFG.trend_gate.enabled = trend_gate_enabled
        updated.append("trend_gate_enabled")
    if trigger_mode is not None:
        if trigger_mode not in ("mean_reversion", "liquidity_sweep"):
            raise HTTPException(
                status_code=400,
                detail="trigger_mode must be 'mean_reversion' or 'liquidity_sweep'",
            )
        QUADAPT_CFG.trigger.mode = trigger_mode
        updated.append("trigger_mode")
    logger.info(f"Strategy config updated: {updated}")
    return {"updated": updated}


@app.get("/strategy/history")
async def strategy_history(db: AsyncSession = Depends(get_db_dependency)):
    """Return aggregated trade history with equity curve + per-symbol breakdown."""
    from collections import defaultdict

    result = await db.execute(select(TradeLog).order_by(TradeLog.executed_at.asc()))
    trades = result.scalars().all()
    total_trades = len(trades)
    winning_trades = len([t for t in trades if t.result == "win"])
    losing_trades = len([t for t in trades if t.result == "loss"])
    win_rate = round(winning_trades / total_trades, 3) if total_trades > 0 else 0.0
    total_pnl = sum((t.pnl or 0.0) for t in trades)

    daily: dict = defaultdict(lambda: {"pnl": 0.0, "trades": 0})
    symbol_stats: dict = defaultdict(lambda: {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0})
    for t in trades:
        day = t.executed_at.date().isoformat() if t.executed_at else "unknown"
        daily[day]["pnl"] += t.pnl or 0.0
        daily[day]["trades"] += 1
        sym = t.symbol
        symbol_stats[sym]["trades"] += 1
        symbol_stats[sym]["pnl"] += t.pnl or 0.0
        if t.result == "win":
            symbol_stats[sym]["wins"] += 1
        elif t.result == "loss":
            symbol_stats[sym]["losses"] += 1

    equity_curve = []
    cumulative = 0.0
    best_day_val = -float("inf")
    worst_day_val = float("inf")
    for day in sorted(daily.keys()):
        cumulative += daily[day]["pnl"]
        day_pnl = daily[day]["pnl"]
        if day_pnl > best_day_val:
            best_day_val = day_pnl
        if day_pnl < worst_day_val:
            worst_day_val = day_pnl
        equity_curve.append({
            "date": day,
            "pnl": round(day_pnl, 2),
            "cumulative": round(cumulative, 2),
            "trades": daily[day]["trades"],
        })

    per_symbol = {
        sym: {
            "trades": st["trades"],
            "wins": st["wins"],
            "losses": st["losses"],
            "pnl": round(st["pnl"], 2),
            "win_rate": round(st["wins"] / st["trades"], 3) if st["trades"] > 0 else 0.0,
        }
        for sym, st in sorted(symbol_stats.items())
    }

    return {
        "total_trades": total_trades,
        "winning_trades": winning_trades,
        "losing_trades": losing_trades,
        "win_rate": win_rate,
        "total_pnl": round(total_pnl, 2),
        "best_day": round(best_day_val, 2) if best_day_val != -float("inf") else None,
        "worst_day": round(worst_day_val, 2) if worst_day_val != float("inf") else None,
        "per_symbol": per_symbol,
        "equity_curve": equity_curve,
    }


# =============================================================================
# PERFORMANCE ENDPOINTS
# =============================================================================


@app.get("/performance/per-symbol")
async def performance_per_symbol(db: AsyncSession = Depends(get_db_dependency)):
    """Return per-symbol P&L breakdown."""
    from collections import defaultdict

    result = await db.execute(select(TradeLog).order_by(TradeLog.executed_at.asc()))
    trades = result.scalars().all()
    stats: dict = defaultdict(lambda: {"trades": 0, "wins": 0, "losses": 0, "pnl": 0.0, "avg_pnl": 0.0})
    for t in trades:
        sym = t.symbol
        stats[sym]["trades"] += 1
        stats[sym]["pnl"] += t.pnl or 0.0
        if t.result == "win":
            stats[sym]["wins"] += 1
        elif t.result == "loss":
            stats[sym]["losses"] += 1
    result = {}
    for sym, st in sorted(stats.items()):
        result[sym] = {
            "trades": st["trades"],
            "wins": st["wins"],
            "losses": st["losses"],
            "pnl": round(st["pnl"], 2),
            "win_rate": round(st["wins"] / st["trades"], 3) if st["trades"] > 0 else 0.0,
            "avg_pnl": round(st["pnl"] / st["trades"], 2) if st["trades"] > 0 else 0.0,
        }
    return result


# =============================================================================
# LOGS ENDPOINT
# =============================================================================


@app.get("/logs/latest")
def logs_latest(lines: int = 50):
    """Return last N lines from the latest log file."""
    import glob
    log_dir = Path("logs")
    if not log_dir.exists():
        return {"lines": [], "total": 0}
    files = sorted(log_dir.glob("*.log"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        files = sorted(log_dir.glob("*.jsonl"), key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return {"lines": [], "total": 0}
    # Tail the latest file
    with open(files[0], errors="replace") as f:
        all_lines = f.readlines()
    tail = [l.rstrip("\n\r") for l in all_lines[-lines:]]
    return {"file": files[0].name, "lines": tail, "total": len(tail)}


# =============================================================================
# TRADINGVIEW WEBHOOK - Receive Pine Script alerts

@app.post("/webhook/tradingview")
async def tradingview_webhook(payload: dict):
    """Receive Pine Script alert from TradingView webhook.

    Pine Script format (customize in alert() call):
    {
      "symbol": "XAUUSD",
      "action": "BUY",
      "price": 1950.50,
      "sl": 1940.00,
      "tp": 1970.00,
      "passphrase": "...",        # optional auth
      "tp2": 1985.00,             # optional
      "tp3": 2000.00,             # optional
    }
    """

    # Auth: a webhook secret MUST be configured. Without it the endpoint is
    # open to anyone who can reach the port, so we fail CLOSED (reject) rather
    # than silently accepting unsigned signals. Set WEBHOOK_SECRET to enable.
    webhook_secret = CONFIG.webhook_secret
    if not webhook_secret:
        logger.warning("Webhook rejected: WEBHOOK_SECRET not configured")
        raise HTTPException(
            status_code=503,
            detail="Webhook disabled: set WEBHOOK_SECRET to enable",
        )
    provided = payload.get("passphrase") or payload.get("secret") or ""
    if provided != webhook_secret:
        logger.warning("Webhook auth failed (bad passphrase)")
        raise HTTPException(status_code=403, detail="Invalid passphrase")

    # Normalise ticker (OANDA:XAUUSD → XAUUSD, NASDAQ:AAPL → AAPL)
    symbol = payload.get("symbol") or payload.get("ticker") or ""
    symbol = re.sub(r'^[A-Z]+:', '', symbol).strip().upper()

    # Normalise action
    action = (payload.get("action") or payload.get("side") or payload.get("signal") or "").strip().upper()
    action_map = {"BUY": "BUY", "LONG": "BUY", "B": "BUY",
                  "SELL": "SELL", "SHORT": "SELL", "S": "SELL"}
    action = action_map.get(action, "BUY" if action in ("", "BUY", "LONG") else "SELL")

    try:
        entry_price = float(payload.get("price") or payload.get("entry") or payload.get("entry_price") or 0)
    except (TypeError, ValueError):
        entry_price = 0.0

    def _f(k, *aliases):
        for a in [k] + list(aliases):
            try:
                v = payload.get(a)
                if v is not None and v != 0:
                    return float(v)
            except (TypeError, ValueError):
                pass
        return None

    sl = _f("sl", "stoploss")
    tp = _f("tp", "takeprofit")
    tp2 = _f("tp2", "tp_2", "take_profit_2")
    tp3 = _f("tp3", "tp_3", "take_profit_3")

    if not symbol or not entry_price:
        raise HTTPException(status_code=400,
                            detail="Missing required fields: symbol, price")
    if not is_supported_symbol(symbol):
        raise HTTPException(status_code=400,
                            detail=f"Unsupported symbol: {symbol}")

    async with get_db() as db:
        rec = ParsedSignal(
            action=action,
            symbol=symbol,
            entry_price=entry_price,
            sl=sl,
            tp=tp,
            tp2=tp2,
            tp3=tp3,
            raw_text=json.dumps(payload),
            parsed_at=datetime.now(timezone.utc).replace(tzinfo=None),
            status=SignalStatus.PENDING.value,
            ai_score=1.0,  # Trust Pine Script signals
            lot_size=0.0,
            risk_percent=CONFIG.default_risk_percent,
        )
        db.add(rec)
        await db.commit()
        await db.refresh(rec)
        logger.info(f"TradingView → signal {rec.id}: {action} {symbol} @ {entry_price}")
        return {"status": "received", "signal_id": rec.id, "symbol": symbol, "action": action, "price": entry_price}


# =============================================================================
# WEB DASHBOARD - Static files + SPA fallback
# =============================================================================

# Mount static files (CSS, JS)
app.mount("/static", StaticFiles(directory="web_dashboard/static"), name="static")

# Serve index.html for root
dashboard_path = Path("web_dashboard/index.html")

@app.get("/", response_class=HTMLResponse)
def dashboard_root():
    if dashboard_path.exists():
        return HTMLResponse(content=dashboard_path.read_text(encoding="utf-8"))
    return HTMLResponse(content="<h1>Bunga Trader</h1><p>Dashboard not found.</p>")


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page():
    return dashboard_root()


# SPA fallback: catch-all for dashboard routes (must be LAST)
@app.get("/{path:path}")
def spa_catchall(path: str):
    """Serve index.html for any unmatched route (SPA behavior)."""
    # Skip API paths
    if path.startswith("api/") or path.startswith("mobile/"):
        raise HTTPException(status_code=404, detail="Not found")
    if dashboard_path.exists():
        return HTMLResponse(content=dashboard_path.read_text(encoding="utf-8"))
    raise HTTPException(status_code=404, detail="Not found")
