"""Reconcile orphaned paper receipts from immutable prior GitHub run artifacts.

Trading/book tables use autoincrement IDs. Restoring two forked SQLite backups
with raw INSERT OR IGNORE on IDs would silently drop REAL historical trades,
so remap signal IDs and deduplicate by variant, mint and entry timestamp.

This code never invents a trade or fills at a historical price. It copies
only previously OBSERVED historical receipts, preserving their original times
and marks recovered notifications delivered to avoid duplicate old phone alerts.
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time

from . import db as evidence
from . import explore


def _exists(db,table):
    return bool(db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,)).fetchone())


def ensure_recovery(db):
    db.execute("""
    CREATE TABLE IF NOT EXISTS paper_archive_recovery (
      run_id TEXT NOT NULL PRIMARY KEY, recovered_at REAL NOT NULL,
      social_trades_added INTEGER NOT NULL,
      osece_trades_added INTEGER NOT NULL,
      social_trades_closed INTEGER NOT NULL,
      osece_trades_closed INTEGER NOT NULL
    )""")
    db.commit()


def _signal_for(db,row):
    dest=db.execute(
        "SELECT id FROM attention_explore_signals WHERE "
        "variant=? AND mint=? AND source_ts=?",
        (row["variant"],row["mint"],row["source_ts"])).fetchone()
    if dest:
        return dest[0]
    cols=("variant","mint","source","source_ts","signal_ts","signal_price",
          "signal_liquidity","buy_proxy_m5","buyers_m5","sellers_m5",
          "source_rank","status","details")
    db.execute(
        "INSERT OR IGNORE INTO attention_explore_signals "
        "("+",".join(cols)+") VALUES ("+",".join("?" for _ in cols)+")",
        tuple(row[c] for c in cols))
    dest=db.execute(
        "SELECT id FROM attention_explore_signals WHERE "
        "variant=? AND mint=? AND source_ts=?",
        (row["variant"],row["mint"],row["source_ts"])).fetchone()
    if dest is None:
        raise RuntimeError("Cannot preserve archived social trade's source signal")
    return dest[0]


def _osece_signal_for(db,row):
    dest=db.execute(
        "SELECT id FROM osece_signals WHERE variant=? AND mint=?",
        (row["variant"],row["mint"])).fetchone()
    if dest:
        return dest[0]
    cols=("variant","mint","launch_ts","term_key","source_count","state",
          "reason","watch_ts","watch_price","confirm_ts","confirm_price")
    db.execute(
        "INSERT OR IGNORE INTO osece_signals ("+",".join(cols)+") VALUES ("+
        ",".join("?" for _ in cols)+")",tuple(row[c] for c in cols))
    dest=db.execute(
        "SELECT id FROM osece_signals WHERE variant=? AND mint=?",
        (row["variant"],row["mint"])).fetchone()
    if dest is None:
        raise RuntimeError("Cannot preserve archived OSECE signal")
    return dest[0]


def _copy_osece_launch(db,src,mint):
    row=src.execute("SELECT * FROM osece_launches WHERE mint=?",(mint,)).fetchone()
    if not row:
        raise RuntimeError("Cannot recover OSECE trade without its genuine launch")
    cols=("mint","launch_source","name","symbol","created_ts","observed_ts",
          "term_key","sources_json","source_count","evidence_count")
    db.execute("INSERT OR IGNORE INTO osece_launches ("+",".join(cols)+
               ") VALUES ("+",".join("?" for _ in cols)+")",
               tuple(row[c] for c in cols))


def reconcile(db,src,source_run):
    """Idempotently import only preexisting receipts, never infer outcomes."""
    from ..osece import core as osece
    evidence.ensure(db)
    explore.ensure(db)
    osece.ensure(db)
    ensure_recovery(db)
    prior=db.execute("SELECT run_id FROM paper_archive_recovery WHERE run_id=?",
                     (str(source_run),)).fetchone()
    if prior:
        return {"run_id":str(source_run),"skipped":"already imported"}
    result={"run_id":str(source_run),"social_added":0,"osece_added":0,
            "social_completed":0,"osece_completed":0}
    if _exists(src,"attention_explore_trades") and _exists(src,"attention_explore_signals"):
        # Only active Social Attention. Gecko trades remain available in their
        # original archived runs, but were intentionally retired.
        rows=src.execute(
            "SELECT * FROM attention_explore_trades "
            "WHERE variant=? ORDER BY entry_ts",(explore.VERSIONS[0],)).fetchall()
        for r in rows:
            prior=db.execute(
                "SELECT id,exit_ts FROM attention_explore_trades "
                "WHERE variant=? AND mint=? AND ABS(entry_ts-?)<0.001 "
                "ORDER BY id LIMIT 1",
                (r["variant"],r["mint"],r["entry_ts"])).fetchone()
            if prior:
                tid=prior["id"]
                if prior["exit_ts"] is None and r["exit_ts"] is not None:
                    db.execute(
                        "UPDATE attention_explore_trades SET exit_ts=?,exit_price=?,"
                        "usd_out=?,reason=? WHERE id=?",
                        (r["exit_ts"],r["exit_price"],r["usd_out"],r["reason"],tid))
                    result["social_completed"]+=1
                continue
            sig=src.execute(
                "SELECT * FROM attention_explore_signals WHERE id=?",
                (r["signal_id"],)).fetchone()
            if sig is None:
                raise RuntimeError("Archived social trade missing its recorded signal")
            sid=_signal_for(db,sig)
            cols=("variant","mint","source","entry_ts","entry_price",
                  "usd_in","exit_ts","exit_price","usd_out","reason")
            cur=db.execute(
                "INSERT INTO attention_explore_trades (signal_id,"+
                ",".join(cols)+") VALUES (?,"+",".join("?" for _ in cols)+")",
                (sid,)+tuple(r[c] for c in cols))
            tid=cur.lastrowid
            db.execute("UPDATE attention_explore_signals SET status='paper_bought' "
                       "WHERE id=?",(sid,))
            # These alerts were already part of the original archived run.
            for action in ("BUY","SELL") if r["exit_ts"] is not None else ("BUY",):
                db.execute(
                    "INSERT OR IGNORE INTO attention_explore_notifications "
                    "(trade_id,action,notified_ts) VALUES (?,?,?)",
                    (tid,action,time.time()))
            result["social_added"]+=1
    if all(_exists(src,t) for t in ("osece_trades","osece_signals","osece_launches")):
        for r in src.execute("SELECT * FROM osece_trades ORDER BY entry_ts"):
            prior=db.execute(
                "SELECT id,exit_ts FROM osece_trades "
                "WHERE variant=? AND mint=? AND ABS(entry_ts-?)<0.001 "
                "ORDER BY id LIMIT 1",(r["variant"],r["mint"],r["entry_ts"])).fetchone()
            if prior:
                if prior["exit_ts"] is None and r["exit_ts"] is not None:
                    db.execute("UPDATE osece_trades SET exit_ts=?,exit_price=?,"
                               "usd_out=?,exit_reason=? WHERE id=?",
                               (r["exit_ts"],r["exit_price"],r["usd_out"],
                                r["exit_reason"],prior["id"]))
                    result["osece_completed"]+=1
                continue
            sig=src.execute("SELECT * FROM osece_signals WHERE id=?",
                            (r["signal_id"],)).fetchone()
            if sig is None:
                raise RuntimeError("Archived OSECE trade missing source signal")
            _copy_osece_launch(db,src,r["mint"])
            sid=_osece_signal_for(db,sig)
            cols=("variant","mint","entry_ts","entry_price","entry_liquidity",
                  "usd_in","exit_ts","exit_price","usd_out","exit_reason")
            cur=db.execute(
                "INSERT INTO osece_trades (signal_id,"+",".join(cols)+
                ") VALUES (?,"+",".join("?" for _ in cols)+")",
                (sid,)+tuple(r[c] for c in cols))
            for action in ("BUY","SELL") if r["exit_ts"] is not None else ("BUY",):
                db.execute(
                    "INSERT OR IGNORE INTO osece_notifications "
                    "(trade_id,action,sent_ts) VALUES (?,?,?)",
                    (cur.lastrowid,action,time.time()))
            result["osece_added"]+=1
    db.execute("INSERT INTO paper_archive_recovery "
               "(run_id,recovered_at,social_trades_added,osece_trades_added,"
               "social_trades_closed,osece_trades_closed) VALUES (?,?,?,?,?,?)",
               (str(source_run),time.time(),result["social_added"],
                result["osece_added"],result["social_completed"],
                result["osece_completed"]))
    db.commit()
    return result
