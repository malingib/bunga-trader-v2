import asyncio, json, sys, traceback
from quantcontext.server import backtest_strategy

START = "2024-07-01"
END = "2026-07-01"
UNIV = "sp500"

variants = {
    "A_technical_signal_only": {
        "stages": [{"order": 1, "type": "signal", "skill": "technical_signal",
                     "config": {"indicators": ["RSI", "SMA_cross", "bollinger"],
                                "rsi_oversold": 30, "rsi_overbought": 70}}],
    },
    "B_momentum_screen": {
        "stages": [{"order": 1, "type": "screen", "skill": "momentum_screen",
                     "config": {"lookback_days": 126, "top_pct": 0.2}}],
    },
    "C_momentum_then_technical": {
        "stages": [
            {"order": 1, "type": "screen", "skill": "momentum_screen",
             "config": {"lookback_days": 126, "top_pct": 0.2}},
            {"order": 2, "type": "signal", "skill": "technical_signal",
             "config": {"indicators": ["RSI", "SMA_cross", "bollinger"],
                        "rsi_oversold": 30, "rsi_overbought": 70}},
        ],
    },
}

for name, spec in variants.items():
    print("=" * 70)
    print("VARIANT:", name)
    try:
        raw = asyncio.run(backtest_strategy(
            stages=spec["stages"],
            universe=UNIV,
            rebalance="monthly",
            sizing="equal_weight",
            start_date=START,
            end_date=END,
        ))
        out = json.loads(raw)
        with open(f"_bt_{name}.json", "w") as f:
            json.dump(out, f, indent=2, default=str)
        # Print top-level keys + metrics
        print("TOP KEYS:", list(out.keys()))
        print("METRICS:", json.dumps(out.get("metrics", {}), indent=2))
        ec = out.get("equity_curve", [])
        print("equity_curve points:", len(ec), "first:", ec[0] if ec else None, "last:", ec[-1] if ec else None)
        print("trades:", len(out.get("trades", [])))
        warns = out.get("warnings")
        if warns:
            print("WARNINGS:", warns)
    except Exception as e:
        traceback.print_exc()
        print("FAILED:", repr(e))
    sys.stdout.flush()
print("DONE")
