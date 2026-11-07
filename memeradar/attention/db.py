"""Shared, append-only evidence store for attention-lead research.

Stores no post text, usernames or Telegram session strings. Source message IDs
are hashed, retained only for deduplication / deletion. Event receipt time is
never rewritten with the earlier published time (preventing look-ahead).
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS attention_hits (
 source TEXT NOT NULL, post_key TEXT NOT NULL, mint TEXT NOT NULL,
 posted_ts REAL, observed_ts REAL NOT NULL, engagement REAL, audience REAL,
 PRIMARY KEY(source, post_key, mint));
CREATE INDEX IF NOT EXISTS attention_mint_ts ON attention_hits(mint, observed_ts);
CREATE TABLE IF NOT EXISTS attention_chain (
 mint TEXT NOT NULL, observed_ts REAL NOT NULL,
 price_usd REAL NOT NULL, liquidity_usd REAL NOT NULL, market_cap REAL,
 vol_m5 REAL NOT NULL, buys_m5 INTEGER NOT NULL, sells_m5 INTEGER NOT NULL,
 buy_volume_proxy_m5 REAL NOT NULL, pair TEXT NOT NULL,
 PRIMARY KEY (mint, observed_ts));
CREATE INDEX IF NOT EXISTS attention_chain_time ON attention_chain(observed_ts);
CREATE TABLE IF NOT EXISTS attention_safety (
 mint TEXT PRIMARY KEY, checked_ts REAL NOT NULL, state TEXT NOT NULL, reasons TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS attention_source_state (
 source TEXT PRIMARY KEY, cursor TEXT, status TEXT NOT NULL,
 last_poll_ts REAL NOT NULL, last_success_ts REAL, detail TEXT);
CREATE TABLE IF NOT EXISTS attention_models (
 version TEXT PRIMARY KEY, created_ts REAL NOT NULL, trained_before REAL NOT NULL,
 horizon_seconds INTEGER NOT NULL, feature_names TEXT NOT NULL, model_json TEXT NOT NULL,
 validation_json TEXT NOT NULL, status TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS attention_predictions (
 mint TEXT NOT NULL, observed_ts REAL NOT NULL, model_version TEXT NOT NULL,
 predicted_future_buy_proxy REAL NOT NULL, already_seen_buy_proxy REAL NOT NULL,
 waiting_buy_proxy REAL NOT NULL, liquidity_usd REAL NOT NULL, gate_reason TEXT NOT NULL,
 PRIMARY KEY (mint, observed_ts, model_version));
CREATE TABLE IF NOT EXISTS attention_paper_trades (
 id INTEGER PRIMARY KEY AUTOINCREMENT, mint TEXT NOT NULL, model_version TEXT NOT NULL,
 entry_ts REAL NOT NULL, entry_price REAL NOT NULL, usd_in REAL NOT NULL,
 predicted_buy_proxy REAL NOT NULL, entry_buy_proxy REAL NOT NULL,
 exit_ts REAL, exit_price REAL, usd_out REAL, reason TEXT);
CREATE INDEX IF NOT EXISTS attention_paper_open
 ON attention_paper_trades(mint, exit_ts);
"""


def connect(path: str = "memeradar.db") -> sqlite3.Connection:
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    return db


def ensure(db):
    db.executescript(SCHEMA)


def message_key(post_id: str) -> str:
    return hashlib.sha256(str(post_id).encode("utf-8")).hexdigest()


def record_hit(db, *, source: str, post_id: str, mint: str, observed_ts=None,
               posted_ts=None, engagement=None, audience=None) -> bool:
    observed_ts = time.time() if observed_ts is None else observed_ts
    if posted_ts is not None and posted_ts > observed_ts + 300:
        return False
    if engagement is not None:
        engagement = max(0, float(engagement))
    if audience is not None:
        audience = max(0, float(audience))
    cur = db.execute(
        "INSERT OR IGNORE INTO attention_hits "
        "(source,post_key,mint,posted_ts,observed_ts,engagement,audience) "
        "VALUES (?,?,?,?,?,?,?)",
        (source, message_key(post_id), mint, posted_ts, observed_ts, engagement, audience))
    db.commit()
    return cur.rowcount == 1


def remove_post(db, source: str, post_id: str):
    """Deletion/withdrawal hook. Derived model artifacts must be retrained if affected."""
    cur = db.execute("DELETE FROM attention_hits WHERE source=? AND post_key=?",
                     (source, message_key(post_id)))
    db.commit()
    return cur.rowcount


def source_status(db, source, status, *, cursor=None, detail=None, ok=False, now=None):
    now = time.time() if now is None else now
    prev = db.execute("SELECT cursor,last_success_ts FROM attention_source_state WHERE source=?",
                      (source,)).fetchone()
    db.execute("INSERT OR REPLACE INTO attention_source_state VALUES (?,?,?,?,?,?)",
               (source, cursor if cursor is not None else (prev["cursor"] if prev else None),
                status, now, now if ok else (prev["last_success_ts"] if prev else None), detail))
    db.commit()


def source_cursor(db, source):
    row = db.execute("SELECT cursor FROM attention_source_state WHERE source=?", (source,)).fetchone()
    return row["cursor"] if row else None


def active_mints(db, *, now=None, limit=150, age_seconds=6*3600):
    now = time.time() if now is None else now
    return [r[0] for r in db.execute(
        "SELECT mint FROM attention_hits WHERE observed_ts>=? AND observed_ts<=? "
        "GROUP BY mint ORDER BY MAX(observed_ts) DESC LIMIT ?",
        (now-age_seconds, now, limit))]


def recent_attention(db, mint, now, window=300):
    return [dict(r) for r in db.execute(
        "SELECT source, observed_ts, engagement, audience FROM attention_hits "
        "WHERE mint=? AND observed_ts>? AND observed_ts<=?",
        (mint, now-window, now))]


def brief(db):
    result = {}
    for table in ("attention_hits","attention_chain","attention_safety",
                  "attention_models","attention_predictions","attention_paper_trades"):
        result[table] = db.execute("SELECT COUNT(*) FROM "+table).fetchone()[0]
    result["sources"] = [dict(r) for r in db.execute(
        "SELECT source,status,last_poll_ts,last_success_ts,detail FROM attention_source_state "
        "ORDER BY source")]
    result["paper"] = [dict(r) for r in db.execute(
        "SELECT model_version, COUNT(*) n, "
        "SUM(CASE WHEN exit_ts IS NULL THEN 1 ELSE 0 END) open, "
        "SUM(CASE WHEN exit_ts IS NOT NULL AND usd_out>usd_in THEN 1 ELSE 0 END) wins, "
        "SUM(CASE WHEN exit_ts IS NOT NULL AND usd_out<=usd_in THEN 1 ELSE 0 END) losses, "
        "ROUND(COALESCE(SUM(CASE WHEN exit_ts IS NOT NULL THEN usd_out-usd_in END),0),2) pnl "
        "FROM attention_paper_trades GROUP BY model_version")]
    return result
