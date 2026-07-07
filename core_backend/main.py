"""
Bunga Trader - FastAPI Main Application v2
REST API + WebSocket + Web Dashboard + Mobile API + LLM fallback
"""
import asyncio
import time
from contextlib import asynccontextmanager
from datetime import datetime, date
from typing import Optional
from pathlib import Path

from fastapi import FastAPI, Depends, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy.orm import Session
from sqlalchemy import text

from .database import engine, get_db_dependency, get_db
from .models import Base, RawSignal, ParsedSignal, SignalStatus, TradeLog
from .parser import process_unparsed_signals
from .approval_service import approve_signal_by_id, reject_signal_by_id
from .risk_engine import get_daily_pnl, get_consecutive_losses, check_daily_limits, get_daily_trade_count
from .trade_dispatcher import manager
from .config import CONFIG
from .logger import setup_logger
from .llm_providers.manager import llm_manager
from .auth import require_api_key
from .ws_feedback import process_trade_feedback_message
from .symbols import is_supported_symbol
from .sources.strategy_source import StrategyPoller

logger = setup_logger("MainAPI")

_approve_all_last_at: float = 0.0
_APPROVE_ALL_COOLDOWN_SEC = 60.0
_SIGNAL_MAX_AGE_MINUTES = CONFIG.signal_max_age_minutes

Base.metadata.create_all(bind=engine)


async def parsing_loop():
    logger.info("Parser background task started")
    while True:
        try:
            count = process_unparsed_signals()
            if count > 0:
                logger.info(f"Parsed {count} new signals")
        except Exception as e:
            logger.error(f"Parser loop error: {e}")
        await asyncio.sleep(3)


async def cleanup_loop():
    logger.info("Cleanup background task started")
    while True:
        try:
            with get_db() as db:
                db.execute(text("""
                    DELETE FROM raw_signals 
                    WHERE processed != 0 
                    AND timestamp < datetime('now', '-7 days')
                """))
                db.commit()
        except Exception as e:
            logger.error(f"Cleanup error: {e}")
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Bunga Trader API v2 starting...")
    strategy_poller = StrategyPoller()
    tasks = [
        asyncio.create_task(parsing_loop()),
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

# Include mobile API routes
from .mobile_api.routes import router as mobile_router
app.include_router(mobile_router)


# =============================================================================
# API ROUTES (must come BEFORE static files)
# =============================================================================

@app.get("/health")
def health_check():
    return {"status": "healthy", "timestamp": datetime.utcnow().isoformat(), "version": "2.0.0"}


@app.get("/status")
async def system_status(db: Session = Depends(get_db_dependency)):
    raw_count = db.query(RawSignal).filter(RawSignal.processed == 0).count()
    all_signals = db.query(ParsedSignal).all()
    pending_count = len([s for s in all_signals if s.status == SignalStatus.PENDING.value])
    approved_count = len([s for s in all_signals if s.status == SignalStatus.APPROVED.value])
    executed_count = len([s for s in all_signals if s.status == SignalStatus.EXECUTED.value])

    today = date.today()
    today_start = datetime.combine(today, datetime.min.time())
    daily_trades = db.query(TradeLog).filter(TradeLog.executed_at >= today_start).count()

    bridge_status = await manager.get_status()

    return {
        "signals": {
            "raw_unprocessed": raw_count,
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
            "requires_manual_approval": True,
        },
        "bridge": bridge_status,
    }


@app.get("/llm/status")
def llm_status():
    return {
        "providers": llm_manager.get_status(),
        "best_available": llm_manager.get_best_available(),
    }


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await manager.connect(websocket)
    try:
        while True:
            try:
                data = await asyncio.wait_for(websocket.receive_text(), timeout=30.0)
                if process_trade_feedback_message(data):
                    continue
                logger.debug(f"WS message: {data}")
            except asyncio.TimeoutError:
                try:
                    await websocket.send_text('{"type":"ping"}')
                except:
                    break
    except WebSocketDisconnect:
        logger.info("Bridge disconnected")
    except Exception as e:
        logger.error(f"WS error: {e}")
    finally:
        await manager.disconnect(websocket)


@app.get("/signals/pending")
def list_pending(db: Session = Depends(get_db_dependency)):
    now = datetime.utcnow()
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
    _: None = Depends(require_api_key),
):
    return await approve_signal_by_id(signal_id, account_balance, db)


@app.post("/signals/{signal_id}/reject")
def reject_signal(
    signal_id: int,
    reason: Optional[str] = None,
    db: Session = Depends(get_db_dependency),
    _: None = Depends(require_api_key),
):
    return reject_signal_by_id(signal_id, reason, db)


@app.post("/signals/approve-all")
async def approve_all(
    account_balance: Optional[float] = None,
    db: Session = Depends(get_db_dependency),
    _: None = Depends(require_api_key),
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
def list_trades(limit: int = 50, status: Optional[str] = None, db: Session = Depends(get_db_dependency)):
    query = db.query(TradeLog).order_by(TradeLog.executed_at.desc())
    if status:
        query = query.filter(TradeLog.result == status)
    trades = query.limit(limit).all()
    return {
        "count": len(trades),
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
    return {"status": "updated"}


# =============================================================================
# STRATEGY ENGINE ENDPOINTS
# =============================================================================


_strategy_scheduler = None


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
