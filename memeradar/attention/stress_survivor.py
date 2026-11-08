"""Stress-Survivor v0: cross-sectional sell-pressure resilience, PAPER ONLY.

Unlike breakout, buyer-flow, or dip-reclaim, this experiment seeks a token
whose price and liquidity survive a *contemporaneous decline in its peers*
despite SELL counts exceeding BUY counts in its five-minute DexScreener
window. We then require another independently received quote showing that
the token survived while the peer selloff stopped worsening. Entries occur
on a later quote, not when the signal was observed.

This is an UNVALIDATED hypothesis. DexScreener reports transaction COUNTS,
not buy/sell dollars. Quotes are indications, not executable DEX swaps.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import logging
import math
import os
import statistics
import time

from .backtest import eligible, realized_usd, percent_return

log = logging.getLogger(__name__)

VERSION = "stress_survivor_v0_frozen_20261108"
START_CASH = 250.0
TRADE_USD = 10.0
MAX_OPEN = 3
COOLDOWN_SEC = 4*3600
WATCH_SEC = 210
CONFIRM_MIN_SEC = 60
ENTRY_DELAY_SEC = 20
FILL_SEC = 180
MAX_QUOTE_AGE = 90
MIN_PEERS = 10
MARKET_MEDIAN_MAX = -0.006    # peer median <= -0.6% over about 5m
MARKET_DOWN_FRACTION = 0.60   # at least 60% of peers declining
TOKEN_MIN_RETURN = -0.008
TOKEN_MAX_RETURN = 0.015
MIN_PEER_OUTPERFORMANCE = 0.012
MIN_LIQUIDITY = 20_000.0
MIN_MCAP = 25_000.0
LIQUIDITY_STABILITY = 0.96
SELL_BUY_RATIO = 1.10
MIN_SELL_COUNT = 10
MIN_VOL_M5 = 2_000.0
TP_NET_PCT = 12.0
STOP_NET_PCT = -10.0
MAX_HOLD_SEC = 15*60

SCHEMA = """
CREATE TABLE IF NOT EXISTS stress_survivor_meta (
 key TEXT PRIMARY KEY,value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS stress_survivor_signals (
 id INTEGER PRIMARY KEY AUTOINCREMENT, version TEXT NOT NULL,
 mint TEXT NOT NULL, watch_ts REAL NOT NULL,
 watch_price REAL NOT NULL, watch_liquidity REAL NOT NULL,
 token_ret5 REAL NOT NULL, peer_median_ret5 REAL NOT NULL,
 peer_down_fraction REAL NOT NULL, peer_count INTEGER NOT NULL,
 buys_m5 INTEGER NOT NULL, sells_m5 INTEGER NOT NULL,
 confirmed_ts REAL, confirmed_price REAL,
 state TEXT NOT NULL, reason TEXT,
 UNIQUE(version,mint,watch_ts));
CREATE INDEX IF NOT EXISTS stress_survivor_signal_state
 ON stress_survivor_signals(version,state,watch_ts);
CREATE TABLE IF NOT EXISTS stress_survivor_trades (
 id INTEGER PRIMARY KEY AUTOINCREMENT,version TEXT NOT NULL,
 signal_id INTEGER NOT NULL UNIQUE,mint TEXT NOT NULL,
 entry_ts REAL NOT NULL,entry_price REAL NOT NULL,
 entry_liquidity REAL NOT NULL,usd_in REAL NOT NULL,
 exit_ts REAL,exit_price REAL,usd_out REAL,exit_reason TEXT);
CREATE INDEX IF NOT EXISTS stress_survivor_open
 ON stress_survivor_trades(version,exit_ts,mint);
"""


def ensure(db):
    db.executescript(SCHEMA)


def enabled():
    return os.environ.get("STRESS_SURVIVOR_PAPER_ENABLED") == "1"


def _started(db,now):
    r=db.execute("SELECT value FROM stress_survivor_meta WHERE key='started'").fetchone()
    if r:
        return float(r[0])
    db.execute("INSERT INTO stress_survivor_meta VALUES ('started',?)",(str(now),))
    db.commit()
    return now


def _cohort(db,now):
    """As-of peer study: never touch a price received after now.

    Current quote <=90 s old; baseline quote available at approximately
    T-5 minutes (the latest one no older than 100 seconds from the target).
    Cohort is assembled from unique Solana mints, NOT multiple pools.
    """
    recent=db.execute(
        "SELECT x.* FROM attention_chain x "
        "JOIN (SELECT mint,MAX(observed_ts) ts FROM attention_chain "
        "WHERE observed_ts>? AND observed_ts<=? GROUP BY mint) q "
        "ON x.mint=q.mint AND x.observed_ts=q.ts",
        (now-MAX_QUOTE_AGE,now)).fetchall()
    result={}
    for row in recent:
        if not (row["price_usd"]>0 and row["liquidity_usd"]>=MIN_LIQUIDITY
                and (row["market_cap"] or 0)>=MIN_MCAP):
            continue
        target=row["observed_ts"]-300
        prev=db.execute(
            "SELECT price_usd,liquidity_usd,observed_ts,pair FROM attention_chain "
            "WHERE mint=? AND observed_ts>? AND observed_ts<=? "
            "ORDER BY observed_ts DESC LIMIT 1",
            (row["mint"],target-100,target)).fetchone()
        if not prev or prev["price_usd"]<=0 or prev["liquidity_usd"]<=0:
            continue
        if not row["pair"] or row["pair"]!=prev["pair"]:
            continue  # pool migration creates artificial price jumps
        price_ret=row["price_usd"]/prev["price_usd"]-1
        if not math.isfinite(price_ret):
            continue
        result[row["mint"]]={
            "mint":row["mint"],"ts":row["observed_ts"],
            "price":row["price_usd"],"liquidity":row["liquidity_usd"],
            "prev_liquidity":prev["liquidity_usd"],"market_cap":row["market_cap"],
            "ret5":price_ret,"buys":row["buys_m5"],"sells":row["sells_m5"],
            "vol":row["vol_m5"]}
    return result


def _peer_stats(cohort,exclude=None):
    arr=[r["ret5"] for mint,r in cohort.items() if mint!=exclude]
    if len(arr)<MIN_PEERS:
        return None
    return {"median":statistics.median(arr),"down":sum(x<0 for x in arr)/len(arr),
            "count":len(arr)}


def _candidate(row,peer):
    if not peer or peer["median"]>MARKET_MEDIAN_MAX or peer["down"]<MARKET_DOWN_FRACTION:
        return False
    if not TOKEN_MIN_RETURN<=row["ret5"]<=TOKEN_MAX_RETURN:
        return False
    if row["ret5"]-peer["median"]<MIN_PEER_OUTPERFORMANCE:
        return False
    if row["liquidity"]<row["prev_liquidity"]*LIQUIDITY_STABILITY:
        return False
    if (row["sells"]<MIN_SELL_COUNT or
            row["sells"]<row["buys"]*SELL_BUY_RATIO or
            row["vol"]<MIN_VOL_M5):
        return False
    return True


def _cash(db):
    r=db.execute(
        "SELECT COALESCE(SUM(usd_in),0),COALESCE(SUM(usd_out),0) "
        "FROM stress_survivor_trades WHERE version=?",(VERSION,)).fetchone()
    return START_CASH-r[0]+r[1]


def _open(db):
    return db.execute(
        "SELECT COUNT(*) FROM stress_survivor_trades "
        "WHERE version=? AND exit_ts IS NULL",(VERSION,)).fetchone()[0]


def _latest_quote(db,mint,now):
    return db.execute(
        "SELECT * FROM attention_chain WHERE mint=? "
        "AND observed_ts>? AND observed_ts<=? "
        "ORDER BY observed_ts DESC LIMIT 1",
        (mint,now-MAX_QUOTE_AGE,now)).fetchone()


def _close(db,now):
    n=0
    for pos in db.execute(
        "SELECT * FROM stress_survivor_trades "
        "WHERE version=? AND exit_ts IS NULL ORDER BY entry_ts",
        (VERSION,)).fetchall():
        quote=_latest_quote(db,pos["mint"],now)
        if not quote or quote["observed_ts"]<=pos["entry_ts"]:
            continue
        net=percent_return(pos["entry_price"],quote["price_usd"])
        why=("TAKE_NET_12" if net>=TP_NET_PCT else
             "STOP_NET_10" if net<=STOP_NET_PCT else
             "LIQUIDITY_DRAIN" if quote["liquidity_usd"]<pos["entry_liquidity"]*.80 else
             "TIME_15M" if now-pos["entry_ts"]>=MAX_HOLD_SEC else None)
        if not why:
            continue
        usd_out=realized_usd(pos["entry_price"],quote["price_usd"],pos["usd_in"])
        db.execute(
            "UPDATE stress_survivor_trades "
            "SET exit_ts=?,exit_price=?,usd_out=?,exit_reason=? WHERE id=?",
            (quote["observed_ts"],quote["price_usd"],usd_out,why,pos["id"]))
        log.info("STRESS SURVIVOR PAPER SELL %s $10 -> $%.2f net=%+.2f (%+.1f%%) %s",
                 pos["mint"],usd_out,usd_out-pos["usd_in"],net,why)
        n+=1
    return n


def _fill(db,now):
    n=0
    rows=db.execute(
        "SELECT * FROM stress_survivor_signals "
        "WHERE version=? AND state='queued' ORDER BY confirmed_ts,id",
        (VERSION,)).fetchall()
    for s in rows:
        if now-s["confirmed_ts"]>FILL_SEC:
            db.execute("UPDATE stress_survivor_signals SET state='expired',"
                       "reason='fill window expired' WHERE id=?",(s["id"],))
            continue
        quote=db.execute(
            "SELECT * FROM attention_chain WHERE mint=? "
            "AND observed_ts>=? AND observed_ts<=? ORDER BY observed_ts LIMIT 1",
            (s["mint"],s["confirmed_ts"]+ENTRY_DELAY_SEC,now)).fetchone()
        if not quote or now-quote["observed_ts"]>MAX_QUOTE_AGE:
            continue
        px=quote["price_usd"]
        if not eligible(db,s["mint"],quote["observed_ts"],quote["liquidity_usd"]):
            status="risk_rejected"
        elif (quote["liquidity_usd"]<MIN_LIQUIDITY or
              quote["liquidity_usd"]<s["watch_liquidity"]*.97):
            status="liquidity_faded"
        elif (px<s["watch_price"]*.97 or px>s["watch_price"]*1.04):
            status="price_broke_resilience_or_chased"
        elif _open(db)>=MAX_OPEN:
            status="capacity"
        elif _cash(db)<TRADE_USD:
            status="cash"
        elif db.execute(
            "SELECT 1 FROM stress_survivor_trades "
            "WHERE mint=? AND version=? AND exit_ts IS NULL",
            (s["mint"],VERSION)).fetchone():
            status="duplicate_open"
        else:
            db.execute(
                "INSERT INTO stress_survivor_trades "
                "(version,signal_id,mint,entry_ts,entry_price,entry_liquidity,usd_in) "
                "VALUES (?,?,?,?,?,?,?)",
                (VERSION,s["id"],s["mint"],quote["observed_ts"],
                 px,quote["liquidity_usd"],TRADE_USD))
            log.info("STRESS SURVIVOR PAPER BUY %s $10 at %.10g "
                     "peer5=%+.2f%% sell/buy=%d/%d liq=$%.0f",
                     s["mint"],px,s["peer_median_ret5"]*100,
                     s["sells_m5"],s["buys_m5"],quote["liquidity_usd"])
            status="paper_bought"
            n+=1
        db.execute(
            "UPDATE stress_survivor_signals SET state=?,reason=? WHERE id=?",
            (status,None if status=="paper_bought" else status,s["id"]))
    return n


def _confirm(db,now,cohort):
    n=0
    for s in db.execute(
        "SELECT * FROM stress_survivor_signals "
        "WHERE version=? AND state='watch' ORDER BY watch_ts",
        (VERSION,)).fetchall():
        age=now-s["watch_ts"]
        if age>WATCH_SEC:
            db.execute("UPDATE stress_survivor_signals SET state='expired',"
                       "reason='watch expired' WHERE id=?",(s["id"],))
            continue
        if age<CONFIRM_MIN_SEC:
            continue
        row=cohort.get(s["mint"])
        peer=_peer_stats(cohort,exclude=s["mint"])
        if not row or not peer or row["ts"]<=s["watch_ts"]:
            continue
        # Resilience must persist at a newly received quote. The cross-market
        # decline cannot be notably WORSE than at the first observation.
        price_ok=s["watch_price"]*.99<=row["price"]<=s["watch_price"]*1.035
        liq_ok=row["liquidity"]>=s["watch_liquidity"]*.97
        stress_ok=peer["median"]>=s["peer_median_ret5"]-.002
        if not (price_ok and liq_ok and stress_ok):
            if not price_ok or not liq_ok:
                db.execute("UPDATE stress_survivor_signals "
                           "SET state='rejected',reason='resilience failed' WHERE id=?",(s["id"],))
            continue
        if not eligible(db,s["mint"],row["ts"],row["liquidity"]):
            db.execute("UPDATE stress_survivor_signals "
                       "SET state='rejected',reason='safety expired' WHERE id=?",(s["id"],))
            continue
        db.execute(
            "UPDATE stress_survivor_signals "
            "SET state='queued',confirmed_ts=?,confirmed_price=? WHERE id=?",
            (row["ts"],row["price"],s["id"]))
        n+=1
        log.info("STRESS SURVIVOR CONFIRMED %s peer5=%+.2f%% queued later quote",
                 s["mint"],peer["median"]*100)
    return n


def _watch(db,now,started,cohort):
    n=0
    if now<=started or len(cohort)<MIN_PEERS+1:
        return 0
    for mint,row in cohort.items():
        if row["ts"]<=started or now-row["ts"]>MAX_QUOTE_AGE:
            continue
        peer=_peer_stats(cohort,exclude=mint)
        if not _candidate(row,peer):
            continue
        if not eligible(db,mint,row["ts"],row["liquidity"]):
            continue
        if db.execute(
            "SELECT 1 FROM stress_survivor_signals "
            "WHERE version=? AND mint=? AND state IN ('watch','queued')",
            (VERSION,mint)).fetchone():
            continue
        last=db.execute(
            "SELECT MAX(watch_ts) FROM stress_survivor_signals "
            "WHERE version=? AND mint=?",(VERSION,mint)).fetchone()[0]
        if last and now-last<COOLDOWN_SEC:
            continue
        last_trade=db.execute(
            "SELECT MAX(entry_ts) FROM stress_survivor_trades "
            "WHERE version=? AND mint=?",(VERSION,mint)).fetchone()[0]
        if last_trade and now-last_trade<COOLDOWN_SEC:
            continue
        if _open(db)>=MAX_OPEN or _cash(db)<TRADE_USD:
            continue
        db.execute(
            "INSERT OR IGNORE INTO stress_survivor_signals "
            "(version,mint,watch_ts,watch_price,watch_liquidity,token_ret5,"
            "peer_median_ret5,peer_down_fraction,peer_count,buys_m5,sells_m5,state) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,'watch')",
            (VERSION,mint,row["ts"],row["price"],row["liquidity"],row["ret5"],
             peer["median"],peer["down"],peer["count"],row["buys"],row["sells"]))
        log.info("STRESS SURVIVOR WATCH %s peer_median=%+.2f%% "
                 "peers_down=%.0f%% own_return=%+.2f%% sells/buys=%d/%d",
                 mint,peer["median"]*100,peer["down"]*100,row["ret5"]*100,
                 row["sells"],row["buys"])
        n+=1
    return n


def paper_tick(db,now=None):
    if not enabled():
        return {"status":"disabled"}
    now=time.time() if now is None else now
    ensure(db)
    started=_started(db,now)
    closed=_close(db,now)
    entered=_fill(db,now)
    cohort=_cohort(db,now)
    queued=_confirm(db,now,cohort)
    watched=_watch(db,now,started,cohort)
    db.commit()
    return {"status":"untrained_forward_paper","cohort":len(cohort),
            "watched":watched,"confirmed":queued,"entered":entered,"closed":closed}


def report(db,limit=100):
    ensure(db)
    started=db.execute("SELECT value FROM stress_survivor_meta "
                       "WHERE key='started'").fetchone()
    counts={r["state"]:r["n"] for r in db.execute(
        "SELECT state,COUNT(*) n FROM stress_survivor_signals WHERE version=? "
        "GROUP BY state",(VERSION,))}
    total=db.execute(
        "SELECT COUNT(*) n,SUM(CASE WHEN exit_ts IS NULL THEN 1 ELSE 0 END) open,"
        "SUM(CASE WHEN exit_ts IS NOT NULL AND usd_out>usd_in THEN 1 ELSE 0 END) wins,"
        "SUM(CASE WHEN exit_ts IS NOT NULL AND usd_out<=usd_in THEN 1 ELSE 0 END) losses,"
        "COALESCE(SUM(CASE WHEN exit_ts IS NOT NULL THEN usd_out-usd_in END),0) pnl "
        "FROM stress_survivor_trades WHERE version=?",(VERSION,)).fetchone()
    records=[]
    for r in db.execute(
        "SELECT t.*,s.watch_ts,s.peer_median_ret5,s.peer_down_fraction,"
        "s.token_ret5,s.buys_m5,s.sells_m5 FROM stress_survivor_trades t "
        "JOIN stress_survivor_signals s ON s.id=t.signal_id "
        "WHERE t.version=? ORDER BY t.entry_ts DESC LIMIT ?",(VERSION,limit)):
        r=dict(r)
        symbol=None
        try:
            x=db.execute("SELECT symbol FROM tokens "
                         "WHERE chain='solana' AND address=?",(r["mint"],)).fetchone()
            symbol=x[0] if x and x[0] else None
        except Exception:
            pass
        records.append({
            "mint":r["mint"],"symbol":symbol,
            "signal_utc":datetime.fromtimestamp(r["watch_ts"],timezone.utc).isoformat(),
            "entry_utc":datetime.fromtimestamp(r["entry_ts"],timezone.utc).isoformat(),
            "exit_utc":(datetime.fromtimestamp(r["exit_ts"],timezone.utc).isoformat()
                        if r["exit_ts"] else None),
            "entry_price":r["entry_price"],"exit_price":r["exit_price"],
            "peer_median_5m_pct":round(r["peer_median_ret5"]*100,2),
            "token_5m_pct":round(r["token_ret5"]*100,2),
            "sell_count":r["sells_m5"],"buy_count":r["buys_m5"],
            "net_pnl":(round(r["usd_out"]-r["usd_in"],2) if r["exit_ts"] else None),
            "reason":r["exit_reason"] or "OPEN"})
    return {"model":VERSION,"status":"unvalidated_forward_paper_experiment",
            "started_utc":(datetime.fromtimestamp(float(started[0]),timezone.utc).isoformat()
                           if started else None),
            "signal_states":counts,"closed":total["n"]-(total["open"] or 0),
            "open":total["open"] or 0,"wins":total["wins"] or 0,
            "losses":total["losses"] or 0,"realized_pnl":round(total["pnl"],2),
            "cash":round(_cash(db),2),"trades":records,
            "execution_warning":"Indicative quotes, assumed costs, no real swap liquidity simulation"}
