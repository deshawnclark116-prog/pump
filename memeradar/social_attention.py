"""Social attention lead-lag research. Collection only: no orders or paper buys.

Keep raw timestamps and addresses. Never turn a ticker-only match into a trade.
The observation tables are independent of the existing radar strategy books.
"""
import re
import sqlite3
import time

# Base58 alphabet excludes 0 O I l; exact mint resolution is still required.
MINT_RE = re.compile(r"(?<![1-9A-HJ-NP-Za-km-z])([1-9A-HJ-NP-Za-km-z]{32,44})(?![1-9A-HJ-NP-Za-km-z])")
TICKER_RE = re.compile(r"(?<!\\w)\\$([A-Za-z][A-Za-z0-9]{1,11})(?!\\w)")
SCHEMA = """
CREATE TABLE IF NOT EXISTS social_attention (
 id INTEGER PRIMARY KEY, observed_at REAL NOT NULL, posted_at REAL,
 source TEXT NOT NULL, source_id TEXT NOT NULL, post_id TEXT NOT NULL,
 address TEXT, ticker TEXT, audience INTEGER, engagement REAL,
 UNIQUE(source, source_id, post_id, address, ticker)
);
CREATE INDEX IF NOT EXISTS social_by_address ON social_attention(address, observed_at);
CREATE TABLE IF NOT EXISTS social_chain (
 observed_at REAL NOT NULL, address TEXT NOT NULL, price_usd REAL,
 liquidity_usd REAL, vol_m5 REAL, buys_m5 INTEGER, sells_m5 INTEGER,
 PRIMARY KEY(observed_at, address)
);
CREATE INDEX IF NOT EXISTS social_chain_address ON social_chain(address, observed_at);
CREATE TABLE IF NOT EXISTS social_safety (
 address TEXT PRIMARY KEY, checked_at REAL NOT NULL,
 passed INTEGER NOT NULL, reasons TEXT NOT NULL
);
"""

def install(db: sqlite3.Connection):
    db.executescript(SCHEMA)

def parse(text: str):
    return sorted(set(MINT_RE.findall(text or ""))), sorted(set(TICKER_RE.findall(text or "")))

def record(db, *, source, source_id, post_id, text, audience=None, engagement=None,
           posted_at=None, observed_at=None):
    """Save mint references and unresolved ticker references separately."""
    now = time.time() if observed_at is None else observed_at
    mints, tickers = parse(text)
    refs = [(a, None) for a in mints] + [(None, t.upper()) for t in tickers]
    for address, ticker in refs:
        db.execute("""INSERT OR IGNORE INTO social_attention
          (observed_at,posted_at,source,source_id,post_id,address,ticker,audience,engagement)
          VALUES (?,?,?,?,?,?,?,?,?)""",
          (now, posted_at, source, str(source_id), str(post_id),
           address, ticker, audience, engagement))
    db.commit()
    return len(refs)

def snapshot(db, address, data, observed_at=None):
    now = time.time() if observed_at is None else observed_at
    db.execute("""INSERT OR REPLACE INTO social_chain
       (observed_at,address,price_usd,liquidity_usd,vol_m5,buys_m5,sells_m5)
       VALUES (?,?,?,?,?,?,?)""",
       (now,address,data.get("price_usd"),data.get("liquidity_usd"),
        data.get("vol_m5"),data.get("buys_m5"),data.get("sells_m5")))
    db.commit()

def eligible_addresses(db, since):
    return [r[0] for r in db.execute(
        "SELECT DISTINCT address FROM social_attention WHERE address IS NOT NULL AND observed_at >= ?",
        (since,))]
