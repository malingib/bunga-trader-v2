"""CLI for ResearchOrchestrator — run tournament / improvement / full loop."""
from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from agents.orchestrator import ResearchOrchestrator


async def main():
    ap = argparse.ArgumentParser(description="Research orchestrator — tournament + improvement")
    ap.add_argument("--mode", choices=["tournament", "improvement", "full", "autonomous"], default="tournament")
    ap.add_argument("--symbols", nargs="+", default=None, help="filter symbols e.g. XAUUSD SP500")
    ap.add_argument("--improve-strategy", default="donchian_breakout")
    ap.add_argument("--improve-symbol", default="XAUUSD")
    ap.add_argument("--improve-tf", default="15m")
    ap.add_argument("--free-reign", action="store_true", help="enable autonomous writes (research-only) + proposals for money paths")
    ap.add_argument("--interval", type=int, default=0, help="for autonomous: seconds between cycles (0=once, else forever)")
    ap.add_argument("--max-cycles", type=int, default=1, help="for autonomous forever: max cycles (0=infinite)")
    args = ap.parse_args()

    if args.mode == "autonomous" or args.free_reign:
        from agents.autonomous import AutonomousOrchestrator
        orch = AutonomousOrchestrator(free_reign=args.free_reign)
        if args.interval > 0:
            await orch.run_forever(interval_sec=args.interval, symbols=args.symbols, max_cycles=args.max_cycles or 0)
        else:
            res = await orch.autonomous_cycle(symbols=args.symbols)
            print(json.dumps(res, indent=2, default=str))
        print(f"\nLedger: {orch.ledger}  history: {len(orch.history)} agents  applied: {len(orch.applied)} proposals: {len(orch.proposals)}")
        return

    orch = ResearchOrchestrator()

    if args.mode in ("tournament", "full"):
        print(">> orchestrator: tournament")
        res = await orch.run_tournament(symbols=args.symbols) if args.symbols else await orch.run_tournament()
        print(json.dumps({"status": res.status, "metrics": res.metrics, "notes": res.notes}, indent=2, default=str))
        if res.artifacts.get("ranked"):
            print("\nTop 5 ranked (OOS_PASS filtered in improvement):")
            from research_lab import rank_results
            ranked = res.artifacts["ranked"][:5]
            for r in ranked:
                print(f"  {r.experiment.strategy_id} {r.experiment.symbol} {r.experiment.timeframe} {r.status} PF={r.metrics.get('profit_factor'):.2f}")

    if args.mode in ("improvement", "full"):
        print("\n>> orchestrator: improvement")
        # example grid for donchian lookback — caller can pass custom grid via orchestrator.create_agent directly
        data_path = f"data/market_cache/yf_{args.improve_symbol}_{args.improve_tf}_60d.csv"
        # fallback to 1h/2y if 60d missing
        if not Path(data_path).exists():
            data_path = f"data/market_cache/yf_{args.improve_symbol}_1h_2y.csv"
        grid = {"lookback": [10, 15, 20, 25, 30]}
        res = await orch.run_improvement(symbol=args.improve_symbol, timeframe=args.improve_tf, strategy_id=args.improve_strategy, data_path=data_path, grid=grid)
        print(json.dumps({"status": res.status, "metrics": res.metrics, "notes": res.notes}, indent=2, default=str))

    print(f"\nLedger: {orch.ledger}  history: {len(orch.history)} agents")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nInterrupted by user — ledger preserved at backtests/results.jsonl")
        import sys; sys.exit(130)
