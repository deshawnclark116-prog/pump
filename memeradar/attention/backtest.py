"""Chronological replay for ATTENTION model; separate from live paper ledger.

Signals are assembled solely from data known at signal time. The simulated
entry is at a LATER quote, and unresolved exposures are not counted as wins.
No rule tuning against a consumed validation/holdout window.
"""
from __future__ import annotations

from collections import defaultdict
import json
import time

from . import db as evidence
from .model import FEATURES, features_for, forecast

FEE=0.0125
SLIPPAGE=0.01
HORIZON=3600
TRADE_USD=10
CAPITAL=250


def realized_usd(entry,exit_price,usd=TRADE_USD):
    if entry<=0 or exit_price is None or exit_price<=0:
        return 0.
    tokens=(usd*(1-FEE))/(entry*(1+SLIPPAGE))
    return tokens*exit_price*(1-SLIPPAGE)*(1-FEE)


def percent_return(entry,price):
    return (realized_usd(entry,price)/TRADE_USD-1)*100


def buy_arrived(rows,entry_ts,now):
    arrived=0.;last=entry_ts
    for row in rows:
        ts=row["observed_ts"]
        if ts<=entry_ts:continue
        if ts>now:break
        dt=ts-last
        if dt>120: return None
        if dt>0:
            arrived+=row["buy_volume_proxy_m5"]*dt/300.
            last=ts
    return arrived


def eligible(db, mint,ts,liq):
    if liq<15000:
        return False
    row=db.execute("SELECT state,checked_ts FROM attention_safety_history "
                   "WHERE mint=? AND checked_ts<=? ORDER BY checked_ts DESC LIMIT 1",
                   (mint,ts)).fetchone()
    return bool(row and row["state"]=="passed" and ts-row["checked_ts"]<=3600)


def observations(db,since,until):
    """No futures in signal construction; leave incomplete exits unresolved."""
    hits=defaultdict(list); chain=defaultdict(list)
    for r in db.execute("SELECT * FROM attention_hits WHERE observed_ts BETWEEN ? AND ? "
                        "ORDER BY mint,observed_ts",(since-300,until)):
        hits[r["mint"]].append(dict(r))
    for r in db.execute("SELECT * FROM attention_chain WHERE observed_ts BETWEEN ? AND ? "
                        "ORDER BY mint,observed_ts",(since-360,until+HORIZON+120)):
        chain[r["mint"]].append(dict(r))
    return hits,chain


def signals(db,spec,since,until):
    hits,chain=observations(db,since,until)
    output=[]
    for mint,rows in chain.items():
        relevant=hits.get(mint,[])
        if not relevant:continue
        latest_sample=0
        for i,row in enumerate(rows):
            ts=row["observed_ts"]
            if not since<=ts<until or ts-latest_sample<300:
                continue
            recent=[h for h in relevant if ts-300<h["observed_ts"]<=ts]
            if not recent:
                continue
            earlier=next((r for r in reversed(rows[:i]) if r["observed_ts"]<=ts-240),None)
            x=features_for(row,recent,earlier)
            future=forecast(spec,x)
            liq=row["liquidity_usd"]
            if not eligible(db,mint,ts,liq) or future<3*liq:
                continue
            output.append(dict(mint=mint,ts=ts,predicted=future,liq=liq,
                               signal_price=row["price_usd"],seen=row["buy_volume_proxy_m5"],x=x))
            latest_sample=ts
    return sorted(output,key=lambda s:s["ts"]),chain


def simulate(db,version,*,since,until):
    model=db.execute("SELECT * FROM attention_models WHERE version=?",(version,)).fetchone()
    if not model or model["status"]!="retrospective_pass":
        return {"status":"blocked","reason":"validated retrospective model required"}
    if since<model["created_ts"]:
        # No trading evaluation on the same held-out set used to choose the model.
        return {"status":"blocked","reason":"independent evaluation must start after model creation"}
    spec=json.loads(model["model_json"])
    candidates,chain=signals(db,spec,since,until)
    cash=CAPITAL; completed=[];unresolved=[]
    last_exit={}
    trades=[]
    for signal in candidates:
        mint=signal["mint"]; ts=signal["ts"]; rows=chain[mint]
        if ts-last_exit.get(mint,0)<3600:
            continue
        # Slots are occupied by any earlier entry whose exit has not yet occurred.
        active=sum(1 for t in trades if t["entry_ts"]<=ts and
                   (t["exit_ts"] is None or t["exit_ts"]>ts))
        free_cash=CAPITAL-sum(TRADE_USD for t in trades if t["entry_ts"]<=ts
                              and (t["exit_ts"] is None or t["exit_ts"]>ts))
        if active>=5 or free_cash<TRADE_USD:
            continue
        next_row=next((r for r in rows if ts+20<=r["observed_ts"]<=ts+120),None)
        if next_row is None or not eligible(db,mint,next_row["observed_ts"],next_row["liquidity_usd"]):
            continue
        entry=next_row["price_usd"]; t0=next_row["observed_ts"]
        pos=dict(mint=mint,signal_ts=ts,entry_ts=t0,entry_price=entry,
                 predicted_buy_proxy=signal["predicted"],exit_ts=None,
                 reason="UNRESOLVED",usd_out=None,net_pnl=None)
        later=[r for r in rows if t0<r["observed_ts"]<=t0+HORIZON+120]
        acc=0.; prev=t0
        for r in later:
            dt=r["observed_ts"]-prev
            if dt>120: break
            acc+=r["buy_volume_proxy_m5"]*dt/300.
            prev=r["observed_ts"]
            pct=percent_return(entry,r["price_usd"])
            reason=("STOP" if pct<=-30 else
                    "75PCT_ARRIVED" if acc>=0.75*signal["predicted"] else
                    "TIME" if r["observed_ts"]>=t0+HORIZON else None)
            if reason:
                pos.update(exit_ts=r["observed_ts"],exit_price=r["price_usd"],
                           reason=reason,usd_out=realized_usd(entry,r["price_usd"]))
                pos["net_pnl"]=pos["usd_out"]-TRADE_USD
                last_exit[mint]=r["observed_ts"]
                completed.append(pos)
                break
        if pos["exit_ts"] is None:
            unresolved.append(pos)
        trades.append(pos)
    pnls=[x["net_pnl"] for x in completed]
    return {"status":"research_replay","version":version,"since":since,"until":until,
            "signals":len(candidates),"closed":len(completed),"unresolved":len(unresolved),
            "wins":sum(p>0 for p in pnls),"losses":sum(p<=0 for p in pnls),
            "realized_pnl":round(sum(pnls),2),
            "limitations":["Derived buy-volume proxy, not wallet-verified buy USD",
                           "No executable swap quotes, MEV or failed-transaction simulation",
                           "Backtest is not evidence of a profitable live strategy",
                           "Unresolved exposures excluded from realized P&L"],
            "trades":trades}
