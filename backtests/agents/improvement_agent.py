"""ImprovementAgent — parameter / combination improvement loop."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Callable, Dict, List

import pandas as pd

from .base import AgentResult, BaseAgent

import sys
RESEARCH_ROOT = str(Path(__file__).resolve().parents[1])
if RESEARCH_ROOT not in sys.path:
    sys.path.insert(0, RESEARCH_ROOT)

from research_lab import Experiment, chronological_split, parameter_stability, score_result  # noqa: E402
from research_runner import freeze_oos_candidate, run_parameter_research, select_validation_candidates  # noqa: E402
from strategy_interface import validate_ohlcv  # noqa: E402
from tournament_config import MAX_PARAMETER_CV  # noqa: E402


def _load_df(path: Path) -> pd.DataFrame:
    import pandas as pd
    df = pd.read_csv(path)
    for col in ["Datetime", "Date"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
            df = df.set_index(col)
            break
    return df.sort_index().rename(columns={c: c.capitalize() if c.lower() in ("open","high","low","close","volume") else c for c in df.columns})


class ImprovementAgent(BaseAgent):
    """Tests grid variants for a single strategy/symbol/timeframe and freezes OOS."""

    def __init__(self, symbol: str, timeframe: str, strategy_id: str, data_path: str, grid: Dict[str, List[Any]], backtest_fn: Callable | None = None, version: str = "1.1", **kw):
        super().__init__("improvement", kw)
        self.symbol = symbol
        self.timeframe = timeframe
        self.strategy_id = strategy_id
        self.data_path = data_path
        self.grid = grid
        self.backtest_fn = backtest_fn
        self.version = version

    async def run(self) -> AgentResult:
        return await asyncio.to_thread(self._run_sync)

    def _run_sync(self) -> AgentResult:
        p = Path(self.data_path)
        if not p.exists():
            return self._fail(f"data not found {p}")
        # lazy import backtest helper
        sys.path.insert(0, str(p.parents[1] / "backtests"))
        import importlib.util
        spec = importlib.util.spec_from_file_location("rt", str(Path(__file__).resolve().parents[1] / "run_tournament.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _bt_signal = mod.backtest_signal
        df = validate_ohlcv(_load_df(p))

        # default backtest_fn: donchian-style if not supplied, caller should pass custom
        if self.backtest_fn is None:
            # build generic from strategy_library if available
            from strategy_library import STRATEGIES
            strat_fn = STRATEGIES.get(self.strategy_id)
            if strat_fn is None:
                return self._fail(f"unknown strategy {self.strategy_id}")

            def _bt(data, params):
                # params are merged into signal for simple strategies that accept kwargs
                try:
                    sig = strat_fn(data, **params)
                except TypeError:
                    sig = strat_fn(data)
                m = _bt_signal(data, sig, self.symbol)
                class R: pass
                r = R(); r.ret_pct = m["ret_pct"]; r.trades = m["trades"]; r.win_pct = m["win_pct"]; r.max_dd_pct = m["max_dd_pct"]
                r.profit_factor = m["profit_factor"]; r.expectancy = m["expectancy"]; r.avg_r = m["expectancy_r"]; r.sharpe = m["sharpe"]
                return r
            bt = _bt
        else:
            bt = self.backtest_fn

        results = run_parameter_research(strategy_id=self.strategy_id, version=self.version, symbol=self.symbol, timeframe=self.timeframe, data=df, backtest=bt, grid=self.grid, min_trades=30)
        cands = select_validation_candidates(results, top_n=3)
        best = cands[0] if cands else None
        oos = freeze_oos_candidate(best, data=df, backtest=bt) if best else None

        # stability
        by_id = {r.experiment.experiment_id: {"profit_factor": r.metrics.get("profit_factor", 0)} for r in results}
        stab = parameter_stability(by_id, "profit_factor")
        cv_ok = stab.get("cv", 999) <= MAX_PARAMETER_CV

        status = "OOS_PASS" if oos and oos.status == "OOS_PASS" and cv_ok else "OOS_FAIL"
        if not cands:
            status = "REJECT"

        return self._ok(
            metrics={"variants": len(results), "candidates": len(cands), "best_score": score_result(best.metrics, best.metrics.get("complexity",0)) if best else 0, "cv": stab.get("cv", 0), "cv_ok": cv_ok, "oos_status": oos.status if oos else "NONE"},
            artifacts={"results": results, "candidates": cands, "best": best, "oos": oos, "stability": stab, "df_len": len(df)},
            notes=[f"{self.strategy_id} {self.symbol} {self.timeframe} best {best.experiment.parameters if best else {}} oos {oos.status if oos else 'NONE'} cv_ok={cv_ok}"],
        )
