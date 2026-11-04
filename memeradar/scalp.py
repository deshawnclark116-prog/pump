"""Fourth strategy: the scalper (fake money only).

Quick in-and-out trades. It looks at every coin the radar is already scoring (safety-checked),
spots any that are moving up RIGHT NOW with real two-sided buying, then (buy the dip) waits for the
first pullback off the high instead of buying the burst, takes a small profit fast and
gets out, and never holds long. It can trade the same coin again after a short cooldown.

  BUY   price up `min_m5_pct`..`max_m5_pct` in the last 5 min (moving, not already spiked), enough
        buyers, more buyers than sellers but not one-sided, enough volume and liquidity, and the
        coin isn't LATE/DUMPING.
  SELL  your money +`take_profit_pct` after fees -> take profit
        your money -`stop_loss_pct` after fees    -> stop loss
        held `max_hold_minutes`                   -> time's up, out whatever the price
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from .models import TokenState
from .paper import PaperRules, PaperTrader, cost_factor
from .stage import DUMPING, LATE

log = logging.getLogger(__name__)
DEX = "https://api.dexscreener.com"


@dataclass
class ScalpRules:
    enabled: bool = True
    take_profit_pct: float = 15.0     # YOUR money after fees
    stop_loss_pct: float = 10.0       # YOUR money after fees
    max_hold_minutes: float = 10.0
    min_m5_pct: float = 3.0           # moving up right now...
    max_m5_pct: float = 25.0          # ...but not already spiked
    min_buyers_m5: int = 30
    min_buy_sell_ratio: float = 1.2
    max_buy_sell_ratio: float = 5.0
    min_volume_m5_usd: float = 5_000
    min_liquidity_usd: float = 15_000
    min_mcap_usd: float = 10_000
    cooldown_minutes: float = 15.0    # before scalping the same coin again
    slippage_pct: float = 0.5         # a $10 order barely moves a $15k+ pool
    start_balance_usd: float = 100.0
    trade_usd: float = 10.0
    max_open: int = 5
    # buy the dip: when a coin qualifies, don't buy the burst. Watch it, and buy only after it pulls
    # back `dip_pct` from its high while buyers are still ahead of sellers. Give up if it falls more
    # than `max_dip_pct` (that's a dump, not a dip) or no dip comes within `dip_watch_minutes`.
    buy_the_dip: bool = True
    dip_pct: float = 12.0
    max_dip_pct: float = 30.0
    dip_watch_minutes: float = 10.0


def qualifies(t: TokenState, r: ScalpRules) -> str:
    """Short reason if `t` is a scalp right now, else ""."""
    if not r.enabled or not t.price_usd or not t.safety or not t.safety.passed:
        return ""
    if t.stage and t.stage.stage in (LATE, DUMPING):
        return ""
    m5 = (t.price_change or {}).get("m5")
    if m5 is None or not (r.min_m5_pct <= m5 <= r.max_m5_pct):
        return ""
    if (t.market_cap or 0) < r.min_mcap_usd:
        return ""
    if (t.liquidity_usd or 0) < r.min_liquidity_usd or t.vol_m5 < r.min_volume_m5_usd:
        return ""
    b, s = (t.buyers or {}).get("m5", 0), (t.sellers or {}).get("m5", 0)
    if b < r.min_buyers_m5:
        return ""
    ratio = b / max(s, 1)
    if not (r.min_buy_sell_ratio <= ratio <= r.max_buy_sell_ratio):
        return ""
    return f"up {m5:+.0f}% in 5 min · {b} buyers / {s} sellers · ${t.vol_m5 / 1e3:.0f}k traded"


@dataclass
class _Watch:
    token: TokenState          # live object: the radar keeps refreshing its buyers/sellers
    why: str
    start_ts: float
    high: float


@dataclass
class _Hold:
    symbol: str
    chain: str
    entry_ts: float
    entry_price: float
    pair: str


class Scalper:
    def __init__(self, r: ScalpRules, paper: PaperRules, db, send) -> None:
        self.r = r
        self.rules = PaperRules(enabled=True, start_balance_usd=r.start_balance_usd, trade_usd=r.trade_usd,
                                fee_pct=paper.fee_pct, slippage_pct=r.slippage_pct, max_open=r.max_open)
        self.book = PaperTrader(db, self.rules, table="paper_trades_scalp", label="scalp")
        self.cf = cost_factor(self.rules)
        self.send = send                                  # async (text, chain, address, symbol, pair)
        self.held: dict[str, _Hold] = {}
        self.watching: dict[str, _Watch] = {}
        self.last_exit: dict[str, float] = {}
        for row in self.book.open_trades():
            self.held[row["address"]] = _Hold(row["symbol"], row["chain"], row["entry_ts"],
                                              row["entry_price"] / (1 + r.slippage_pct / 100), "")

    def money(self, h: _Hold, price: float) -> float:
        return (price / h.entry_price * self.cf - 1) * 100

    async def consider(self, t: TokenState) -> None:
        if t.address in self.held or t.address in self.watching or \
                time.time() - self.last_exit.get(t.address, 0) < self.r.cooldown_minutes * 60:
            return
        why = qualifies(t, self.r)
        if not why:
            return
        if self.r.buy_the_dip:
            self.watching[t.address] = _Watch(t, why, time.time(), t.price_usd)
            log.info("scalp: watching %s for a %.0f%% dip", t.symbol, self.r.dip_pct)
            return
        await self._buy(t, t.price_usd, f"⚡ scalp: {why}")

    async def _buy(self, t: TokenState, price: float, note: str) -> None:
        msg = self.book.buy(t.chain, t.address, t.symbol, price,
                            mcap=(t.market_cap or 0) * price / t.price_usd if t.price_usd else t.market_cap)
        if not msg or "skipped" in msg:
            return
        self.held[t.address] = _Hold(t.symbol, t.chain, time.time(), price, t.pair_address or "")
        await self.send(msg + f"\n{note}\nsells at +{self.r.take_profit_pct:.0f}% or "
                        f"-{self.r.stop_loss_pct:.0f}% of your money, or after {self.r.max_hold_minutes:.0f} min",
                        t.chain, t.address, t.symbol, t.pair_address)

    async def _check_dips(self, prices: dict[str, float]) -> None:
        now = time.time()
        for addr, w in list(self.watching.items()):
            px = prices.get(addr)
            if now - w.start_ts > self.r.dip_watch_minutes * 60:
                self.watching.pop(addr, None)                      # no dip came: let it go
                continue
            if not px:
                continue
            w.high = max(w.high, px)
            off = (1 - px / w.high) * 100
            if off > self.r.max_dip_pct:
                log.info("scalp: %s fell %.0f%% from its high, that's a dump: skip", w.token.symbol, off)
                self.watching.pop(addr, None)
                self.last_exit[addr] = now
                continue
            b, s = (w.token.buyers or {}).get("m5", 0), (w.token.sellers or {}).get("m5", 0)
            if off >= self.r.dip_pct and b > s:
                self.watching.pop(addr, None)
                await self._buy(w.token, px, f"⚡ scalp (bought the dip): {off:.0f}% below its high, "
                                             f"buyers still ahead ({b} vs {s})\nwhy it was watched: {w.why}")

    async def manage(self, http) -> None:
        if not self.held and not self.watching:
            return
        by_chain: dict[str, list[str]] = {}
        for addr, h in self.held.items():
            by_chain.setdefault(h.chain, []).append(addr)
        for addr, w in self.watching.items():
            by_chain.setdefault(w.token.chain, []).append(addr)
        prices: dict[str, float] = {}
        pairs: dict[str, str] = {}
        for chain, addrs in by_chain.items():
            for i in range(0, len(addrs), 30):
                data = await http.get(f"{DEX}/tokens/v1/{chain}/{','.join(addrs[i:i + 30])}")
                for p in data if isinstance(data, list) else []:
                    a = (p.get("baseToken") or {}).get("address")
                    liq = float((p.get("liquidity") or {}).get("usd") or 0)
                    if (a in self.held or a in self.watching) and (a not in prices or liq > pairs.get(a + "#liq", 0)):
                        try:
                            prices[a] = float(p.get("priceUsd") or 0)
                        except ValueError:
                            continue
                        pairs[a], pairs[a + "#liq"] = p.get("pairAddress", ""), liq
        now = time.time()
        for addr, h in list(self.held.items()):
            price = prices.get(addr)
            if not price:
                continue
            m = self.money(h, price)
            reason = ("TAKE PROFIT" if m >= self.r.take_profit_pct else
                      "STOP LOSS" if m <= -self.r.stop_loss_pct else
                      "TIME'S UP" if now - h.entry_ts > self.r.max_hold_minutes * 60 else "")
            if reason:
                msg = self.book.sell(h.chain, addr, price, reason)
                self.held.pop(addr, None)
                self.last_exit[addr] = now
                if msg:
                    await self.send(msg, h.chain, addr, h.symbol, h.pair or pairs.get(addr, ""))
        await self._check_dips(prices)
