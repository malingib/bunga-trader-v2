"""CLI for a complete historical research cycle.

Research-only: cached historical OHLCV is loaded, validated, split
chronologically, and evaluated. No approval, AI validation, messaging or
live execution is involved.
"""
from __future__ import annotations
import argparse, glob, json
from datetime import datetime, timezone
from pathlib import Path
from research_lab import chronological_split, rank_results, Experiment, ExperimentResult
from strategy_library import STRATEGIES
from strategy_interface import validate_ohlcv
from run_tournament import backtest_signal, load_df
from tournament_config import MIN_TRADES_VALIDATION, MIN_TRADES_OOS


def run(paths: list[str]) -> dict:
    results, errors = [], []
    for path in paths:
        p = Path(path); parts = p.stem.split("_")
        if len(parts) < 3: continue
        symbol, timeframe = parts[1], parts[2]
        try:
            data = validate_ohlcv(load_df(p))
            if len(data) < 300:
                errors.append({"file": path, "error": f"too short: {len(data)} bars"}); continue
            split = chronological_split(data)
            for strategy_id, fn in STRATEGIES.items():
                # Each split is evaluated independently; no parameters are selected on OOS.
                train = backtest_signal(split.train, fn(split.train), symbol)
                validation = backtest_signal(split.validation, fn(split.validation), symbol)
                oos = backtest_signal(split.test, fn(split.test), symbol)
                validation_ok = validation["trades"] >= MIN_TRADES_VALIDATION and validation["profit_factor"] > 1 and validation["expectancy_r"] > 0
                oos_ok = oos["trades"] >= MIN_TRADES_OOS and oos["profit_factor"] > 1 and oos["expectancy_r"] > 0
                status = "VALIDATION_REJECT" if not validation_ok else ("OOS_PASS" if oos_ok else "OOS_FAIL")
                metrics = {
                    "train_return_pct": train["ret_pct"], "train_trades": train["trades"],
                    "validation_return_pct": validation["ret_pct"], "validation_trades": validation["trades"],
                    "validation_profit_factor": validation["profit_factor"], "validation_expectancy_r": validation["expectancy_r"],
                    "validation_max_drawdown_pct": validation["max_dd_pct"], "validation_sharpe": validation["sharpe"],
                    "oos_return_pct": oos["ret_pct"], "oos_trades": oos["trades"],
                    "oos_profit_factor": oos["profit_factor"], "oos_expectancy_r": oos["expectancy_r"],
                    "oos_max_drawdown_pct": oos["max_dd_pct"], "oos_sharpe": oos["sharpe"],
                    "profit_factor": validation["profit_factor"], "expectancy_r": validation["expectancy_r"],
                    "sharpe": validation["sharpe"], "max_drawdown_pct": validation["max_dd_pct"],
                    "trades": validation["trades"], "complexity": 1.0,
                }
                exp = Experiment(f"{strategy_id}-v1-{symbol}-{timeframe}", strategy_id, "1.0", symbol, timeframe, {}, hypothesis=strategy_id)
                results.append(ExperimentResult(exp, metrics, status))
        except Exception as exc:
            errors.append({"file": path, "error": str(exc)})
    ranked = rank_results(results)
    return {"created_at": datetime.now(timezone.utc).isoformat(), "files": len(paths), "experiments": len(results),
            "validation_candidates": sum(r.status != "VALIDATION_REJECT" for r in results), "oos_pass": sum(r.status == "OOS_PASS" for r in results),
            "errors": errors, "leaderboard": [{"id":r.experiment.experiment_id,"strategy":r.experiment.strategy_id,"symbol":r.experiment.symbol,"timeframe":r.experiment.timeframe,"status":r.status,"metrics":r.metrics} for r in ranked[:50]]}


def main() -> None:
    p=argparse.ArgumentParser(); p.add_argument("--glob",default="data/market_cache/yf_*.csv"); p.add_argument("--output",default="backtests/research_report.json"); a=p.parse_args()
    report=run(sorted(glob.glob(a.glob))); out=Path(a.output); out.parent.mkdir(parents=True,exist_ok=True); out.write_text(json.dumps(report,indent=2,default=str))
    print(json.dumps({k:report[k] for k in ("files","experiments","validation_candidates","oos_pass","errors")},indent=2))

if __name__ == "__main__": main()
