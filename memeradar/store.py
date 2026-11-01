"""SQLite storage (free replacement for Postgres/ClickHouse) – snapshots feed the backtester."""
from __future__ import annotations

import json
import sqlite3
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS tokens (
    chain TEXT, address TEXT, symbol TEXT, name TEXT, source TEXT, first_seen REAL,
    safety_passed INTEGER, safety_reasons TEXT,
    PRIMARY KEY (chain, address));
CREATE TABLE IF NOT EXISTS snapshots (
    ts REAL, chain TEXT, address TEXT, score REAL, features TEXT,
    price_usd REAL, liquidity_usd REAL, vol_m5 REAL, vol_h1 REAL, market_cap REAL);
CREATE INDEX IF NOT EXISTS snap_token ON snapshots (chain, address, ts);
CREATE TABLE IF NOT EXISTS alerts (ts REAL, chain TEXT, address TEXT, symbol TEXT, score REAL, price_usd REAL);
"""


class Store:
    def __init__(self, path: str) -> None:
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def upsert_token(self, t) -> None:
        s = t.safety
        self.db.execute(
            """INSERT INTO tokens VALUES (?,?,?,?,?,?,?,?)
               ON CONFLICT(chain, address) DO UPDATE SET symbol=excluded.symbol, name=excluded.name,
               safety_passed=excluded.safety_passed, safety_reasons=excluded.safety_reasons""",
            (t.chain, t.address, t.symbol, t.name, t.source, t.first_seen,
             None if s is None else int(s.passed), None if s is None else json.dumps(s.reasons)))

    def snapshot(self, t) -> None:
        self.db.execute("INSERT INTO snapshots VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (time.time(), t.chain, t.address, t.score, json.dumps(t.features),
                         t.price_usd, t.liquidity_usd, t.vol_m5, t.vol_h1, t.market_cap))

    def alert(self, t) -> None:
        self.db.execute("INSERT INTO alerts VALUES (?,?,?,?,?,?)",
                        (time.time(), t.chain, t.address, t.symbol, t.score, t.price_usd))

    def last_alert_ts(self, chain: str, address: str) -> float:
        row = self.db.execute("SELECT MAX(ts) FROM alerts WHERE chain=? AND address=?",
                              (chain, address)).fetchone()
        return row[0] or 0.0

    def commit(self) -> None:
        self.db.commit()

    def leaderboard(self, limit: int = 50, since_minutes: int = 120) -> list[dict]:
        rows = self.db.execute(
            """SELECT s.*, t.symbol, t.name, t.source, t.safety_reasons
               FROM snapshots s JOIN tokens t USING (chain, address)
               WHERE s.ts = (SELECT MAX(ts) FROM snapshots x WHERE x.chain=s.chain AND x.address=s.address)
                 AND s.ts > ? AND t.safety_passed = 1
               ORDER BY s.score DESC LIMIT ?""",
            (time.time() - since_minutes * 60, limit)).fetchall()
        return [dict(r) for r in rows]

    def recent_alerts(self, limit: int = 20) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM alerts ORDER BY ts DESC LIMIT ?", (limit,)).fetchall()]

    def all_snapshots(self) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM snapshots WHERE price_usd IS NOT NULL ORDER BY chain, address, ts")]
