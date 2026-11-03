"""Fake-money trading ("paper trading").

Every entry alert buys a fixed dollar amount with pretend money, and the first red exit alert
(or the end of the watch) sells it all. Fees and slippage are charged the way a real pump.fun /
PumpPortal trade would charge them, so the fake balance shows what the strategy would really
have earned. Nothing here touches a wallet or sends a transaction.
"""
from __future__ import annotations

import time
from dataclasses import dataclass


@dataclass
class PaperRules:
    enabled: bool = True
    start_balance_usd: float = 100.0
    trade_usd: float = 5.0          # spent on every entry alert
    fee_pct: float = 1.25           # per buy and per sell (trading-service + pump.fun fees, estimate)
    slippage_pct: float = 2.0       # you buy a bit higher / sell a bit lower than the quoted price
    max_open: int = 5               # never hold more than this many coins at once


SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT, chain TEXT, address TEXT, symbol TEXT,
    entry_ts REAL, entry_price REAL, usd_in REAL, tokens REAL,
    exit_ts REAL, exit_price REAL, usd_out REAL, reason TEXT);
"""


class PaperTrader:
    def __init__(self, db, rules: PaperRules) -> None:
        self.db, self.rules = db, rules
        self.db.executescript(SCHEMA)

    # ---------------------------------------------------------------- state
    def open_trades(self) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM paper_trades WHERE exit_ts IS NULL ORDER BY entry_ts")]

    def closed_trades(self) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM paper_trades WHERE exit_ts IS NOT NULL ORDER BY entry_ts")]

    def cash(self) -> float:
        r = self.db.execute("SELECT COALESCE(SUM(usd_in),0), COALESCE(SUM(usd_out),0) FROM paper_trades").fetchone()
        return self.rules.start_balance_usd - r[0] + r[1]

    def is_open(self, chain: str, address: str) -> bool:
        return self.db.execute("SELECT 1 FROM paper_trades WHERE chain=? AND address=? AND exit_ts IS NULL",
                               (chain, address)).fetchone() is not None

    def record(self) -> tuple[int, int]:
        closed = self.closed_trades()
        wins = sum(1 for t in closed if t["usd_out"] > t["usd_in"])
        return wins, len(closed) - wins

    # ---------------------------------------------------------------- actions
    def buy(self, chain: str, address: str, symbol: str, price: float, now: float | None = None) -> str | None:
        r = self.rules
        if not r.enabled or not price or self.is_open(chain, address):
            return None
        if len(self.open_trades()) >= r.max_open:
            return f"🤖 PAPER: skipped ${symbol}, already holding {r.max_open} coins"
        if self.cash() < r.trade_usd:
            return f"🤖 PAPER: skipped ${symbol}, fake balance too low (${self.cash():.2f})"
        fill = price * (1 + r.slippage_pct / 100)
        tokens = r.trade_usd * (1 - r.fee_pct / 100) / fill
        self.db.execute(
            "INSERT INTO paper_trades (chain,address,symbol,entry_ts,entry_price,usd_in,tokens) VALUES (?,?,?,?,?,?,?)",
            (chain, address, symbol, now or time.time(), fill, r.trade_usd, tokens))
        self.db.commit()
        return (f"🤖 PAPER BUY ${symbol}: ${r.trade_usd:.2f} (fake money)\n"
                f"cash left ${self.cash():.2f} · holding {len(self.open_trades())} coin(s)")

    def sell(self, chain: str, address: str, price: float | None, reason: str,
             now: float | None = None) -> str | None:
        r = self.rules
        row = self.db.execute("SELECT * FROM paper_trades WHERE chain=? AND address=? AND exit_ts IS NULL",
                              (chain, address)).fetchone()
        if not row:
            return None
        fill = max(0.0, (price or 0) * (1 - r.slippage_pct / 100))
        usd_out = row["tokens"] * fill * (1 - r.fee_pct / 100)
        self.db.execute("UPDATE paper_trades SET exit_ts=?, exit_price=?, usd_out=?, reason=? WHERE id=?",
                        (now or time.time(), fill, usd_out, reason, row["id"]))
        self.db.commit()
        pnl = usd_out - row["usd_in"]
        pct = pnl / row["usd_in"] * 100
        wins, losses = self.record()
        icon = "✅" if pnl > 0 else "❌"
        return (f"🤖 PAPER SELL ${row['symbol']} ({reason}): {icon} {pct:+.0f}% · {pnl:+.2f} USD after fees\n"
                f"{self.summary_line()} · {wins} wins / {losses} losses")

    def summary_line(self) -> str:
        """Total = cash + open coins at what we paid for them (we don't re-price them here)."""
        total = self.cash() + sum(t["usd_in"] for t in self.open_trades())
        change = total - self.rules.start_balance_usd
        return f"fake balance ${total:.2f} ({change:+.2f} since start)"
