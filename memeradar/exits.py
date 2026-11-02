"""Exit alerts: once a coin has been alerted, keep watching it and say when to get out.

  🟠 TAKE PROFIT   up +50% / +100% / +200% since the alert (each level once)
  🟠 FADING        the coin's stage turned LATE (buyers drying up / well off its high)
  🔴 GET OUT       dropped 25% from its high since the alert (trailing stop)
  🔴 STOP LOSS     down 30% from the alert price
  🔴 DUMPING       stage turned DUMPING (sellers swamping buyers)
  🔴 RUG WARNING   pool liquidity fell by half or more – money is being pulled out

A red alert ends the watch. Watching also ends after `max_watch_hours`.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field

from .stage import DUMPING, LATE


@dataclass
class ExitRules:
    take_profit_levels: list[float] = field(default_factory=lambda: [50.0, 100.0, 200.0])  # % gain
    trailing_stop_pct: float = 25.0     # % drop from the high since the alert
    trailing_arm_pct: float = 10.0      # trailing stop only arms once it's been up this much
    stop_loss_pct: float = 30.0         # % below the alert price
    liquidity_drop_pct: float = 50.0    # % of pool liquidity gone since the alert
    max_watch_hours: float = 6.0
    check_interval: int = 10            # seconds between exit checks (DexScreener only, cheap)


@dataclass
class Position:
    chain: str
    address: str
    symbol: str
    pair_address: str
    entry_ts: float
    entry_price: float
    entry_liquidity: float | None = None
    peak_price: float = 0.0
    tp_hit: list[float] = field(default_factory=list)
    faded_sent: bool = False
    closed: bool = False
    close_reason: str = ""
    last_price: float | None = None

    def change(self, price: float) -> float:
        return (price / self.entry_price - 1) * 100


@dataclass
class ExitSignal:
    level: str     # "orange" | "red" | "info"
    title: str
    detail: str


def evaluate(pos: Position, price: float | None, liquidity: float | None, stage: str | None,
             rules: ExitRules, now: float | None = None) -> list[ExitSignal]:
    """Update the position with the latest data and return any exit signals to send."""
    now = now or time.time()
    if pos.closed or not price or not pos.entry_price:
        return []
    pos.last_price = price
    pos.peak_price = max(pos.peak_price or pos.entry_price, price)
    chg = pos.change(price)
    peak_chg = pos.change(pos.peak_price)
    off_high = (1 - price / pos.peak_price) * 100
    out: list[ExitSignal] = []

    def close(title: str, detail: str) -> None:
        pos.closed, pos.close_reason = True, title
        out.append(ExitSignal("red", title, detail))

    # --- red: get out (first match wins, then the watch ends)
    if pos.entry_liquidity and liquidity is not None and \
            liquidity <= pos.entry_liquidity * (1 - rules.liquidity_drop_pct / 100):
        close("RUG WARNING", f"pool liquidity down {100 - liquidity / pos.entry_liquidity * 100:.0f}% since alert")
    elif chg <= -rules.stop_loss_pct:
        close("STOP LOSS", f"{chg:+.0f}% from alert price")
    elif peak_chg >= rules.trailing_arm_pct and off_high >= rules.trailing_stop_pct:
        close("GET OUT", f"{off_high:.0f}% off its high (high was {peak_chg:+.0f}%)")
    elif stage == DUMPING:
        close("DUMPING", "sellers swamping buyers / falling hard")
    if pos.closed:
        return out

    # --- orange: take profit / warning
    for lvl in rules.take_profit_levels:
        if chg >= lvl and lvl not in pos.tp_hit:
            pos.tp_hit.append(lvl)
            out.append(ExitSignal("orange", "TAKE PROFIT", f"up {chg:+.0f}% since alert (passed +{lvl:.0f}%)"))
    if stage == LATE and not pos.faded_sent:
        pos.faded_sent = True
        out.append(ExitSignal("orange", "FADING", "buyers drying up / move looks tired"))

    if now - pos.entry_ts > rules.max_watch_hours * 3600:
        pos.closed, pos.close_reason = True, "watch ended"
        out.append(ExitSignal("info", "WATCH ENDED", f"{rules.max_watch_hours:.0f}h passed"))
    return out


def format_exit(pos: Position, sig: ExitSignal) -> str:
    icon = {"red": "🔴", "orange": "🟠"}.get(sig.level, "⚪")
    price = pos.last_price or pos.entry_price
    mins = (time.time() - pos.entry_ts) / 60
    return (f"{icon} {sig.title}: ${pos.symbol}\n{sig.detail}\n"
            f"now {pos.change(price):+.0f}% · best {pos.change(pos.peak_price):+.0f}% · "
            f"{mins:.0f} min since alert\n"
            f"https://dexscreener.com/{pos.chain}/{pos.pair_address or pos.address}")
