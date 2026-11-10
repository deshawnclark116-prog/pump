"""Meme Radar v1 micro-bankroll PAPER accounts, born 2026-11-09.

Each existing strategy's *own raw-signal decision* authorizes an intent; it
cannot consume another strategy's probability, capital, or simulated order.
The archived $250 ledgers are preserved and exit-only. New accounts are
independent, forward-only $25 with $1 or $5 stakes, no resets/top-ups.

Quotes are *indicative*, not guaranteed executable AMM swap quotes. This
paper model explicitly deducts two-sided percentage friction and conservative
fixed $0.02 transaction overhead each side. Market/priority/gas costs vary
and unobserved real slippage may be materially higher, particularly for $1
trades. No wallet, signing key, or swap API anywhere in this module.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import math
import os
import sqlite3
import time

from .attention.backtest import eligible
from .attention import explore

log=logging.getLogger(__name__)

SOCIAL="social_attention"
SOCIAL_GUARDED="social_attention_guarded_20261110"
OSECE_SINGLE="osece_early_signal"
OSECE_COLLISION="osece_cross_source"
OSECE_LIQ="osece_early_liquidity"
OSECE_COLLISION_LIQ="osece_cross_source_early_liquidity_20261110"
MODELS=(SOCIAL,SOCIAL_GUARDED,OSECE_SINGLE,OSECE_COLLISION,OSECE_LIQ,OSECE_COLLISION_LIQ)
SIZE_OPTIONS=(1.0,5.0)
START_CASH=25.0
FEE_PER_SIDE=0.0125
SLIPPAGE_PER_SIDE=0.01
NETWORK_OVERHEAD_USD_PER_SIDE=0.02
ENTRY_LAG=20.0
MAX_ENTRY_WAIT=180.0
SOCIAL_HOLD=1800.0
OSECE_HOLD=1200.0
SOCIAL_COOLDOWN=3600.0
OSECE_COOLDOWN=7200.0
QUOTE_MAX_AGE=90.0
SCHEMA="""
CREATE TABLE IF NOT EXISTS micro_meta (
 key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS micro_intents (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 model TEXT NOT NULL, stake REAL NOT NULL,
 mint TEXT NOT NULL, source_signal_ts REAL NOT NULL,
 ready_ts REAL NOT NULL, ready_price REAL NOT NULL,
 status TEXT NOT NULL, reason TEXT,
 entry_ts REAL, entry_price REAL,
 entry_liquidity REAL, exit_ts REAL,
 exit_price REAL, usd_out REAL, exit_reason TEXT,
 UNIQUE(model,stake,mint,source_signal_ts));
CREATE INDEX IF NOT EXISTS micro_active
 ON micro_intents(model,stake,status,ready_ts);
CREATE INDEX IF NOT EXISTS micro_mint
 ON micro_intents(mint,entry_ts,status);
"""


def enabled():
    return os.environ.get("MICRO_PAPER_ENABLED")=="1"


def ensure(db):
    db.executescript(SCHEMA)


def activated(db,now=None):
    ensure(db)
    now=time.time() if now is None else now
    r=db.execute("SELECT value FROM micro_meta WHERE key='started_at'").fetchone()
    if r:
        return float(r[0])
    db.execute("INSERT INTO micro_meta(key,value) VALUES ('started_at',?)",(str(now),))
    db.commit()
    return now


def from_osece(variant):
    from .osece import core
    return {
        core.SINGLE:OSECE_SINGLE,
        core.COLLISION:OSECE_COLLISION,
        core.EARLY_LIQ:OSECE_LIQ,
        core.COLLISION_EARLY:OSECE_COLLISION_LIQ,
    }.get(variant)


def mark_ready(db, *, model, mint, source_signal_ts, ready_ts, ready_price):
    """Called only after source model passed its own causal third-quote gate.
    The NEXT separately observed quote is required for micro buy simulation.
    """
    if not enabled() or model not in MODELS:
        return 0
    start=activated(db,ready_ts)
    if source_signal_ts<start or ready_ts<start or not ready_price or ready_price<=0:
        return 0
    added=0
    for size in SIZE_OPTIONS:
        cur=db.execute(
            "INSERT OR IGNORE INTO micro_intents "
            "(model,stake,mint,source_signal_ts,ready_ts,ready_price,status)"
            " VALUES (?,?,?,?,?,?,'queued')",
            (model,size,mint,source_signal_ts,ready_ts,ready_price))
        added+=cur.rowcount
    db.commit()
    return added


def proceeds(entry,exit_price,stake):
    if entry is None or exit_price is None or entry<=0 or exit_price<=0:
        return None
    amount_after_overhead=max(0.0,stake-NETWORK_OVERHEAD_USD_PER_SIDE)
    tokens=amount_after_overhead*(1-FEE_PER_SIDE)/(entry*(1+SLIPPAGE_PER_SIDE))
    return max(0.0,tokens*exit_price*(1-SLIPPAGE_PER_SIDE)*
               (1-FEE_PER_SIDE)-NETWORK_OVERHEAD_USD_PER_SIDE)


def net_pct(entry,exit_price,stake):
    cash=proceeds(entry,exit_price,stake)
    return (cash/stake-1)*100 if cash is not None else None


def cash(db,model,size):
    r=db.execute(
        "SELECT COALESCE(SUM(CASE WHEN entry_ts IS NOT NULL THEN stake END),0),"
        "COALESCE(SUM(usd_out),0) "
        "FROM micro_intents WHERE model=? AND stake=?",(model,size)).fetchone()
    return START_CASH-float(r[0])+float(r[1])


def open_count(db,model,size):
    return db.execute("SELECT COUNT(*) FROM micro_intents "
                      "WHERE model=? AND stake=? AND status='open'",
                      (model,size)).fetchone()[0]


def _latest(db,mint,model,now):
    if model in (SOCIAL,SOCIAL_GUARDED):
        return db.execute(
            "SELECT observed_ts,price_usd price,liquidity_usd liquidity "
            "FROM attention_chain WHERE mint=? AND observed_ts<=? AND observed_ts>? "
            "ORDER BY observed_ts DESC LIMIT 1",(mint,now,now-QUOTE_MAX_AGE)).fetchone()
    return db.execute(
        "SELECT observed_ts,price,liquidity "
        "FROM osece_quotes WHERE mint=? AND observed_ts<=? AND observed_ts>? "
        "ORDER BY observed_ts DESC LIMIT 1",(mint,now,now-QUOTE_MAX_AGE)).fetchone()


def _market(db,mint,model,now):
    """Never use stale or synthetic fills; reuse original gate at micro entry."""
    q=_latest(db,mint,model,now)
    if not q or not q["price"] or not math.isfinite(q["price"]):
        return None
    if model in (SOCIAL,SOCIAL_GUARDED):
        if not eligible(db,mint,q["observed_ts"],q["liquidity"]):
            return None
        return q
    from .osece import core
    full=db.execute(
        "SELECT * FROM osece_quotes WHERE mint=? AND observed_ts=?",
        (mint,q["observed_ts"])).fetchone()
    source_variant={
        OSECE_SINGLE:core.SINGLE,
        OSECE_COLLISION:core.COLLISION,
        OSECE_LIQ:core.EARLY_LIQ,
        OSECE_COLLISION_LIQ:core.COLLISION_EARLY,
    }.get(model)
    if not core._market_entry_ok(full,source_variant) or not core._eligible_for_variant(
            db,mint,q["observed_ts"],q["liquidity"],source_variant):
        return None
    return q


def _guard_social_reason(db, p, q):
    """Prospective two-quote buyer-confirmation arm (not an optimized champion).

    Original Social Attention stays unchanged. All receipts precede the
    simulated fill. No outcome-based selection on the consumed holdout.
    """
    if q["liquidity"] < 25000:
        return "guard_low_liquidity"
    rows=db.execute(
        "SELECT observed_ts,price_usd,liquidity_usd,vol_m5,buys_m5,sells_m5,"
        "buy_volume_proxy_m5 FROM attention_chain WHERE mint=? AND observed_ts<=? "
        "AND observed_ts>=? ORDER BY observed_ts DESC LIMIT 4",
        (p["mint"],q["observed_ts"],q["observed_ts"]-120)).fetchall()
    if len(rows)<2 or rows[1]["observed_ts"]>rows[0]["observed_ts"]-20:
        return "guard_no_independent_quote"
    if any(r["buys_m5"]<15 or r["sells_m5"]<2 or
           r["buys_m5"]<1.25*r["sells_m5"] or
           r["buy_volume_proxy_m5"]<500 or
           r["vol_m5"]<1000 or r["liquidity_usd"]<25000
           for r in rows[:2]):
        return "guard_weak_buyer_confirmation"
    source=db.execute(
        "SELECT signal_price FROM attention_explore_signals WHERE variant=? "
        "AND mint=? AND ABS(signal_ts-?)<0.001 ORDER BY id DESC LIMIT 1",
        (explore.VERSIONS[0],p["mint"],p["source_signal_ts"])).fetchone()
    if not source or source["signal_price"]<=0:
        return "guard_source_signal_missing"
    if q["price"]>source["signal_price"]*1.05:
        return "guard_price_chase_over_5pct"
    if q["price"]<rows[1]["price_usd"]*.94:
        return "guard_short_term_dump"
    return None


def tick(db,now=None):
    if not enabled():
        return {"status":"disabled"}
    now=time.time() if now is None else now
    ensure(db)
    activated(db,now)
    result={"bought":0,"sold":0,"expired":0,"waiting":0,"blocked_cash":0,
            "blocked_capacity":0}
    # Exit first so released capital is usable on a later fresh buy.
    for p in db.execute("SELECT * FROM micro_intents WHERE status='open' "
                        "ORDER BY entry_ts,id").fetchall():
        q=_latest(db,p["mint"],p["model"],now)
        if not q or q["observed_ts"]<=p["entry_ts"]:
            result["waiting"]+=1
            continue
        value=proceeds(p["entry_price"],q["price"],p["stake"])
        pct=(value/p["stake"]-1)*100
        hold=SOCIAL_HOLD if p["model"] in (SOCIAL,SOCIAL_GUARDED) else OSECE_HOLD
        stop=-20 if p["model"] in (SOCIAL,SOCIAL_GUARDED) else -12
        reason=("TAKE_PROFIT_NET_20" if pct>=20 else
                "STOP_LOSS_NET" if pct<=stop else
                "LIQUIDITY_DRAIN" if p["model"] not in (SOCIAL,SOCIAL_GUARDED) and
                 q["liquidity"]<.75*p["entry_liquidity"] else
                "TIME_LIMIT" if now-p["entry_ts"]>=hold else None)
        if not reason:
            continue
        db.execute("UPDATE micro_intents SET status='closed',exit_ts=?,"
                   "exit_price=?,usd_out=?,exit_reason=? WHERE id=?",
                   (q["observed_ts"],q["price"],value,reason,p["id"]))
        result["sold"]+=1
        log.info("MICRO PAPER SELL %s $%.2f mint=%s PNL=%+.4f (%+.2f%%) %s",
                 p["model"],p["stake"],p["mint"],value-p["stake"],pct,reason)
    for p in db.execute("SELECT * FROM micro_intents WHERE status='queued' "
                        "ORDER BY ready_ts,id").fetchall():
        if p["ready_ts"]>now:
            continue  # future causal receipt cannot expire before becoming actionable
        if now-p["ready_ts"]>MAX_ENTRY_WAIT:
            db.execute("UPDATE micro_intents SET status='expired',reason='missing_followup_quote' "
                       "WHERE id=?",(p["id"],))
            result["expired"]+=1
            continue
        q=_market(db,p["mint"],p["model"],now)
        if not q or q["observed_ts"]<p["ready_ts"]+ENTRY_LAG:
            result["waiting"]+=1
            continue
        if p["model"]==SOCIAL_GUARDED:
            reason=_guard_social_reason(db,p,q)
            if reason is not None:
                db.execute("UPDATE micro_intents SET status='rejected',reason=? "
                           "WHERE id=?",(reason,p["id"]))
                continue
        if q["observed_ts"]>p["ready_ts"]+MAX_ENTRY_WAIT:
            db.execute("UPDATE micro_intents SET status='expired',reason='late_quote' "
                       "WHERE id=?",(p["id"],))
            result["expired"]+=1
            continue
        if q["price"]>p["ready_price"]*1.10:
            db.execute("UPDATE micro_intents SET status='rejected',reason='chase_above_10pct'"
                       " WHERE id=?",(p["id"],))
            continue
        # One open position per coin per account and a cooldown after any buy.
        previous=db.execute(
            "SELECT MAX(entry_ts) FROM micro_intents WHERE model=? AND stake=? "
            "AND mint=? AND entry_ts IS NOT NULL",
            (p["model"],p["stake"],p["mint"])).fetchone()[0]
        cooldown=SOCIAL_COOLDOWN if p["model"] in (SOCIAL,SOCIAL_GUARDED) else OSECE_COOLDOWN
        if previous is not None and now-previous<cooldown:
            db.execute("UPDATE micro_intents SET status='rejected',reason='cooldown'"
                       " WHERE id=?",(p["id"],))
            continue
        maximum=(6 if p["stake"]==1 else 2) if p["model"]==SOCIAL_GUARDED else (15 if p["stake"]==1 else 4)
        if open_count(db,p["model"],p["stake"])>=maximum:
            db.execute("UPDATE micro_intents SET status='rejected',reason='slots_full'"
                       " WHERE id=?",(p["id"],))
            result["blocked_capacity"]+=1
            continue
        if cash(db,p["model"],p["stake"])+1e-8<p["stake"]:
            db.execute("UPDATE micro_intents SET status='rejected',reason='insufficient_cash'"
                       " WHERE id=?",(p["id"],))
            result["blocked_cash"]+=1
            continue
        db.execute(
            "UPDATE micro_intents SET status='open',entry_ts=?,entry_price=?,"
            "entry_liquidity=? WHERE id=?",
            (q["observed_ts"],q["price"],q["liquidity"],p["id"]))
        result["bought"]+=1
        log.info("MICRO PAPER BUY %s $%.2f mint=%s price=%.10g cash_after=$%.4f",
                 p["model"],p["stake"],p["mint"],q["price"],
                 cash(db,p["model"],p["stake"]))
    db.commit()
    return result


def report(db,now=None,limit=40):
    ensure(db)
    now=time.time() if now is None else now
    start=db.execute("SELECT value FROM micro_meta WHERE key='started_at'").fetchone()
    accounts=[]
    for model in MODELS:
        for size in SIZE_OPTIONS:
            closed=db.execute(
                "SELECT COUNT(*),"
                "SUM(CASE WHEN usd_out>stake THEN 1 ELSE 0 END),"
                "SUM(CASE WHEN usd_out<=stake THEN 1 ELSE 0 END),"
                "COALESCE(SUM(usd_out-stake),0) "
                "FROM micro_intents WHERE model=? AND stake=? AND status='closed'",
                (model,size)).fetchone()
            positions=[dict(r) for r in db.execute(
                "SELECT mint,entry_ts,entry_price,stake,exit_ts,exit_price,"
                "usd_out,exit_reason,status,reason FROM micro_intents "
                "WHERE model=? AND stake=? AND status IN ('open','closed') "
                "ORDER BY COALESCE(entry_ts,0) DESC LIMIT ?",
                (model,size,limit))]
            available=cash(db,model,size)
            open_total=open_count(db,model,size)
            stale=0
            estimated_open_value=0
            for p in positions:
                if p["status"]!="open":
                    continue
                q=_latest(db,p["mint"],model,now)
                if not q:
                    stale+=1
                    continue
                estimated_open_value+=proceeds(p["entry_price"],q["price"],size)
            if open_total>sum(p["status"]=="open" for p in positions):
                stale+=open_total-sum(p["status"]=="open" for p in positions)
            accounts.append({
                "model":model,"stake_usd":size,
                "starting_cash_usd":START_CASH,
                "cash_usd":round(available,4),
                "closed":int(closed[0]),"open":open_total,
                "wins":int(closed[1] or 0),"losses":int(closed[2] or 0),
                "net_realized_pnl_usd":round(closed[3],4),
                "estimated_open_liquidation_usd":round(estimated_open_value,4),
                "estimated_equity_usd":(round(available+estimated_open_value,4)
                                         if stale==0 else None),
                "open_unpriced":stale,
                "intents_waiting":db.execute(
                    "SELECT COUNT(*) FROM micro_intents WHERE model=? AND stake=? "
                    "AND status='queued'",(model,size)).fetchone()[0],
                "trades":positions,
            })
    # Keep the reason each candidate did NOT turn into a trade. This
    # prevents conflating "failed RugCheck" with "no timely market quote".
    rejected=[dict(r) for r in db.execute(
        "SELECT model,stake,status,reason,COUNT(*) count FROM micro_intents "
        "WHERE status IN ('rejected','expired') GROUP BY model,stake,status,reason "
        "ORDER BY model,stake,status,reason")]
    osece_diagnostics=[]
    if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                  "AND name='osece_signals'").fetchone():
        epoch=float(start[0]) if start else 0
        osece_diagnostics=[dict(r) for r in db.execute(
            "SELECT variant,state,COALESCE(reason,'') reason,COUNT(*) count "
            "FROM osece_signals WHERE launch_ts>=? "
            "GROUP BY variant,state,reason ORDER BY variant,state,reason",
            (epoch,))]
    return {
        "candidate_rejection_reasons":rejected,
        "osece_signal_diagnostics":osece_diagnostics,
        "mode":"paper_only_small_bankroll","activated_utc":(
            datetime.fromtimestamp(float(start[0]),timezone.utc).isoformat()
            if start else None),
        "asof_utc":datetime.fromtimestamp(now,timezone.utc).isoformat(),
        "fees":{"percent_fee_per_side":FEE_PER_SIDE,
                "percent_slippage_per_side":SLIPPAGE_PER_SIDE,
                "estimated_fixed_overhead_usd_per_side":NETWORK_OVERHEAD_USD_PER_SIDE},
        "accounts":accounts,
        "warnings":["No real orders, on-chain fills or checked Jupiter swap routes",
                    "A $1 trade may be unavailable or unprofitable after real fees",
                    "Estimated equity is null whenever an open price is stale",
                    "Historical $250 ledgers are preserved separately"]
    }


def main():
    import argparse
    p=argparse.ArgumentParser(description="Independent $25 micro paper trading accounts")
    p.add_argument("--db",default="memeradar.db")
    p.add_argument("command",nargs="?",default="report",choices=("report",))
    args=p.parse_args()
    db=sqlite3.connect(args.db)
    db.row_factory=sqlite3.Row
    print(json.dumps(report(db),indent=2,allow_nan=False))


if __name__=="__main__":
    main()
