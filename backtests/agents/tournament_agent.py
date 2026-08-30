"""TournamentAgent — sharded tournament execution per symbol/timeframe."""
from __future__ import annotations

import asyncio
import glob
from pathlib import Path
from typing import Any, Dict, List

import pandas as pd

from .base import AgentResult, BaseAgent

# research modules are siblings of agents/
import sys
RESEARCH_ROOT = str(Path(__file__).resolve().parents[1])
if RESEARCH_ROOT not in sys.path:
    sys.path.insert(0, RESEARCH_ROOT)

from research_lab import Experiment, ExperimentResult, chronological_split, rank_results  # noqa: E402
from research_tournament import tournament  # noqa: E402
from strategy_interface import validate_ohlcv  # noqa: E402
from strategy_library import STRATEGIES  # noqa: E402
from tournament_config import DEFAULT_COST_MODEL, MIN_TRADES_OOS, MIN_TRADES_VALIDATION  # noqa: E402


def _load_df(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    for col in ["Datetime", "Date", "date", "Timestamp"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
            df = df.set_index(col)
            break
    df = df.sort_index()
    if "Open" not in df.columns:
        mapping = {}
        for c in df.columns:
            lc = c.lower()
            if lc == "open": mapping[c] = "Open"
            elif lc == "high": mapping[c] = "High"
            elif lc == "low": mapping[c] = "Low"
            elif lc == "close": mapping[c] = "Close"
            elif lc == "volume": mapping[c] = "Volume"
        if mapping:
            df = df.rename(columns=mapping)
    return df


def _backtest_signal(df: pd.DataFrame, signal: pd.Series, symbol: str):
    """Copied sizing logic from run_tournament.py — deterministic, look-ahead safe."""
    from strategy_library import atr
    from engine_corrected import pip_value
    import math

    n = len(df)
    a = atr(df, 14)
    pip_size = 0.01 if symbol.upper() in ("XAUUSD", "GOLD") else 1.0
    pv = pip_value(symbol)
    bal = 1000.0
    peak = bal
    trades = wins = 0
    pos = None
    equity: List[float] = []
    pnls: List[float] = []
    sig = signal.reindex(df.index).fillna(False).astype(bool)
    for i in range(200, n):
        if pos is not None:
            exit_px = None
            low = float(df["Low"].iloc[i]); high = float(df["High"].iloc[i]); close = float(df["Close"].iloc[i])
            if pos["side"] == "BUY":
                if low <= pos["sl"]:
                    exit_px = pos["sl"]
                elif high >= pos["tp"]:
                    exit_px = pos["tp"]
            else:
                if high >= pos["sl"]:
                    exit_px = pos["sl"]
                elif low <= pos["tp"]:
                    exit_px = pos["tp"]
            if exit_px is None and (i - pos["idx"]) >= 15:
                exit_px = close
            if exit_px is not None:
                is_win = (pos["side"] == "BUY" and exit_px > pos["entry"]) or (pos["side"] == "SELL" and exit_px < pos["entry"])
                points = abs(exit_px - pos["entry"]) / pip_size
                pnl = abs(points * pv * pos["lot"]) if is_win else -abs(points * pv * pos["lot"])
                bal += pnl; pnls.append(pnl); trades += 1
                if pnl > 0: wins += 1
                if bal > peak: peak = bal
                pos = None
        equity.append(bal)
        if pos is not None:
            continue
        if not sig.iloc[i]:
            continue
        atr_v = float(a.iloc[i]) if not pd.isna(a.iloc[i]) else 0
        if atr_v <= 0:
            continue
        sl_d = atr_v * 1.2; tp_d = sl_d * 2.0
        entry = float(df["Close"].iloc[i])
        sl = entry - sl_d; sl_pips = abs(entry - sl) / pip_size
        if sl_pips <= 0: continue
        lot = max(0.001, (1000 * 0.01) / (sl_pips * pv))
        pos = dict(side="BUY", entry=entry, sl=sl, tp=entry + tp_d, lot=lot, idx=i)
    max_dd = 0.0; cp = 1000.0
    for e in equity:
        if e > cp: cp = e
        dd = (cp - e) / cp * 100 if cp else 0
        if dd > max_dd: max_dd = dd
    ret = (bal / 1000 - 1) * 100
    win_pct = (wins / trades * 100) if trades else 0
    gross = sum(p for p in pnls if p > 0); loss = sum(-p for p in pnls if p < 0)
    pf = gross / loss if loss > 0 else (99.0 if gross > 0 else 0)
    avg_win = (sum(p for p in pnls if p > 0) / max(wins, 1)) if wins else 0
    avg_loss = (sum(-p for p in pnls if p < 0) / max(trades - wins, 1)) if trades > wins else 0
    expectancy = (wins / trades * avg_win - (trades - wins) / trades * avg_loss) if trades else 0
    expectancy_r = expectancy / 10.0 if trades else 0  # risk = 10 (1% of 1000)
    rets = pd.Series(equity).pct_change().dropna()
    sharpe = float(rets.mean() / rets.std() * (252 * 24) ** 0.5) if len(rets) > 2 and rets.std() != 0 else 0.0
    if pd.isna(sharpe) or abs(sharpe) == float("inf"): sharpe = 0.0
    return dict(ret_pct=ret, trades=float(trades), win_pct=win_pct, max_dd_pct=max_dd, profit_factor=float(pf),
                expectancy=float(expectancy), expectancy_r=float(expectancy_r), sharpe=float(sharpe),
                final_equity=bal, equity=equity, pnls=pnls, wins=wins)


class TournamentAgent(BaseAgent):
    """Runs tournament sharded by file; orchestrator can spawn N of these."""

    def __init__(self, cache_glob: str = "data/market_cache/yf_*.csv", symbols: List[str] | None = None, **kw):
        super().__init__("tournament", kw)
        self.cache_glob = cache_glob
        self.symbols = set(symbols) if symbols else None

    async def run(self) -> AgentResult:
        # offload CPU work to thread so orchestrator stays responsive
        return await asyncio.to_thread(self._run_sync)

    def _run_sync(self) -> AgentResult:
        files = sorted(glob.glob(self.cache_glob))
        if self.symbols:
            files = [f for f in files if any(s in f for s in self.symbols)]
        if not files:
            return self._fail(f"no cache files matching {self.cache_glob}")

        results: List[ExperimentResult] = []
        curves: Dict[str, pd.Series] = {}
        per_file: List[Dict[str, Any]] = []

        for f in files:
            p = Path(f)
            parts = p.stem.split("_")
            if len(parts) < 3: continue
            symbol, timeframe = parts[1], parts[2]
            try:
                df = validate_ohlcv(_load_df(p))
            except Exception as e:
                per_file.append({"file": f, "error": str(e)})
                continue
            if len(df) < 300:
                per_file.append({"file": f, "error": f"too short {len(df)}"})
                continue
            split = chronological_split(df)
            for strat_id, fn in STRATEGIES.items():
                try:
                    train_sig, val_sig, oos_sig = fn(split.train), fn(split.validation), fn(split.test)
                except Exception as e:
                    continue
                train_m = _backtest_signal(split.train, train_sig, symbol)
                val_m = _backtest_signal(split.validation, val_sig, symbol)
                oos_m = _backtest_signal(split.test, oos_sig, symbol)
                status = "VALIDATION_CANDIDATE"
                if val_m["trades"] < MIN_TRADES_VALIDATION:
                    status = "VALIDATION_REJECT"
                metrics = {
                    **{f"train_{k}": v for k, v in train_m.items() if k not in ("equity", "pnls", "wins")},
                    **{f"validation_{k}": v for k, v in val_m.items() if k not in ("equity", "pnls", "wins")},
                    **{f"oos_{k}": v for k, v in oos_m.items() if k not in ("equity", "pnls", "wins")},
                    "profit_factor": float(val_m["profit_factor"]),
                    "expectancy_r": float(val_m["expectancy_r"]),
                    "sharpe": float(val_m["sharpe"]),
                    "max_drawdown_pct": float(val_m["max_dd_pct"]),
                    "trades": float(val_m["trades"]),
                    "oos_profit_factor": float(oos_m["profit_factor"]),
                    "oos_expectancy_r": float(oos_m["expectancy_r"]),
                    "oos_trades": float(oos_m["trades"]),
                    "complexity": 1.0,
                }
                if status == "VALIDATION_CANDIDATE":
                    if oos_m["trades"] >= MIN_TRADES_OOS and oos_m["profit_factor"] > 1.0 and oos_m["expectancy_r"] > 0:
                        status = "OOS_PASS"
                    else:
                        status = "OOS_FAIL"
                exp = Experiment(f"{strat_id}-v1-{symbol}-{timeframe}", strat_id, "1.0", symbol, timeframe, {}, hypothesis=f"{strat_id} on {symbol} {timeframe}")
                results.append(ExperimentResult(exp, metrics, status))
                curves[f"{strat_id}:{symbol}:{timeframe}"] = pd.Series(val_m["equity"])
            per_file.append({"file": f, "symbol": symbol, "timeframe": timeframe, "bars": len(df)})

        ranked = rank_results(results)
        entries = tournament(results)
        eligible = [r for r in results if r.status == "OOS_PASS"]
        return self._ok(
            metrics={"total": len(results), "oos_pass": len(eligible), "files": len(files)},
            artifacts={"results": results, "ranked": ranked, "entries": entries, "curves": curves, "per_file": per_file},
            notes=[f"tournament {len(results)} exps, {len(eligible)} OOS_PASS"],
        )
