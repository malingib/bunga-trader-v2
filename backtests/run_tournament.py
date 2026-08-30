"""Run cross-market strategy tournament on cached data (research-only)."""
from __future__ import annotations
import glob
from pathlib import Path
import pandas as pd
import sys
sys.path.insert(0, str(Path(__file__).parent))

from research_lab import Experiment, ExperimentResult, chronological_split, rank_results, score_result
from research_tournament import tournament
from strategy_library import STRATEGIES
from strategy_interface import validate_ohlcv
from robustness import monte_carlo_trade_paths, robustness_score
from portfolio_lab import align_equity_curves, correlation_matrix
from tournament_config import MIN_TRADES_TRAIN, MIN_TRADES_VALIDATION, MIN_TRADES_OOS, DEFAULT_COST_MODEL

# engine sizing logic reused
from engine_corrected import pip_value
import math

CACHE_DIR = Path("data/market_cache")

def bars_from_df(df: pd.DataFrame):
    # normalize to OHLC DataFrame indexed by time
    return df

def backtest_signal(df: pd.DataFrame, signal: pd.Series, symbol: str, start_equity=1000.0, risk_pct=1.0, sl_atr=1.2, rr=2.0, max_hold=15, max_dd_pct=40.0):
    """Simple signal-driven backtest with ATR SL/TP, risk sizing matching engine_corrected."""
    from strategy_library import atr
    n = len(df)
    a = atr(df, 14)
    pip_size = 0.01 if symbol.upper() in ("XAUUSD","GOLD") else 1.0
    pv = pip_value(symbol)
    bal = start_equity
    peak = bal
    trades = wins = 0
    pos = None
    equity = []
    pnls = []
    killed=False
    # align signal index with df index
    sig = signal.reindex(df.index).fillna(False).astype(bool)
    for i in range(200, n):
        # manage pos
        if pos is not None:
            exit_px = None
            low = float(df["Low"].iloc[i]); high=float(df["High"].iloc[i]); close=float(df["Close"].iloc[i])
            if pos["side"]=="BUY":
                if low <= pos["sl"]:
                    exit_px = pos["sl"]
                elif high >= pos["tp"]:
                    exit_px = pos["tp"]
            else:
                if high >= pos["sl"]:
                    exit_px = pos["sl"]
                elif low <= pos["tp"]:
                    exit_px = pos["tp"]
            if exit_px is None and (i - pos["idx"]) >= max_hold:
                exit_px = close
            if exit_px is not None:
                points = abs(exit_px - pos["entry"]) / pip_size
                pnl = points * pv * pos["lot"]
                if (pos["side"]=="BUY" and exit_px < pos["entry"]) or (pos["side"]=="SELL" and exit_px > pos["entry"]):
                    pnl = -pnl
                # for BUY, if exit is SL (<entry) points positive but should be loss -> handled
                # Actually for BUY: exit SL < entry => pnl should be negative
                # points is abs, so flip sign if loss
                is_win = (pos["side"]=="BUY" and exit_px > pos["entry"]) or (pos["side"]=="SELL" and exit_px < pos["entry"])
                if not is_win:
                    pnl = -abs(pnl)
                else:
                    pnl = abs(pnl)
                bal += pnl
                pnls.append(pnl)
                trades+=1
                if pnl>0: wins+=1
                if bal>peak: peak=bal
                if (peak - bal)/peak*100 >= max_dd_pct:
                    killed=True
                pos=None
        # equity each bar
        equity.append(bal)
        if killed: continue
        if pos is not None: continue
        if not sig.iloc[i]: continue
        atr_v = float(a.iloc[i]) if not pd.isna(a.iloc[i]) else 0
        if atr_v<=0: continue
        sl_d = atr_v * sl_atr
        tp_d = sl_d * rr
        entry = float(df["Close"].iloc[i])
        # decide direction: if strategy is reversion we still go long; for generic tournament go long only
        # Use signal as long entry; short entries could be separate but we keep long-only for now
        sl = entry - sl_d
        tp = entry + tp_d
        sl_pips = abs(entry - sl)/pip_size
        if sl_pips<=0: continue
        risk_amount = start_equity * risk_pct/100
        lot = max(0.001, risk_amount/(sl_pips*pv))
        pos = dict(side="BUY", entry=entry, sl=sl, tp=tp, lot=lot, idx=i)

    # stats
    max_dd=0; cp=start_equity; min_eq=start_equity
    for e in equity:
        if e>cp: cp=e
        if e<min_eq: min_eq=e
        dd=(cp-e)/cp*100 if cp else 0
        if dd>max_dd: max_dd=dd
    ret=(bal/start_equity-1)*100
    win_pct=(wins/trades*100) if trades else 0
    gross = sum(p for p in pnls if p>0); loss = sum(-p for p in pnls if p<0)
    pf = gross/loss if loss>0 else (float('inf') if gross>0 else 0)
    if pf==float('inf'): pf=99.0
    avg_win = (sum(p for p in pnls if p>0)/max(wins,1)) if wins else 0
    avg_loss = (sum(-p for p in pnls if p<0)/max(trades-wins,1)) if trades>wins else 0
    expectancy = (wins/trades*avg_win - (trades-wins)/trades*avg_loss) if trades else 0
    # expectancy in R: avg pnl / risk per trade; approximate R as expectancy / risk_amount
    expectancy_r = expectancy / (start_equity*risk_pct/100) if trades else 0
    # sharpe approx from equity returns
    import numpy as np
    eq_s = pd.Series(equity)
    rets = eq_s.pct_change().dropna()
    sharpe = float(rets.mean()/rets.std()*math.sqrt(252*24) ) if len(rets)>2 and rets.std()!=0 else 0.0
    if math.isnan(sharpe) or math.isinf(sharpe): sharpe=0.0
    return {
        "ret_pct": ret, "trades": float(trades), "win_pct": win_pct, "max_dd_pct": max_dd,
        "profit_factor": float(pf), "expectancy": float(expectancy), "expectancy_r": float(expectancy_r),
        "sharpe": float(sharpe), "final_equity": bal, "equity": equity, "pnls": pnls
    }

def load_df(path: Path):
    df = pd.read_csv(path)
    # detect date column
    for col in ["Datetime","Date","date","Timestamp"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], utc=True, errors="coerce")
            df = df.set_index(col)
            break
    df = df.sort_index()
    # ensure OHLC names
    rename = {c: c.capitalize() for c in df.columns}
    # fix Open etc already
    if "Open" not in df.columns:
        # try case-insensitive
        mapping={}
        for c in df.columns:
            if c.lower()=="open": mapping[c]="Open"
            if c.lower()=="high": mapping[c]="High"
            if c.lower()=="low": mapping[c]="Low"
            if c.lower()=="close": mapping[c]="Close"
            if c.lower()=="volume": mapping[c]="Volume"
        df=df.rename(columns=mapping)
    return df

def main():
    files = sorted(glob.glob(str(CACHE_DIR / "yf_*.csv")))
    if not files:
        print("No cache files found in data/market_cache/yf_*.csv")
        return
    print(f"Found {len(files)} cache files")
    results=[]
    curves={}
    for f in files:
        p = Path(f)
        # parse symbol/timeframe: yf_NAS100_1h_2y.csv
        parts = p.stem.split("_")
        if len(parts)<3: continue
        symbol = parts[1]
        timeframe = parts[2]
        print(f"\n== {symbol} {timeframe} ({p.name}) ==")
        try:
            df = load_df(p)
            df = validate_ohlcv(df)
        except Exception as e:
            print(f"  validate failed: {e}")
            continue
        if len(df)<300:
            print(f"  too short {len(df)} bars, skip")
            continue
        split = chronological_split(df)
        print(f"  split: train={len(split.train)} val={len(split.validation)} oos={len(split.test)}")
        for strat_id, fn in STRATEGIES.items():
            # build signals for each split separately to avoid lookahead
            try:
                train_sig = fn(split.train)
                val_sig = fn(split.validation)
                oos_sig = fn(split.test)
            except Exception as e:
                print(f"  ! {strat_id} signal failed: {e}")
                continue
            train_m = backtest_signal(split.train, train_sig, symbol)
            val_m = backtest_signal(split.validation, val_sig, symbol)
            oos_m = backtest_signal(split.test, oos_sig, symbol)
            # gate checks
            status="VALIDATION_CANDIDATE"
            if val_m["trades"] < MIN_TRADES_VALIDATION:
                status="VALIDATION_REJECT"
            # combine metrics like research_runner
            metrics={
                **{f"train_{k}":v for k,v in train_m.items() if k not in ("equity","pnls")},
                **{f"validation_{k}":v for k,v in val_m.items() if k not in ("equity","pnls")},
                **{f"oos_{k}":v for k,v in oos_m.items() if k not in ("equity","pnls")},
                "profit_factor": float(val_m["profit_factor"]),
                "expectancy_r": float(val_m["expectancy_r"]),
                "sharpe": float(val_m["sharpe"]),
                "max_drawdown_pct": float(val_m["max_dd_pct"]),
                "trades": float(val_m["trades"]),
                "oos_profit_factor": float(oos_m["profit_factor"]),
                "oos_expectancy_r": float(oos_m["expectancy_r"]),
                "complexity": 1.0,
            }
            exp = Experiment(
                experiment_id=f"{strat_id}-v1-{symbol}-{timeframe}",
                strategy_id=strat_id, version="1.0", symbol=symbol, timeframe=timeframe,
                parameters={}, hypothesis=f"{strat_id} on {symbol} {timeframe}"
            )
            # adjust status for OOS if validation passed
            if status=="VALIDATION_CANDIDATE":
                if oos_m["trades"]>=MIN_TRADES_OOS and oos_m["profit_factor"]>1.0 and oos_m["expectancy_r"]>0:
                    status="OOS_PASS"
                else:
                    status="OOS_FAIL"
            res = ExperimentResult(experiment=exp, metrics=metrics, status=status)
            results.append(res)
            # store equity for correlation (use validation equity)
            key=f"{strat_id}:{symbol}:{timeframe}"
            curves[key]=pd.Series(val_m["equity"])
            print(f"  {strat_id:22} trainT={train_m['trades']:3.0f} valT={val_m['trades']:3.0f} oosT={oos_m['trades']:3.0f} "
                  f"valPF={val_m['profit_factor']:4.1f} oosPF={oos_m['profit_factor']:4.1f} {status}")

    if not results:
        print("No results")
        return
    print(f"\n=== TOURNAMENT LEADERBOARD ({len(results)} experiments) ===")
    ranked = rank_results(results)
    entries = tournament(results)
    for i, r in enumerate(ranked[:15],1):
        e=r.experiment
        sc=score_result(r.metrics, r.metrics.get("complexity",0))
        print(f"{i:2}. {e.strategy_id:22} {e.symbol:7} {e.timeframe:4} score={sc:6.2f} "
              f"valPF={r.metrics.get('profit_factor',0):4.1f} oosPF={r.metrics.get('oos_profit_factor',0):4.1f} "
              f"trades={r.metrics.get('trades',0):.0f} dd={r.metrics.get('max_drawdown_pct',0):.1f}% {r.status}")

    print("\n=== TOP 5 TOURNAMENT ENTRIES (research_tournament.tournament) ===")
    for i, e in enumerate(entries[:5],1):
        print(f"{i}. {e.key} score={e.score:.2f} status={e.status}")

    # robustness on top candidate
    if ranked:
        top = ranked[0]
        # reconstruct pnls for top: re-run quick to get pnls
        # find file for top
        top_file = None
        for f in files:
            if f"_{top.experiment.symbol}_" in f and f"_{top.experiment.timeframe}_" in f:
                top_file=f; break
        if top_file:
            df = validate_ohlcv(load_df(Path(top_file)))
            split = chronological_split(df)
            fn = STRATEGIES[top.experiment.strategy_id]
            val_m = backtest_signal(split.validation, fn(split.validation), top.experiment.symbol)
            mc = monte_carlo_trade_paths(val_m["pnls"], iterations=2000, seed=42)
            print(f"\nRobustness (top {top.experiment.strategy_id} {top.experiment.symbol} {top.experiment.timeframe}): {mc}")
            # stability across variants not available (single param), show CV approx
            from research_lab import parameter_stability
            stab = parameter_stability({top.experiment.experiment_id: {"profit_factor": top.metrics.get("profit_factor",0)}}, "profit_factor")
            print(f"Stability: {stab}")

    # correlation
    try:
        if len(curves)>=2:
            corr = correlation_matrix(curves)
            print(f"\nCorrelation matrix shape {corr.shape}, mean upper-tri: {corr.where(~corr.isin([1.0])).stack().mean():.3f}")
            print(corr.round(2).to_string())
    except Exception as e:
        print(f"corr failed {e}")

if __name__=="__main__":
    main()
