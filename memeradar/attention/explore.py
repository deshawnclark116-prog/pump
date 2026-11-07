"""Untrained attention-catalyst PAPER experiment.

Explicitly not the validated delay model: no "waiting money" forecasts and no
probabilities. This forward-only experiment answers: which trades WOULD the
current sources have made, after observed RugCheck, spread-like costs, and
a delayed market quote? No wallet, RPC, signature or live execution path.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import math
import os
import time

from .backtest import eligible, percent_return, realized_usd

log = logging.getLogger(__name__)

VERSIONS = ("attention_social_probe_v0", "attention_gecko_trend_probe_v0")
INITIAL_CASH = 250.0
TRADE_USD = 10.0
MAX_OPEN = 4
COOLDOWN_SEC = 3600
MAX_SIGNAL_AGE_SEC = 900
QUOTE_FRESH_SEC = 90
ENTRY_DELAY_SEC = 20
ENTRY_WINDOW_SEC = 180
MAX_PRICE_RUNUP = 0.10
TAKE_NET_PCT = 20.0
STOP_NET_PCT = -20.0
HOLD_SEC = 1800

SCHEMA = """
CREATE TABLE IF NOT EXISTS attention_explore_meta (
 key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS attention_explore_signals (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 variant TEXT NOT NULL, mint TEXT NOT NULL, source TEXT NOT NULL,
 source_ts REAL NOT NULL, signal_ts REAL NOT NULL,
 signal_price REAL NOT NULL, signal_liquidity REAL NOT NULL,
 buy_proxy_m5 REAL NOT NULL, buyers_m5 INTEGER NOT NULL,
 sellers_m5 INTEGER NOT NULL, source_rank INTEGER,
 status TEXT NOT NULL, details TEXT NOT NULL,
 UNIQUE(variant,mint,source_ts));
CREATE INDEX IF NOT EXISTS attention_explore_signal_state
 ON attention_explore_signals(status,signal_ts);
CREATE TABLE IF NOT EXISTS attention_explore_trades (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 variant TEXT NOT NULL, mint TEXT NOT NULL, source TEXT NOT NULL,
 signal_id INTEGER NOT NULL UNIQUE,
 entry_ts REAL NOT NULL, entry_price REAL NOT NULL, usd_in REAL NOT NULL,
 exit_ts REAL, exit_price REAL, usd_out REAL, reason TEXT,
 FOREIGN KEY(signal_id) REFERENCES attention_explore_signals(id));
CREATE INDEX IF NOT EXISTS attention_explore_trade_open
 ON attention_explore_trades(variant,exit_ts,mint);
CREATE TABLE IF NOT EXISTS attention_explore_notifications (
 trade_id INTEGER NOT NULL, action TEXT NOT NULL, notified_ts REAL NOT NULL,
 PRIMARY KEY(trade_id,action));
"""


def ensure(db):
    db.executescript(SCHEMA)


def enabled():
    return os.environ.get("ATTENTION_EXPERIMENTAL_PAPER") == "1"


def activated_at(db, now):
    """Start only from new observations; never create pseudo-forward old trades."""
    row = db.execute(
        "SELECT value FROM attention_explore_meta WHERE key='activated_at'").fetchone()
    if row:
        return float(row[0])
    db.execute("INSERT INTO attention_explore_meta VALUES ('activated_at',?)",
               (str(now),))
    db.commit()
    return now


def _latest_quote(db, mint, now):
    return db.execute(
        "SELECT * FROM attention_chain WHERE mint=? AND observed_ts<=? "
        "ORDER BY observed_ts DESC LIMIT 1", (mint, now)).fetchone()


def _latest_events(db, variant, now, started):
    """Only reception-timed source events observed AFTER paper experiment start."""
    since = max(started, now-MAX_SIGNAL_AGE_SEC)
    if variant == VERSIONS[0]:
        rows = db.execute(
            "SELECT mint,MAX(observed_ts) source_ts, NULL AS rank "
            "FROM attention_hits "
            "WHERE source IN ('bluesky','4chan_biz','telegram','x','reddit') "
            "AND observed_ts>? AND observed_ts<=? GROUP BY mint "
            "ORDER BY source_ts DESC LIMIT 150", (since, now)).fetchall()
        return [(r["mint"],r["source_ts"],None) for r in rows]
    rows = db.execute(
        "SELECT mint,observed_ts,rank FROM attention_trends "
        "WHERE source='geckoterminal_trending' AND mint IS NOT NULL "
        "AND rank BETWEEN 1 AND 10 AND observed_ts>? AND observed_ts<=? "
        "ORDER BY observed_ts DESC,rank ASC LIMIT 300", (since, now)).fetchall()
    found=set()
    result=[]
    for r in rows:
        if r["mint"] in found:
            continue
        found.add(r["mint"])
        result.append((r["mint"],r["observed_ts"],r["rank"]))
    return result


def _cash(db, variant):
    row=db.execute(
        "SELECT COALESCE(SUM(usd_in),0),COALESCE(SUM(usd_out),0) "
        "FROM attention_explore_trades WHERE variant=?",(variant,)).fetchone()
    return INITIAL_CASH-row[0]+row[1]


def _open(db,variant):
    return db.execute(
        "SELECT COUNT(*) FROM attention_explore_trades "
        "WHERE variant=? AND exit_ts IS NULL",(variant,)).fetchone()[0]


def _close_positions(db, now):
    exits=0
    for pos in db.execute(
        "SELECT * FROM attention_explore_trades WHERE exit_ts IS NULL "
        "ORDER BY entry_ts").fetchall():
        row=db.execute(
            "SELECT observed_ts,price_usd FROM attention_chain "
            "WHERE mint=? AND observed_ts>? AND observed_ts<=? "
            "ORDER BY observed_ts DESC LIMIT 1",
            (pos["mint"],pos["entry_ts"],now)).fetchone()
        if not row or now-row["observed_ts"]>QUOTE_FRESH_SEC:
            continue  # unknown quote is NOT a simulated fill
        pct=percent_return(pos["entry_price"],row["price_usd"])
        reason=("TAKE_PROFIT_NET_20" if pct>=TAKE_NET_PCT else
                "STOP_LOSS_NET_20" if pct<=STOP_NET_PCT else
                "TIME_30M" if now-pos["entry_ts"]>=HOLD_SEC else None)
        if not reason:
            continue
        usd_out=realized_usd(pos["entry_price"],row["price_usd"],pos["usd_in"])
        db.execute(
            "UPDATE attention_explore_trades "
            "SET exit_ts=?,exit_price=?,usd_out=?,reason=? WHERE id=?",
            (row["observed_ts"],row["price_usd"],usd_out,reason,pos["id"]))
        exits+=1
        log.info("ATTENTION EXPLORE PAPER SELL %s %s mint=%s PNL=%+.2f (%+.1f%% net) %s",
                 pos["variant"],pos["source"],pos["mint"],
                 usd_out-pos["usd_in"],pct,reason)
    return exits


def _fill_signals(db,now):
    entries=0
    for sig in db.execute(
        "SELECT * FROM attention_explore_signals WHERE status='queued' "
        "ORDER BY signal_ts,id").fetchall():
        if now-sig["signal_ts"]>ENTRY_WINDOW_SEC:
            db.execute("UPDATE attention_explore_signals SET status='expired' WHERE id=?",
                       (sig["id"],))
            continue
        # No fills at the signal quote or at a historical same-tick print.
        row=db.execute(
            "SELECT observed_ts,price_usd,liquidity_usd FROM attention_chain "
            "WHERE mint=? AND observed_ts>=? AND observed_ts<=? "
            "ORDER BY observed_ts LIMIT 1",
            (sig["mint"],sig["signal_ts"]+ENTRY_DELAY_SEC,now)).fetchone()
        if not row or now-row["observed_ts"]>QUOTE_FRESH_SEC:
            continue
        variant=sig["variant"]
        if not eligible(db,sig["mint"],row["observed_ts"],row["liquidity_usd"]):
            status="risk_rejected"
        elif not row["price_usd"] or row["price_usd"]>sig["signal_price"]*(1+MAX_PRICE_RUNUP):
            status="late_or_pumped"
        elif _open(db,variant)>=MAX_OPEN:
            status="no_slots"
        elif _cash(db,variant)<TRADE_USD:
            status="no_cash"
        elif db.execute(
            "SELECT 1 FROM attention_explore_trades WHERE mint=? AND variant=? "
            "AND exit_ts IS NULL",(sig["mint"],variant)).fetchone():
            status="already_open"
        else:
            db.execute(
                "INSERT INTO attention_explore_trades "
                "(variant,mint,source,signal_id,entry_ts,entry_price,usd_in) "
                "VALUES (?,?,?,?,?,?,?)",
                (variant,sig["mint"],sig["source"],sig["id"],
                 row["observed_ts"],row["price_usd"],TRADE_USD))
            status="paper_bought"
            entries+=1
            log.info("ATTENTION EXPLORE PAPER BUY %s %s mint=%s $%.2f at %.10g "
                     "liquidity=$%.0f (after real source, RugCheck, delayed quote)",
                     variant,sig["source"],sig["mint"],TRADE_USD,row["price_usd"],
                     row["liquidity_usd"])
        db.execute("UPDATE attention_explore_signals SET status=? WHERE id=?",
                   (status,sig["id"]))
    return entries


def _queue_signals(db, now, started):
    queued=0
    for variant in VERSIONS:
        source="social" if variant==VERSIONS[0] else "geckoterminal_trending"
        for mint, source_ts, rank in _latest_events(db,variant,now,started):
            if db.execute(
                "SELECT 1 FROM attention_explore_signals "
                "WHERE variant=? AND mint=? AND source_ts=?",
                (variant,mint,source_ts)).fetchone():
                continue
            # The same coin may not repeatedly churn a paper bankroll.
            last=db.execute(
                "SELECT MAX(entry_ts) FROM attention_explore_trades "
                "WHERE variant=? AND mint=?",(variant,mint)).fetchone()[0]
            if last is not None and now-last<COOLDOWN_SEC:
                continue
            if _open(db,variant)>=MAX_OPEN or _cash(db,variant)<TRADE_USD:
                continue
            row=_latest_quote(db,mint,now)
            if not row or not(source_ts<=row["observed_ts"]<=now) or now-row["observed_ts"]>QUOTE_FRESH_SEC:
                continue
            liq=row["liquidity_usd"]
            buyers=row["buys_m5"]; sellers=row["sells_m5"]
            vol=row["vol_m5"]; proxy=row["buy_volume_proxy_m5"]
            if not all(math.isfinite(v) for v in (liq,vol,proxy,row["price_usd"])):
                continue
            # Conservative enough for first paper receipts; NOT calibrated.
            if liq<15000 or row["price_usd"]<=0 or (row["market_cap"] or 0)<15000:
                continue
            if source=="social":
                meets=(vol>=500 and buyers>=5 and buyers>=sellers and proxy>=250)
            else:
                meets=(rank<=10 and vol>=1500 and buyers>=12
                       and buyers>=1.1*max(1,sellers) and proxy>=750)
            if not meets or not eligible(db,mint,row["observed_ts"],liq):
                continue
            details=json.dumps({"status":"untrained_heuristic","rank":rank,
                                "source_event_at":source_ts,"signal_quote_at":row["observed_ts"],
                                "buys_m5":buyers,"sells_m5":sellers,
                                "vol_m5":vol,"buy_proxy_m5":proxy,
                                "note":"no validated waiting-money forecast"})
            db.execute(
                "INSERT OR IGNORE INTO attention_explore_signals "
                "(variant,mint,source,source_ts,signal_ts,signal_price,"
                "signal_liquidity,buy_proxy_m5,buyers_m5,sellers_m5,"
                "source_rank,status,details) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (variant,mint,source,source_ts,row["observed_ts"],row["price_usd"],
                 liq,proxy,buyers,sellers,rank,"queued",details))
            queued+=1
            log.info("ATTENTION EXPLORE SIGNAL %s mint=%s source=%s rank=%s "
                     "buys=%d sells=%d vol5=$%.0f queued NEXT quote",
                     variant,mint,source,rank,buyers,sellers,vol)
    return queued


def paper_tick(db, now=None):
    """One 30-second forward step; never reads observations later than now."""
    if not enabled():
        return {"status":"disabled","reason":"ATTENTION_EXPERIMENTAL_PAPER != 1"}
    now=time.time() if now is None else now
    ensure(db)
    started=activated_at(db,now)
    exits=_close_positions(db,now)
    entries=_fill_signals(db,now)
    queued=_queue_signals(db,now,started)
    db.commit()
    return {"status":"untrained_exploratory_paper","queued":queued,
            "entries":entries,"exits":exits}


def report(db, limit=50):
    ensure(db)
    start=db.execute(
        "SELECT value FROM attention_explore_meta WHERE key='activated_at'").fetchone()
    books=[]
    for variant in VERSIONS:
        rows=db.execute(
            "SELECT * FROM attention_explore_trades WHERE variant=? "
            "ORDER BY entry_ts DESC LIMIT ?",(variant,limit)).fetchall()
        tally=db.execute(
            "SELECT COUNT(*),"
            "SUM(CASE WHEN exit_ts IS NULL THEN 1 ELSE 0 END),"
            "SUM(CASE WHEN exit_ts IS NOT NULL AND usd_out>usd_in THEN 1 ELSE 0 END),"
            "SUM(CASE WHEN exit_ts IS NOT NULL AND usd_out<=usd_in THEN 1 ELSE 0 END),"
            "COALESCE(SUM(CASE WHEN exit_ts IS NOT NULL THEN usd_out-usd_in END),0)"
            "FROM attention_explore_trades WHERE variant=?",(variant,)).fetchone()
        trades=[]
        for r in rows:
            r=dict(r)
            label=r["mint"]
            try:
                sym=db.execute("SELECT symbol FROM tokens WHERE chain='solana' "
                               "AND address=?",(label,)).fetchone()
                if sym and sym[0]:
                    label=sym[0]
            except Exception: # standalone attention-only database
                pass
            trades.append({"coin":label,"mint":r["mint"],"source":r["source"],
                           "entry_utc":datetime.fromtimestamp(r["entry_ts"],timezone.utc).isoformat(),
                           "exit_utc":(datetime.fromtimestamp(r["exit_ts"],timezone.utc).isoformat()
                                       if r["exit_ts"] else None),
                           "entry_price":r["entry_price"],"exit_price":r["exit_price"],
                           "pnl":round(r["usd_out"]-r["usd_in"],2) if r["usd_out"] is not None else None,
                           "reason":r["reason"] or "OPEN"})
        books.append({"model":variant,"paper_only":True,"validated":False,
                      "closed":int(tally[0]-(tally[1] or 0)),
                      "open":int(tally[1] or 0),"wins":int(tally[2] or 0),
                      "losses":int(tally[3] or 0),"realized_pnl":round(tally[4],2),
                      "cash":round(_cash(db,variant),2),"trades":trades})
    return {"status":"exploratory_forward_paper_not_validated",
            "activated_utc":(datetime.fromtimestamp(float(start[0]),timezone.utc).isoformat()
                             if start else None),
            "rules":{"entry":"verified recent mint/Gecko ranking + market buyers + RugCheck passed; delayed quote",
                     "fees_per_side":0.0125,"slippage_per_side":0.01,
                     "take_profit_net_pct":TAKE_NET_PCT,"stop_loss_net_pct":STOP_NET_PCT,
                     "max_hold_minutes":HOLD_SEC//60,"paper_stake_usd":TRADE_USD},
            "books":books}
