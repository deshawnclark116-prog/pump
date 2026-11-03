"""Fake-money trading ("paper trading").

Every entry alert buys a fixed dollar amount with pretend money and then does exactly what the
exit alerts say: take profit 1 (+50%) sells nothing but locks the stop loss at +50%, take profit 2
(+100%) sells 50%, take profit 3 (+200%) sells 25% more, take profit 4 sells the rest, and any red
alert (or the end of the watch) sells everything left. Fees and slippage are charged the way a real pump.fun /
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
    trade_usd: float = 10.0         # spent on every entry alert
    fee_pct: float = 1.25           # per buy and per sell (trading-service + pump.fun fees, estimate)
    slippage_pct: float = 2.0       # you buy a bit higher / sell a bit lower than the quoted price
    max_open: int = 5               # never hold more than this many coins at once


def cost_factor(r: "PaperRules") -> float:
    """Share of the money a full buy+sell round trip keeps (fees both ways + slippage both ways)."""
    return (1 - r.fee_pct / 100) ** 2 * (1 - r.slippage_pct / 100) / (1 + r.slippage_pct / 100)


SCHEMA = """
CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT, chain TEXT, address TEXT, symbol TEXT,
    entry_ts REAL, entry_price REAL, usd_in REAL, tokens REAL,
    exit_ts REAL, exit_price REAL, usd_out REAL, reason TEXT,
    tokens_sold REAL DEFAULT 0, usd_partial REAL DEFAULT 0);
"""
NEW_COLUMNS = {"tokens_sold": "REAL DEFAULT 0", "usd_partial": "REAL DEFAULT 0", "entry_mcap": "REAL"}


def money(x: float | None) -> str:
    if not x:
        return "?"
    for div, suf in ((1e9, "B"), (1e6, "M"), (1e3, "k")):
        if x >= div:
            return f"${x / div:.1f}{suf}"
    return f"${x:.0f}"


def coin_price(x: float) -> str:
    return f"${x:.10f}".rstrip("0") if x < 0.01 else f"${x:.4f}"


class PaperTrader:
    def __init__(self, db, rules: PaperRules) -> None:
        self.db, self.rules = db, rules
        self.db.executescript(SCHEMA)
        have = {r[1] for r in self.db.execute("PRAGMA table_info(paper_trades)")}
        for col, typ in NEW_COLUMNS.items():   # upgrade databases made before partial sells existed
            if col not in have:
                self.db.execute(f"ALTER TABLE paper_trades ADD COLUMN {col} {typ}")
        self.db.commit()

    # ---------------------------------------------------------------- state
    def open_trades(self) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM paper_trades WHERE exit_ts IS NULL ORDER BY entry_ts")]

    def closed_trades(self) -> list[dict]:
        return [dict(r) for r in self.db.execute(
            "SELECT * FROM paper_trades WHERE exit_ts IS NOT NULL ORDER BY entry_ts")]

    def cash(self) -> float:
        r = self.db.execute(
            "SELECT COALESCE(SUM(usd_in),0), COALESCE(SUM(CASE WHEN exit_ts IS NULL THEN usd_partial "
            "ELSE usd_out END),0) FROM paper_trades").fetchone()
        return self.rules.start_balance_usd - r[0] + r[1]

    def is_open(self, chain: str, address: str) -> bool:
        return self.db.execute("SELECT 1 FROM paper_trades WHERE chain=? AND address=? AND exit_ts IS NULL",
                               (chain, address)).fetchone() is not None

    def record(self) -> tuple[int, int]:
        closed = self.closed_trades()
        wins = sum(1 for t in closed if t["usd_out"] > t["usd_in"])
        return wins, len(closed) - wins

    # ---------------------------------------------------------------- actions
    def buy(self, chain: str, address: str, symbol: str, price: float, now: float | None = None,
            mcap: float | None = None, stop_loss_pct: float | None = None,
            first_tp_pct: float | None = None) -> str | None:
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
            "INSERT INTO paper_trades (chain,address,symbol,entry_ts,entry_price,usd_in,tokens,entry_mcap) "
            "VALUES (?,?,?,?,?,?,?,?)",
            (chain, address, symbol, now or time.time(), fill, r.trade_usd, tokens, mcap))
        self.db.commit()
        lines = [f"🤖 PAPER BUY ${symbol}: ${r.trade_usd:.2f} (fake money)",
                 f"bought at market cap {money(mcap)} · price {coin_price(price)}"]
        if mcap and (stop_loss_pct is not None or first_tp_pct is not None):
            cf = cost_factor(r)
            at = lambda pct: money(mcap * (1 + pct / 100) / cf)  # noqa: E731  market cap where YOUR money is at pct
            bits = []
            if stop_loss_pct is not None:
                bits.append(f"stop loss if it falls to {at(-stop_loss_pct)}")
            if first_tp_pct is not None:
                bits.append(f"take profit 1 at {at(first_tp_pct)}")
            lines.append(" · ".join(bits))
        lines.append(f"cash left ${self.cash():.2f} · holding {len(self.open_trades())} coin(s)")
        return "\n".join(lines)

    def _mcap_at(self, row, price: float | None) -> str:
        """Market cap at `price`, from the market cap when it was bought."""
        if not row["entry_mcap"] or not price or not row["entry_price"]:
            return ""
        alert_price = row["entry_price"] / (1 + self.rules.slippage_pct / 100)
        return f" at market cap {money(row['entry_mcap'] * price / alert_price)}"

    def take_profit(self, chain: str, address: str, price: float | None, sell_pct: float) -> str | None:
        """Sell `sell_pct`% of the ORIGINAL coins bought (what the take-profit alert says to sell)."""
        r = self.rules
        row = self.db.execute("SELECT * FROM paper_trades WHERE chain=? AND address=? AND exit_ts IS NULL",
                              (chain, address)).fetchone()
        if not row or not price or sell_pct <= 0:
            return None
        left = row["tokens"] - (row["tokens_sold"] or 0)
        sell_tokens = min(left, row["tokens"] * sell_pct / 100)
        if left - sell_tokens <= row["tokens"] * 0.001:          # nothing meaningful left: close it
            return self.sell(chain, address, price, "TAKE PROFIT")
        per_token = price * (1 - r.slippage_pct / 100) * (1 - r.fee_pct / 100)   # cash per coin sold
        got = sell_tokens * per_token
        self.db.execute("UPDATE paper_trades SET tokens_sold=?, usd_partial=? WHERE id=?",
                        ((row["tokens_sold"] or 0) + sell_tokens, (row["usd_partial"] or 0) + got, row["id"]))
        self.db.commit()
        still = left - sell_tokens
        taken = (row["usd_partial"] or 0) + got
        return (f"🤖 PAPER TAKE PROFIT ${row['symbol']}: sold {sell_pct:.0f}% for ${got:.2f}"
                f"{self._mcap_at(row, price)}\n"
                f"taken out so far ${taken:.2f} of ${row['usd_in']:.2f} put in · "
                f"{still / row['tokens'] * 100:.0f}% still riding (worth ${still * per_token:.2f})")

    def sell(self, chain: str, address: str, price: float | None, reason: str,
             now: float | None = None) -> str | None:
        r = self.rules
        row = self.db.execute("SELECT * FROM paper_trades WHERE chain=? AND address=? AND exit_ts IS NULL",
                              (chain, address)).fetchone()
        if not row:
            return None
        fill = max(0.0, (price or 0) * (1 - r.slippage_pct / 100))
        left = max(0.0, row["tokens"] - (row["tokens_sold"] or 0))
        usd_out = (row["usd_partial"] or 0) + left * fill * (1 - r.fee_pct / 100)
        self.db.execute("UPDATE paper_trades SET exit_ts=?, exit_price=?, usd_out=?, reason=? WHERE id=?",
                        (now or time.time(), fill, usd_out, reason, row["id"]))
        self.db.commit()
        pnl = usd_out - row["usd_in"]
        pct = pnl / row["usd_in"] * 100
        wins, losses = self.record()
        icon = "✅" if pnl > 0 else "❌"
        return (f"🤖 PAPER SELL ${row['symbol']} ({reason}): {icon} {pct:+.0f}% · {pnl:+.2f} USD after fees\n"
                f"sold{self._mcap_at(row, price) or ''} · bought at market cap {money(row['entry_mcap'])}\n"
                f"{self.summary_line()} · {wins} wins / {losses} losses")

    def summary_line(self) -> str:
        """Total = cash + open coins at what we paid for them, minus what was already taken back
        (a coin that already returned its stake counts as 0 until it is sold)."""
        total = self.cash() + sum(max(0.0, t["usd_in"] - (t["usd_partial"] or 0)) for t in self.open_trades())
        change = total - self.rules.start_balance_usd
        return f"fake balance ${total:.2f} ({change:+.2f} since start)"
