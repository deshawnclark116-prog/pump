"""Exit alerts: once a coin has been alerted, keep watching it and say when to get out.

Take profits are measured on YOUR MONEY after buy/sell fees and slippage, not the coin price:

  🟠 TAKE PROFIT 1  +20%:  don't sell. Your stop loss moves up to +20% (this trade is now locked in)
  🟢 TAKE PROFIT 2  +100%: sell 50%
  🟢 TAKE PROFIT 3  +200%: sell 25% more
  🟢 TAKE PROFIT 4  +300%: sell the last 25%
  🔴 STOP LOSS      after a take profit, your money fell back to that take profit's level
                    (the stop moves up with each one: +20%, then +100%, then +200%): sell everything left

  🟠 FADING        the coin's stage turned LATE (buyers drying up / well off its high)
  🔴 GET OUT       dropped 15% from its high since the alert (trailing stop)
  🔴 STOP LOSS     YOUR MONEY down 20% (after fees and slippage)
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
    take_profit_levels: list[float] = field(default_factory=lambda: [20.0, 100.0, 200.0, 300.0])  # YOUR money
    # % of the ORIGINAL position to sell at each level above (0 = sell nothing, just lock in)
    take_profit_sell_pct: list[float] = field(default_factory=lambda: [0.0, 50.0, 25.0, 25.0])
    trailing_stop_pct: float = 15.0     # % drop from the high since the alert
    trailing_arm_pct: float = 10.0      # trailing stop only arms once it's been up this much
    stop_loss_pct: float = 20.0         # % of YOUR money lost (after fees), not just the coin price
    liquidity_drop_pct: float = 50.0    # % of pool liquidity gone since the alert
    max_watch_hours: float = 6.0
    check_interval: int = 3             # seconds between exit checks (DexScreener only, cheap)


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
    sold_pct: float = 0.0          # % of the original position the take-profits said to sell so far
    faded_sent: bool = False
    closed: bool = False
    close_reason: str = ""
    last_price: float | None = None

    cost_factor: float = 1.0   # what a round trip keeps after fees + slippage (0.94 = 6% lost)

    def change(self, price: float) -> float:
        """Coin price change since the alert."""
        return (price / self.entry_price - 1) * 100

    def money_change(self, price: float) -> float:
        """What happened to YOUR money if you bought at the alert and sold now, after fees."""
        return (price / self.entry_price * self.cost_factor - 1) * 100


@dataclass
class ExitSignal:
    level: str     # "orange" | "green" | "red" | "info"
    title: str
    detail: str
    sell_pct: float = 0.0   # take profit: % of the ORIGINAL position to sell now


def evaluate(pos: Position, price: float | None, liquidity: float | None, stage: str | None,
             rules: ExitRules, now: float | None = None) -> list[ExitSignal]:
    """Update the position with the latest data and return any exit signals to send."""
    now = now or time.time()
    if pos.closed or not price or not pos.entry_price:
        return []
    pos.last_price = price
    pos.peak_price = max(pos.peak_price or pos.entry_price, price)
    chg = pos.change(price)
    money = pos.money_change(price)
    peak_chg = pos.change(pos.peak_price)
    off_high = (1 - price / pos.peak_price) * 100
    out: list[ExitSignal] = []

    def close(title: str, detail: str) -> None:
        pos.closed, pos.close_reason = True, title
        out.append(ExitSignal("red", title, detail))

    # --- red: get out (first match wins, then the watch ends)
    locked = max(pos.tp_hit) if pos.tp_hit else None   # each take profit moves the stop up to its level
    if pos.entry_liquidity and liquidity is not None and \
            liquidity <= pos.entry_liquidity * (1 - rules.liquidity_drop_pct / 100):
        close("RUG WARNING", f"pool liquidity down {100 - liquidity / pos.entry_liquidity * 100:.0f}% since alert")
    elif locked is not None:
        if money <= locked:
            close("STOP LOSS", f"your money fell back to +{locked:.0f}% (your locked-in stop): "
                               f"sell everything left (now {money:+.0f}%)")
    elif money <= -rules.stop_loss_pct:
        close("STOP LOSS", f"your money {money:+.0f}% after fees (coin {chg:+.0f}%)")
    elif peak_chg >= rules.trailing_arm_pct and off_high >= rules.trailing_stop_pct:
        close("GET OUT", f"{off_high:.0f}% off its high (high was {peak_chg:+.0f}%)")
    elif stage == DUMPING:
        close("DUMPING", "sellers swamping buyers / falling hard")
    if pos.closed:
        return out

    # --- take profits
    levels = list(rules.take_profit_levels)
    sells = list(rules.take_profit_sell_pct) + [0.0] * len(levels)
    for n, lvl in enumerate(levels):
        if money < lvl or lvl in pos.tp_hit:
            continue
        pos.tp_hit.append(lvl)
        gain = f"your money {money:+.0f}% after fees (coin {chg:+.0f}%)"
        sell = min(sells[n], 100 - pos.sold_pct)
        if n == len(levels) - 1:
            sell = 100 - pos.sold_pct                  # last level: sell whatever is left
        if sell <= 0:
            out.append(ExitSignal("orange", f"TAKE PROFIT {n + 1}",
                                  f"{gain} → don't sell yet. Stop loss moves up to +{lvl:.0f}%: "
                                  f"this trade can't lose now"))
            continue
        pos.sold_pct += sell
        if pos.sold_pct >= 99.99:
            pos.closed, pos.close_reason = True, f"TAKE PROFIT {n + 1}"
            out.append(ExitSignal("green", f"TAKE PROFIT {n + 1}", f"{gain} → sell the rest", sell))
            return out
        out.append(ExitSignal("green", f"TAKE PROFIT {n + 1}",
                              f"{gain} → sell {sell:.0f}% of what you bought "
                              f"({100 - pos.sold_pct:.0f}% left riding, stop moves up to +{lvl:.0f}%)", sell))
    if stage == LATE and not pos.faded_sent:
        pos.faded_sent = True
        out.append(ExitSignal("orange", "FADING", "buyers drying up / move looks tired"))

    if now - pos.entry_ts > rules.max_watch_hours * 3600:
        pos.closed, pos.close_reason = True, "watch ended"
        out.append(ExitSignal("info", "WATCH ENDED", f"{rules.max_watch_hours:.0f}h passed"))
    return out


def format_exit(pos: Position, sig: ExitSignal) -> str:
    icon = {"red": "🔴", "orange": "🟠", "green": "🟢"}.get(sig.level, "⚪")
    price = pos.last_price or pos.entry_price
    mins = (time.time() - pos.entry_ts) / 60
    return (f"{icon} {sig.title}: ${pos.symbol}\n{sig.detail}\n"
            f"your money {pos.money_change(price):+.0f}% · coin {pos.change(price):+.0f}% "
            f"(best {pos.change(pos.peak_price):+.0f}%) · "
            f"{mins:.0f} min since alert\n"
            f"{pos.address}\n"
            f"https://dexscreener.com/{pos.chain}/{pos.pair_address or pos.address}")
