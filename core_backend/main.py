"""
Bunga Trader - FastAPI Main Application v2
REST API + Web Dashboard
"""
import asyncio
import json
import re
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone, date
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import text

from .database import engine, get_db_dependency, get_db, apply_migrations
from .models import Base, ParsedSignal, SignalStatus, TradeLog
from .approval_service import approve_signal_by_id, reject_signal_by_id
from .risk_engine import get_daily_pnl, get_consecutive_losses
from .config import CONFIG
from .logger import setup_logger
from .symbols import is_supported_symbol
from .sources.strategy_source import StrategyPoller

logger = setup_logger("MainAPI")

_approve_all_last_at: float = 0.0
_APPROVE_ALL_COOLDOWN_SEC = 60.0
_SIGNAL_MAX_AGE_MINUTES = CONFIG.signal_max_age_minutes

Base.metadata.create_all(bind=engine)
apply_migrations()


async def cleanup_loop():
    logger.info("Cleanup background task started")
    while True:
        try:
            with get_db() as db:
                from .models import ParsedSignal
                # Clean old executed/rejected signals (>7 days)
                db.execute(text("""
                    DELETE FROM parsed_signals 
                    WHERE status != 'pending' 
                    AND parsed_at < datetime('now', '-7 days')
                """))
                # Clean expired pending signals (older than max age)
                max_age = CONFIG.signal_max_age_minutes
                db.execute(text(f"""
                    DELETE FROM parsed_signals 
                    WHERE status = 'pending' 
                    AND parsed_at < datetime('now', '-{max_age} minutes')
                """))
                db.commit()
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Bunga Trader API v2 starting...")
    # Purge expired pending signals immediately on startup
    try:
        with get_db() as db:
            from .models import ParsedSignal
            max_age = CONFIG.signal_max_age_minutes
            db.execute(text(f"""
                DELETE FROM parsed_signals 
                WHERE status = 'pending' 
                AND parsed_at < datetime('now', '-{max_age} minutes')
            """))
            db.commit()
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

# Include mobile API routes — DISABLED until we have a mobile client
# from .mobile_api.routes import router as mobile_router
# app.include_router(mobile_router)


# =============================================================================
# API ROUTES (must come BEFORE static files)
# =============================================================================

@app.get("/health")
def health_check():
    return {"status": "healthy", "timestamp": datetime.now(timezone.utc).replace(tzinfo=None).isoformat(), "version": "2.0.0"}


@app.get("/status")
def system_status(db: Session = Depends(get_db_dependency)):
    all_signals = db.query(ParsedSignal).all()
    pending_count = len([s for s in all_signals if s.status == SignalStatus.PENDING.value])
    approved_count = len([s for s in all_signals if s.status == SignalStatus.APPROVED.value])
    executed_count = len([s for s in all_signals if s.status == SignalStatus.EXECUTED.value])

    today = date.today()
    today_start = datetime.combine(today, datetime.min.time())
    daily_trades = db.query(TradeLog).filter(TradeLog.executed_at >= today_start).count()

    return {
        "signals": {
            "pending_approval": pending_count,
            "approved": approved_count,
            "executed": executed_count,
        },
        "trading": {
            "daily_trades": daily_trades,
            "daily_pnl": get_daily_pnl(),
            "consecutive_losses": get_consecutive_losses(),
            "max_daily_loss_pct": CONFIG.max_daily_loss_percent,
            "max_consecutive_losses": CONFIG.max_consecutive_losses,
            "daily_profit_target_pct": CONFIG.daily_profit_target_percent,
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
def list_pending(db: Session = Depends(get_db_dependency)):
    now = datetime.now(timezone.utc).replace(tzinfo=None)
    pending = (
        db.query(ParsedSignal)
        .filter(ParsedSignal.status == SignalStatus.PENDING.value)
        .order_by(ParsedSignal.parsed_at.desc())
        .all()
    )
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
def get_signal(signal_id: int, db: Session = Depends(get_db_dependency)):
    signal = db.query(ParsedSignal).filter(ParsedSignal.id == signal_id).first()
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
    db: Session = Depends(get_db_dependency),
):
    return await approve_signal_by_id(signal_id, account_balance, db)


@app.post("/signals/{signal_id}/reject")
def reject_signal(
    signal_id: int,
    reason: Optional[str] = None,
    db: Session = Depends(get_db_dependency),
):
    return reject_signal_by_id(signal_id, reason, db)


@app.post("/signals/approve-all")
async def approve_all(
    account_balance: Optional[float] = None,
    db: Session = Depends(get_db_dependency),
):
    global _approve_all_last_at
    now = time.monotonic()
    if now - _approve_all_last_at < _APPROVE_ALL_COOLDOWN_SEC:
        raise HTTPException(
            status_code=429,
            detail=f"approve-all rate limited; wait {_APPROVE_ALL_COOLDOWN_SEC:.0f}s",
        )
    _approve_all_last_at = now

    pending = db.query(ParsedSignal).filter(ParsedSignal.status == SignalStatus.PENDING.value).all()
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
def list_trades(limit: int = 20, offset: int = 0, status: Optional[str] = None, db: Session = Depends(get_db_dependency)):
    query = db.query(TradeLog).order_by(TradeLog.executed_at.desc())
    if status:
        query = query.filter(TradeLog.result == status)
    total = query.count()
    trades = query.offset(offset).limit(limit).all()
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
def trade_feedback(trade_id: int, pnl: float, status: str, db: Session = Depends(get_db_dependency)):
    trade = db.query(TradeLog).filter(TradeLog.id == trade_id).first()
    if not trade:
        raise HTTPException(status_code=404, detail="Not found")
    trade.pnl = pnl
    trade.result = status
    db.commit()
    logger.info(f"Trade {trade_id} feedback: PnL=${pnl:.2f}")

    # Backfill ML training data with outcome
    from .strategies.engine import log_trade_outcome
    try:
        log_trade_outcome(trade.symbol, trade.executed_at, status, pnl or 0.0)
    except Exception as e:
        logger.warning(f"ML outcome logging failed: {e}")

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

    current = get_active()
    if current is None:
        available = list_available()
        if not available:
            raise HTTPException(status_code=400, detail="No brokers registered")
        # try first available
        instance = await switch_broker(next(iter(available)))
    else:
        instance = await switch_broker(current.name)
    return {
        "status": "ok",
        "active": instance.name if instance else None,
        "connected": instance.is_connected if instance else False,
    }


# =============================================================================
# STRATEGY ENGINE ENDPOINTS
# =============================================================================


_strategy_scheduler = None
_strategy_poller = None


class StrategyScheduler:
    def __init__(self, poller: "StrategyPoller") -> None:
        self.poller = poller
        self._job = None

    def start(self, scheduler: "AsyncIOScheduler") -> None:
        interval = QUADAPT_CFG.market_data.poll_interval_seconds
        self._job = scheduler.add_job(
            self._run_safe,
            "interval",
            seconds=interval,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=interval,
        )
        logger.info("Automation scheduler started")

    def stop(self) -> None:
        if self._job is not None:
            self._job.remove()
            self._job = None
        logger.info("Automation scheduler stopped")

    async def _run_safe(self) -> None:
        if not QUADAPT_CFG.enabled:
            return
        try:
            await self.poller.poll_once()
        except Exception as exc:
            logger.error("Scheduler poll failed: %s", exc)


strategy_scheduler: Optional[StrategyScheduler] = None
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
        "ml_data_dir": cfg.ml_data_dir,
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
def strategy_last_signals(limit: int = 20):
    """Return raw strategy engine signals logged to ML data store."""
    from pathlib import Path
    data_dir = Path(_get_strategy_engine().cfg.ml_data_dir)
    if not data_dir.exists():
        return {"count": 0, "signals": []}
    # Read latest session file
    files = sorted(data_dir.glob("session_*.jsonl"), reverse=True)
    if not files:
        return {"count": 0, "signals": []}
    signals = []
    with open(files[0]) as f:
        for line in f:
            try:
                import json
                signals.append(json.loads(line))
                if len(signals) >= limit:
                    break
            except json.JSONDecodeError:
                continue
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
def strategy_history(db: Session = Depends(get_db_dependency)):
    """Return aggregated trade history with equity curve + per-symbol breakdown."""
    from collections import defaultdict

    trades = db.query(TradeLog).order_by(TradeLog.executed_at.asc()).all()
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
def performance_per_symbol(db: Session = Depends(get_db_dependency)):
    """Return per-symbol P&L breakdown."""
    from collections import defaultdict

    trades = db.query(TradeLog).order_by(TradeLog.executed_at.asc()).all()
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

    # Auth: validate passphrase if configured
    webhook_secret = CONFIG.webhook_secret
    if webhook_secret:
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

    db = next(get_db())
    try:
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
        db.commit()
        db.refresh(rec)
        logger.info(f"TradingView → signal {rec.id}: {action} {symbol} @ {entry_price}")
        return {"status": "received", "signal_id": rec.id, "symbol": symbol, "action": action, "price": entry_price}
    except Exception as e:
        db.rollback()
        logger.error(f"TradingView webhook failed: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        db.close()


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
