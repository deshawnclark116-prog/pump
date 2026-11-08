"""Open-Source Entropy Collision Engine v1 — untrained, independent PAPER experiment.

Freeze the causal order: public content RECEIVED before token creation,
creation before market confirmation, confirmation before a different entry
quote. No lookahead, symbol-only inference, wallets or real orders.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import html
import json
import logging
import math
import os
import re
import sqlite3
import time
import unicodedata

from ..attention.attention import valid_mint
from ..attention.backtest import realized_usd, percent_return, eligible

log = logging.getLogger(__name__)
VERSION = "osece_v1_frozen_20261108"
SINGLE = VERSION + "_single_source"
COLLISION = VERSION + "_cross_source"
BOOKS = (SINGLE, COLLISION)
INITIAL_CASH = 250
TRADE_USD = 10
MAX_OPEN = 3
MAX_CULTURE_LEAD_SEC = 1800
SIGNAL_LIFETIME_SEC = 2700
QUOTE_MAX_AGE_SEC = 110
ENTRY_DELAY_SEC = 20
TAKE_NET = 20
STOP_NET = -12
HOLD_SEC = 20 * 60
COOLDOWN_SEC = 2 * 3600

STOP = frozenset(" ".join((
    "solana bitcoin ethereum crypto token tokens pump fun moon trading markets",
    "good morning thank you breaking news official update live today tonight",
    "google wikipedia bluesky twitter twitch reddit github meme memes"
)).split())
BLOCK = frozenset(("goodmorning","thankyou","breakingnews","officialupdate",
                   "bitcoin","ethereum","solana","crypto","cryptocurrency",
                   "twitter","bluesky","newpost","clickhere","watchlive"))
HASHTAG = re.compile(r"(?<!\w)#([A-Za-z][A-Za-z0-9_]{3,35})")
TITLE = re.compile(r"(?<![A-Za-z])(?:[A-Z][a-z]{2,}|[A-Z]{4,12})\s+(?:[A-Z][a-z]{2,}|[A-Z]{4,12})(?:\s+(?:[A-Z][a-z]{2,}|[A-Z]{4,12}))?(?![A-Za-z])")
WORD = re.compile(r"[A-Za-z0-9]+")
SOURCES = ("bluesky","wikimedia","google_trends","gdelt","hackernews","nostr")
SCHEMA = """
CREATE TABLE IF NOT EXISTS osece_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS osece_sources (
 source TEXT PRIMARY KEY, checked_ts REAL NOT NULL,
 state TEXT NOT NULL, detail TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS osece_events (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 source TEXT NOT NULL, source_key TEXT NOT NULL,
 actor_key TEXT NOT NULL, term TEXT NOT NULL,
 term_key TEXT NOT NULL, observed_ts REAL NOT NULL,
 published_ts REAL,
 UNIQUE(source,source_key,term_key));
CREATE INDEX IF NOT EXISTS osece_event_term ON osece_events(term_key,observed_ts);
CREATE INDEX IF NOT EXISTS osece_event_time ON osece_events(observed_ts);
CREATE TABLE IF NOT EXISTS osece_launch_census (
 hour_epoch INTEGER NOT NULL, source TEXT NOT NULL,
 events INTEGER NOT NULL DEFAULT 0,
 matched INTEGER NOT NULL DEFAULT 0,
 PRIMARY KEY(hour_epoch,source));
CREATE TABLE IF NOT EXISTS osece_launches (
 mint TEXT PRIMARY KEY, launch_source TEXT NOT NULL, name TEXT NOT NULL,
 symbol TEXT NOT NULL, created_ts REAL NOT NULL,
 observed_ts REAL NOT NULL, term_key TEXT NOT NULL,
 sources_json TEXT NOT NULL, source_count INTEGER NOT NULL,
 evidence_count INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS osece_signals (
 id INTEGER PRIMARY KEY AUTOINCREMENT,
 variant TEXT NOT NULL, mint TEXT NOT NULL,
 launch_ts REAL NOT NULL, term_key TEXT NOT NULL,
 source_count INTEGER NOT NULL, state TEXT NOT NULL,
 reason TEXT, watch_ts REAL, watch_price REAL,
 confirm_ts REAL, confirm_price REAL,
 UNIQUE(variant,mint));
CREATE INDEX IF NOT EXISTS osece_signal_state ON osece_signals(state,launch_ts);
CREATE TABLE IF NOT EXISTS osece_quotes (
 mint TEXT NOT NULL, observed_ts REAL NOT NULL,
 price REAL NOT NULL, liquidity REAL NOT NULL,
 buy_usd_m5 REAL, sell_usd_m5 REAL,
 organic_score REAL, num_traders INTEGER,
 audit_ok INTEGER, PRIMARY KEY(mint,observed_ts));
CREATE INDEX IF NOT EXISTS osece_quote_time ON osece_quotes(observed_ts);
CREATE TABLE IF NOT EXISTS osece_trades (
 id INTEGER PRIMARY KEY AUTOINCREMENT, variant TEXT NOT NULL,
 signal_id INTEGER NOT NULL UNIQUE,
 mint TEXT NOT NULL, entry_ts REAL NOT NULL,
 entry_price REAL NOT NULL, entry_liquidity REAL NOT NULL,
 usd_in REAL NOT NULL, exit_ts REAL, exit_price REAL,
 usd_out REAL, exit_reason TEXT);
CREATE INDEX IF NOT EXISTS osece_open ON osece_trades(variant,exit_ts,mint);
CREATE TABLE IF NOT EXISTS osece_notifications (
 trade_id INTEGER NOT NULL, action TEXT NOT NULL,
 sent_ts REAL NOT NULL, PRIMARY KEY(trade_id,action));
"""


def ensure(db):
    db.executescript(SCHEMA)


def enabled():
    return os.environ.get("OSECE_PAPER_ENABLED") == "1"


def _hash(value):
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def normalize(text):
    text = unicodedata.normalize("NFKD", str(text or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return "".join(WORD.findall(text.lower()))


def valid_term(term):
    key = normalize(term)
    return bool(5 <= len(key) <= 35 and not key.isdigit()
                and key not in BLOCK and not any(x in key for x in ("http","www"))
                and len(set(key)) >= 3)


def extract_terms(text, *, curated=False):
    """Small, deterministic event vocabulary, not a full web post archive."""
    raw = html.unescape(str(text or ""))[:1200]
    candidates = set(HASHTAG.findall(raw))
    candidates.update(TITLE.findall(raw))
    if curated:
        # Search terms and news titles often are lowercase. Restrict to first
        # four tokens and adjacent 2-3 grams, never anonymous generic words.
        cleaned = re.sub(r"\s[-|:].*$", "", raw)
        words = [w for w in WORD.findall(cleaned) if w.lower() not in STOP][:6]
        if 1 <= len(words) <= 4:
            candidates.add(" ".join(words))
        # At most two adjacent phrase fragments, rather than every bigram
        # from every news title; syndicated headlines otherwise dominate.
        if len(words)>=2:
            candidates.add(" ".join(words[:2]))
        if len(words)>=3:
            candidates.add(" ".join(words[:3]))
    # No generic single TitleCase words from social posts; exact names,
    # multiword entities and hashtags only. Bound stored terms per item.
    return sorted({normalize(x) for x in candidates if valid_term(x)})[:4]


def started_at(db, now):
    row = db.execute("SELECT value FROM osece_meta WHERE key='started_at'").fetchone()
    if row:
        return float(row[0])
    db.execute("INSERT INTO osece_meta VALUES ('started_at',?)",(str(now),))
    db.commit()
    return now


def source_state(db, source, state, detail="", now=None):
    now = time.time() if now is None else now
    db.execute("INSERT OR REPLACE INTO osece_sources VALUES (?,?,?,?)",
               (source,now,state,str(detail)[:220]))
    db.commit()


def record_event(db, *, source, external_id, text, actor=None,
                 observed_ts=None, published_ts=None, curated=False):
    if source not in SOURCES or not external_id:
        return 0
    now = time.time() if observed_ts is None else observed_ts
    if published_ts is not None and (published_ts > now+120 or
                                     now-published_ts > (3600 if curated else 900)):
        return 0
    terms = extract_terms(text,curated=curated)
    if not terms:
        return 0
    inserted = 0
    for term in terms:
        cursor = db.execute(
            "INSERT OR IGNORE INTO osece_events "
            "(source,source_key,actor_key,term,term_key,observed_ts,published_ts)"
            " VALUES (?,?,?,?,?,?,?)",
            (source,_hash(external_id),_hash(actor or external_id),term,term,
             now,published_ts))
        inserted += cursor.rowcount
    if inserted:
        db.commit()
    return inserted


def _sources_for(db, key, launch_ts):
    events = db.execute(
        "SELECT source,actor_key,observed_ts FROM osece_events "
        "WHERE term_key=? AND observed_ts<? AND observed_ts>=?",
        (key,launch_ts-1,launch_ts-MAX_CULTURE_LEAD_SEC)).fetchall()
    by_source=defaultdict(set)
    for r in events:
        by_source[r["source"]].add(r["actor_key"])
    qualified={}
    for source, authors in by_source.items():
        # Social streams need independent people, not a single promoter.
        if source in ("bluesky","nostr") and len(authors)<3:
            continue
        qualified[source]=len(authors)
    return qualified, sum(len(v) for v in by_source.values())


def record_launch(db, *, mint, name, symbol, created_ts=None,
                  observed_ts=None, source="pumpportal"):
    """Match source observations that genuinely preceded creation.
    Fail closed on unknown creation time for later-discovered pools.
    """
    now=time.time() if observed_ts is None else observed_ts
    if not valid_mint(mint) or not name or created_ts is None:
        return {"status":"ignored","reason":"invalid or missing mint/name/creation time"}
    if created_ts>now+15 or now-created_ts>900:
        return {"status":"ignored","reason":"stale or future launch timestamp"}
    hour=int(now//3600)*3600
    db.execute("INSERT INTO osece_launch_census(hour_epoch,source,events,matched) "
               "VALUES (?,?,1,0) ON CONFLICT(hour_epoch,source) "
               "DO UPDATE SET events=events+1",(hour,source))
    # Match an entire normalized token NAME, never 2-letter symbol fragments.
    keys=[normalize(name)]
    skey=normalize(symbol)
    if len(skey)>=6 and skey not in keys:
        keys.append(skey)
    matches=[]
    for key in keys:
        if not valid_term(key):
            continue
        sources,count=_sources_for(db,key,created_ts)
        if count:
            matches.append((len(sources),count,key,sources))
    if not matches:
        db.commit()
        return {"status":"unmatched"}
    n,count,key,sources=max(matches,key=lambda x:(x[0],x[1]))
    cur=db.execute(
        "INSERT OR IGNORE INTO osece_launches "
        "(mint,launch_source,name,symbol,created_ts,observed_ts,"
        "term_key,sources_json,source_count,evidence_count) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (mint,source,str(name)[:100],str(symbol)[:30],created_ts,now,
         key,json.dumps(sources,sort_keys=True),n,count))
    if cur.rowcount:
        db.execute("UPDATE osece_launch_census SET matched=matched+1 "
                   "WHERE hour_epoch=? AND source=?",(hour,source))
        # Preserve frozen source diversity AS OF creation, never upgrade a
        # launch using social messages published or observed after it.
        if n>=1:
            db.execute("INSERT OR IGNORE INTO osece_signals "
                       "(variant,mint,launch_ts,term_key,source_count,state)"
                       " VALUES (?,?,?,?,?,'watch')",
                       (SINGLE,mint,now,key,n))
        if n>=2:
            db.execute("INSERT OR IGNORE INTO osece_signals "
                       "(variant,mint,launch_ts,term_key,source_count,state)"
                       " VALUES (?,?,?,?,?,'watch')",
                       (COLLISION,mint,now,key,n))
    db.commit()
    if cur.rowcount:
        log.info("OSECE MATCH mint=%s name=%s term=%s sources=%s variants=%s",
                 mint,name,key,sorted(sources),1+(n>=2))
    return {"status":"matched","sources":n,"signals":1+(n>=2) if cur.rowcount else 0}


def _num(v):
    try:
        x=float(v)
        return x if math.isfinite(x) and x>=0 else None
    except (ValueError,TypeError,OverflowError):
        return None


def record_jupiter_quote(db, token, now=None):
    """Store only directly reported buy/sell USD; never count-derived estimates."""
    now=time.time() if now is None else now
    if not isinstance(token,dict):
        return False
    mint=token.get("id")
    if not valid_mint(mint):
        return False
    price=_num(token.get("usdPrice"))
    liquidity=_num(token.get("liquidity"))
    if not price or liquidity is None:
        return False
    stats=token.get("stats5m") or {}
    if not isinstance(stats,dict):
        stats={}
    audit=token.get("audit") or {}
    if not isinstance(audit,dict):
        audit={}
    # Unknown authority flags stay unknown, not quietly classified safe.
    authority=(audit.get("mintAuthorityDisabled"),audit.get("freezeAuthorityDisabled"))
    audit_ok=(int(all(x is True for x in authority) and not audit.get("isSus"))
              if all(x is not None for x in authority) else None)
    vals=(mint,now,price,liquidity,_num(stats.get("buyVolume")),
          _num(stats.get("sellVolume")),_num(token.get("organicScore")),
          int(stats["numTraders"]) if isinstance(stats.get("numTraders"),int) and stats["numTraders"]>=0 else None,
          audit_ok)
    db.execute("INSERT OR IGNORE INTO osece_quotes "
               "(mint,observed_ts,price,liquidity,buy_usd_m5,sell_usd_m5,"
               "organic_score,num_traders,audit_ok) VALUES (?,?,?,?,?,?,?,?,?)",vals)
    db.commit()
    return True


def tracked_mints(db, now=None, limit=80):
    now=time.time() if now is None else now
    return [r[0] for r in db.execute(
        "SELECT mint FROM ("
        "SELECT mint,MAX(launch_ts) priority FROM osece_signals "
        "WHERE state IN ('watch','confirm','queued') AND launch_ts>=? GROUP BY mint "
        "UNION ALL "
        "SELECT mint,MAX(entry_ts) priority FROM osece_trades "
        "WHERE exit_ts IS NULL GROUP BY mint "
        ") GROUP BY mint ORDER BY MAX(priority) DESC LIMIT ?",
        (now-SIGNAL_LIFETIME_SEC,limit))]


def _last_quote(db,mint,now):
    return db.execute(
        "SELECT * FROM osece_quotes WHERE mint=? AND observed_ts>? "
        "AND observed_ts<=? ORDER BY observed_ts DESC LIMIT 1",
        (mint,now-QUOTE_MAX_AGE_SEC,now)).fetchone()


def _cash(db,variant):
    r=db.execute(
        "SELECT COALESCE(SUM(usd_in),0),COALESCE(SUM(usd_out),0)"
        " FROM osece_trades WHERE variant=?",(variant,)).fetchone()
    return INITIAL_CASH-r[0]+r[1]


def _open(db,variant):
    return db.execute("SELECT COUNT(*) FROM osece_trades "
                      "WHERE variant=? AND exit_ts IS NULL",(variant,)).fetchone()[0]


def _market_entry_ok(q):
    return bool(q and q["liquidity"]>=20000 and q["price"]>0 and
                q["buy_usd_m5"] is not None and q["sell_usd_m5"] is not None and
                q["buy_usd_m5"]>=1200 and
                q["buy_usd_m5"]>=1.2*max(100,q["sell_usd_m5"]) and
                q["organic_score"] is not None and q["organic_score"]>=25 and
                q["audit_ok"]==1)


def tick(db,now=None):
    if not enabled():
        return {"status":"disabled"}
    ensure(db)
    now=time.time() if now is None else now
    started=started_at(db,now)
    result={"status":"unvalidated_forward_paper","watching":0,"queued":0,"bought":0,"sold":0,"rejected":0}
    for pos in db.execute(
        "SELECT * FROM osece_trades WHERE exit_ts IS NULL ORDER BY entry_ts").fetchall():
        q=_last_quote(db,pos["mint"],now)
        if not q or q["observed_ts"]<=pos["entry_ts"]:
            continue
        pct=percent_return(pos["entry_price"],q["price"])
        why=("TAKE_NET_20" if pct>=TAKE_NET else
             "STOP_NET_12" if pct<=STOP_NET else
             "LIQUIDITY_DRAIN" if q["liquidity"]<pos["entry_liquidity"]*.75 else
             "TIME_20M" if now-pos["entry_ts"]>=HOLD_SEC else None)
        if not why:
            continue
        out=realized_usd(pos["entry_price"],q["price"],pos["usd_in"])
        db.execute("UPDATE osece_trades "
                   "SET exit_ts=?,exit_price=?,usd_out=?,exit_reason=? WHERE id=?",
                   (q["observed_ts"],q["price"],out,why,pos["id"]))
        log.info("OSECE PAPER SELL %s mint=%s net=$%+.2f reason=%s",
                 pos["variant"],pos["mint"],out-pos["usd_in"],why)
        result["sold"]+=1
    for sig in db.execute(
        "SELECT * FROM osece_signals WHERE state IN ('watch','confirm','queued') "
        "ORDER BY launch_ts,id").fetchall():
        state=sig["state"]
        if sig["launch_ts"]<started or now-sig["launch_ts"]>SIGNAL_LIFETIME_SEC:
            db.execute("UPDATE osece_signals SET state='expired',reason='launch too old' WHERE id=?",
                       (sig["id"],))
            result["rejected"]+=1
            continue
        q=_last_quote(db,sig["mint"],now)
        if state=="watch":
            if not q or q["observed_ts"]<=sig["launch_ts"]+30:
                continue
            if not _market_entry_ok(q):
                continue
            db.execute("UPDATE osece_signals "
                       "SET state='confirm',watch_ts=?,watch_price=? WHERE id=?",
                       (q["observed_ts"],q["price"],sig["id"]))
            result["watching"]+=1
        elif state=="confirm":
            if not q or q["observed_ts"]<sig["watch_ts"]+30:
                continue
            if (not _market_entry_ok(q) or
                not sig["watch_price"]*.94<=q["price"]<=sig["watch_price"]*1.12):
                db.execute("UPDATE osece_signals SET state='rejected',reason='confirmation failed' "
                           "WHERE id=?",(sig["id"],))
                result["rejected"]+=1
                continue
            db.execute("UPDATE osece_signals SET state='queued',"
                       "confirm_ts=?,confirm_price=? WHERE id=?",
                       (q["observed_ts"],q["price"],sig["id"]))
            result["queued"]+=1
        else:
            if not q or q["observed_ts"]<sig["confirm_ts"]+ENTRY_DELAY_SEC:
                continue
            if q["observed_ts"]>sig["confirm_ts"]+180:
                db.execute("UPDATE osece_signals SET state='expired',reason='no timely fill' "
                           "WHERE id=?",(sig["id"],))
                result["rejected"]+=1
                continue
            if not _market_entry_ok(q) or q["price"]>sig["confirm_price"]*1.10:
                status="rejected"; reason="late runup or missing buyer evidence"
            elif not eligible(db,sig["mint"],q["observed_ts"],q["liquidity"]):
                # Preserve the queue for a timely RugCheck refresh.
                continue
            elif _open(db,sig["variant"])>=MAX_OPEN or _cash(db,sig["variant"])<TRADE_USD:
                status="rejected";reason="capacity or cash"
            elif db.execute(
                "SELECT 1 FROM osece_trades WHERE mint=? AND variant=? AND entry_ts>? ",
                (sig["mint"],sig["variant"],now-COOLDOWN_SEC)).fetchone():
                status="rejected";reason="duplicate in cooldown"
            else:
                db.execute("INSERT OR IGNORE INTO osece_trades "
                           "(variant,signal_id,mint,entry_ts,entry_price,entry_liquidity,usd_in)"
                           " VALUES (?,?,?,?,?,?,?)",
                           (sig["variant"],sig["id"],sig["mint"],q["observed_ts"],
                            q["price"],q["liquidity"],TRADE_USD))
                status="paper_bought";reason=None;result["bought"]+=1
                log.info("OSECE PAPER BUY %s mint=%s USD 10 price=%.10g source_count=%s "
                         "buy5=%.0f sell5=%.0f",
                         sig["variant"],sig["mint"],q["price"],sig["source_count"],
                         q["buy_usd_m5"],q["sell_usd_m5"])
            db.execute("UPDATE osece_signals SET state=?,reason=? WHERE id=?",
                       (status,reason,sig["id"]))
    db.commit()
    return result


def report(db,limit=75):
    ensure(db)
    sources=[dict(r) for r in db.execute("SELECT * FROM osece_sources ORDER BY source")]
    states=[dict(r) for r in db.execute(
        "SELECT variant,state,COUNT(*) n FROM osece_signals "
        "GROUP BY variant,state ORDER BY variant,state")]
    books=[]
    for variant in BOOKS:
        all_rows=db.execute(
            "SELECT COUNT(*) n,"
            "SUM(CASE WHEN exit_ts IS NULL THEN 1 ELSE 0 END) open,"
            "SUM(CASE WHEN exit_ts IS NOT NULL AND usd_out>usd_in THEN 1 ELSE 0 END) wins,"
            "SUM(CASE WHEN exit_ts IS NOT NULL AND usd_out<=usd_in THEN 1 ELSE 0 END) losses,"
            "COALESCE(SUM(CASE WHEN exit_ts IS NOT NULL THEN usd_out-usd_in END),0) pnl"
            " FROM osece_trades WHERE variant=?",(variant,)).fetchone()
        trades=[]
        for r in db.execute(
            "SELECT t.*,l.name,l.symbol,l.term_key,l.sources_json "
            "FROM osece_trades t JOIN osece_launches l ON l.mint=t.mint "
            "WHERE t.variant=? ORDER BY t.entry_ts DESC LIMIT ?",(variant,limit)):
            trades.append({
                "token":r["name"],"symbol":r["symbol"],"mint":r["mint"],
                "term":r["term_key"],"sources":json.loads(r["sources_json"]),
                "entry_utc":datetime.fromtimestamp(r["entry_ts"],timezone.utc).isoformat(),
                "exit_utc":(datetime.fromtimestamp(r["exit_ts"],timezone.utc).isoformat()
                             if r["exit_ts"] else None),
                "entry_price":r["entry_price"],"exit_price":r["exit_price"],
                "net_pnl":round(r["usd_out"]-r["usd_in"],2) if r["usd_out"] is not None else None,
                "reason":r["exit_reason"] or "OPEN"})
        books.append({"variant":variant,"paper_only":True,
                      "closed":all_rows["n"]-(all_rows["open"] or 0),
                      "open":all_rows["open"] or 0,"wins":all_rows["wins"] or 0,
                      "losses":all_rows["losses"] or 0,
                      "net_realized_pnl":round(all_rows["pnl"],2),
                      "cash":round(_cash(db,variant),2),"trades":trades})
    totals={table:db.execute("SELECT COUNT(*) FROM "+table).fetchone()[0]
            for table in ("osece_events","osece_launches","osece_signals",
                          "osece_quotes","osece_trades")}
    census=[dict(r) for r in db.execute(
        "SELECT source,SUM(events) events,SUM(matched) matched "
        "FROM osece_launch_census GROUP BY source")]
    return {"status":"unvalidated_forward_paper","version":VERSION,"counts":totals,
            "source_status":sources,"launch_census":census,
            "signal_states":states,"books":books,
            "warnings":["No real trades or executable swap quotes",
                        "Costs assume 1.25pct fee + 1pct slippage each side",
                        "Jupiter statistics must be present; missing never means zero",
                        "Cultural phrase matching is exploratory; no calibrated hit probabilities"]}


def prune(db,now=None):
    ensure(db)
    now=time.time() if now is None else now
    # Preserve all launch matches, frozen signals and trades.
    db.execute("DELETE FROM osece_events WHERE observed_ts<?",(now-14*86400,))
    db.execute("DELETE FROM osece_quotes WHERE observed_ts<?",(now-14*86400,))
    db.commit()
