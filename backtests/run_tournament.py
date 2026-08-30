"""Cross-market historical strategy tournament (research-only)."""
from __future__ import annotations
import glob, math
from pathlib import Path
import pandas as pd
import numpy as np

from research_lab import Experiment, ExperimentResult, chronological_split, rank_results
from strategy_library import STRATEGIES, atr
from strategy_interface import validate_ohlcv
from tournament_config import MIN_TRADES_VALIDATION, MIN_TRADES_OOS

CACHE_DIR = Path("data/market_cache")

def backtest_signal(df, signal, symbol, start_equity=1000.0, risk_pct=1.0, sl_atr=1.2, rr=2.0, max_hold=15, max_dd_pct=40.0):
    """Long/short signal backtest. Signals are acted on at the next bar open."""
    a=atr(df,14); sig=signal.reindex(df.index).fillna(False).astype(bool); bal=peak=start_equity; pos=None; trades=wins=0; pnls=[]; equity=[]; killed=False
    for i in range(200,len(df)):
        o,h,l,c=[float(df[x].iloc[i]) for x in ("Open","High","Low","Close")]
        if pos:
            exit_px=None
            if pos["side"]==1:
                if l<=pos["sl"]: exit_px=pos["sl"]
                elif h>=pos["tp"]: exit_px=pos["tp"]
            else:
                if h>=pos["sl"]: exit_px=pos["sl"]
                elif l<=pos["tp"]: exit_px=pos["tp"]
            if exit_px is None and i-pos["idx"]>=max_hold: exit_px=c
            if exit_px is not None:
                pnl=(exit_px-pos["entry"])*pos["lot"] if pos["side"]==1 else (pos["entry"]-exit_px)*pos["lot"]
                bal+=pnl; pnls.append(pnl); trades+=1; wins+=pnl>0; peak=max(peak,bal); pos=None
                if peak and (peak-bal)/peak*100>=max_dd_pct: killed=True
        equity.append(bal)
        if killed or pos or i+1>=len(df) or not sig.iloc[i]: continue
        av=float(a.iloc[i]) if not pd.isna(a.iloc[i]) else 0
        if av<=0: continue
        # signal direction: bool strategies are long-only by design; regime/combined callers can pass +/-1.
        direction=1 if not isinstance(signal.iloc[i], (int,float,np.integer,np.floating)) or float(signal.iloc[i])>=0 else -1
        entry=float(df["Open"].iloc[i+1]); risk=av*sl_atr
        sl=entry-risk if direction==1 else entry+risk; tp=entry+risk*rr if direction==1 else entry-risk*rr
        lot=(bal*risk_pct/100)/risk if risk>0 else 0
        if lot>0: pos={"side":direction,"entry":entry,"sl":sl,"tp":tp,"lot":lot,"idx":i+1}
    gross=sum(p for p in pnls if p>0); loss=-sum(p for p in pnls if p<0); pf=gross/loss if loss else (99.0 if gross else 0.0)
    exp=float(np.mean(pnls)/(start_equity*risk_pct/100)) if pnls else 0.0
    eq=pd.Series(equity); rets=eq.pct_change().dropna(); sharpe=float(rets.mean()/rets.std()*math.sqrt(252)) if len(rets)>2 and rets.std() else 0.0
    peak2=start_equity; maxdd=0.0
    for x in equity: peak2=max(peak2,x); maxdd=max(maxdd,(peak2-x)/peak2*100 if peak2 else 0)
    return {"ret_pct":(bal/start_equity-1)*100,"trades":float(trades),"win_pct":wins/trades*100 if trades else 0,"max_dd_pct":maxdd,"profit_factor":pf,"expectancy_r":exp,"sharpe":sharpe,"final_equity":bal,"equity":equity,"pnls":pnls}

def load_df(path):
    df=pd.read_csv(path)
    date=next((c for c in ("Datetime","Date","date","Timestamp") if c in df.columns),None)
    if date: df[date]=pd.to_datetime(df[date],utc=True,errors="coerce"); df=df.set_index(date)
    df=df.sort_index(); mapping={c:next((x for x in ("Open","High","Low","Close","Volume") if c.lower()==x.lower()),c) for c in df.columns}; return df.rename(columns=mapping)

def run(files):
    results=[]; errors=[]
    for f in files:
        p=Path(f); parts=p.stem.split("_")
        if len(parts)<3: continue
        symbol,timeframe=parts[1],parts[2]
        try:
            df=validate_ohlcv(load_df(p));
            if len(df)<300: errors.append({"file":f,"error":f"too short: {len(df)} bars"}); continue
            s=chronological_split(df)
            for sid,fn in STRATEGIES.items():
                train,val,oos=(backtest_signal(x,fn(x),symbol) for x in (s.train,s.validation,s.test))
                vok=val["trades"]>=MIN_TRADES_VALIDATION and val["profit_factor"]>1 and val["expectancy_r"]>0
                ook=oos["trades"]>=MIN_TRADES_OOS and oos["profit_factor"]>1 and oos["expectancy_r"]>0
                status="VALIDATION_REJECT" if not vok else ("OOS_PASS" if ook else "OOS_FAIL")
                m={"train_return_pct":train["ret_pct"],"train_trades":train["trades"],"validation_return_pct":val["ret_pct"],"validation_trades":val["trades"],"validation_profit_factor":val["profit_factor"],"validation_expectancy_r":val["expectancy_r"],"validation_max_drawdown_pct":val["max_dd_pct"],"validation_sharpe":val["sharpe"],"oos_return_pct":oos["ret_pct"],"oos_trades":oos["trades"],"oos_profit_factor":oos["profit_factor"],"oos_expectancy_r":oos["expectancy_r"],"oos_max_drawdown_pct":oos["max_dd_pct"],"oos_sharpe":oos["sharpe"],"profit_factor":val["profit_factor"],"expectancy_r":val["expectancy_r"],"sharpe":val["sharpe"],"max_drawdown_pct":val["max_dd_pct"],"trades":val["trades"],"complexity":1.0}
                e=Experiment(f"{sid}-v1-{symbol}-{timeframe}",sid,"1.0",symbol,timeframe,{},hypothesis=sid); results.append(ExperimentResult(e,m,status))
        except Exception as ex: errors.append({"file":f,"error":str(ex)})
    ranked=rank_results(results)
    return {"created_at":pd.Timestamp.utcnow().isoformat(),"files":len(files),"experiments":len(results),"validation_candidates":sum(r.status!="VALIDATION_REJECT" for r in results),"oos_pass":sum(r.status=="OOS_PASS" for r in results),"errors":errors,"leaderboard":[{"id":r.experiment.experiment_id,"strategy":r.experiment.strategy_id,"symbol":r.experiment.symbol,"timeframe":r.experiment.timeframe,"status":r.status,"metrics":r.metrics} for r in ranked[:50]]}

def main():
    import argparse
    p=argparse.ArgumentParser(); p.add_argument("--glob",default=str(CACHE_DIR/"yf_*.csv")); p.add_argument("--output",default="backtests/research_report.json"); a=p.parse_args(); report=run(sorted(glob.glob(a.glob))); Path(a.output).parent.mkdir(parents=True,exist_ok=True); Path(a.output).write_text(__import__("json").dumps(report,indent=2)); print({k:report[k] for k in ("files","experiments","validation_candidates","oos_pass","errors")})
if __name__=="__main__": main()
