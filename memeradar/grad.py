"""Third strategy: the graduation play (fake money only).

Every pump.fun coin starts on a "bonding curve" at ~28 SOL market cap and graduates to the open
market at ~410 SOL. This strategy watches every brand-new pump.fun coin (free PumpPortal launch
feed + free DexScreener prices), buys the ones climbing fast through the middle of the curve, and
sells near graduation, before the usual graduation dump. It keeps its own fake balance.

Rules (all market caps in SOL, so they don't move with the SOL price):
  BUY   market cap between `buy_min_sol` and `buy_max_sol` (~40-55% of the way), coin under
        `max_age_minutes` old, real buying in the last 5 min (enough buys, more buys than sells but
        some sells), price rising, and RugCheck finds no rug signs.
  SELL  - market cap reaches `sell_at_sol` (near graduation), or it graduates  -> take profit
        - your money down `stop_loss_pct` after fees                          -> stop loss
        - after your money has been up `trail_arm_pct`, the price falls `trail_pct` below its high
                                                                               -> trailing stop
        - held `max_hold_minutes` without reaching the target                 -> time's up
"""
from __future__ import annotations

import logging
import time
from collections import OrderedDict
from dataclasses import dataclass

from typing import TYPE_CHECKING

from .gradrules import GradRules  # noqa: F401  (re-exported)
from .http import Http
from .paper import PaperTrader, cost_factor
from . import safety

if TYPE_CHECKING:
    from .config import Config

log = logging.getLogger(__name__)
DEX = "https://api.dexscreener.com"
# RugCheck flags every bonding-curve coin as "Low Liquidity"; that's expected here, not a rug sign.
IGNORED_RISKS = ("rugcheck: Low Liquidity", "rugcheck: Large Amount of LP Unlocked")


@dataclass
class _Holding:
    symbol: str
    entry_ts: float
    entry_price: float
    entry_mcap_usd: float | None
    pair: str
    high: float = 0.0          # highest price seen since buying (for the trailing stop)


class GradPlay:
    def __init__(self, cfg: "Config", store_db, send) -> None:
        self.cfg, self.r = cfg, cfg.grad
        self.book = PaperTrader(store_db, cfg.paper, table="paper_trades_grad", label="grad")
        self.send = send                                   # async (text, symbol, address, pair)
        self.fresh: OrderedDict[str, tuple[float, str]] = OrderedDict()   # mint -> (born, symbol)
        self.seen_buy: set[str] = set()                    # never buy the same coin twice
        self.rejected: set[str] = set()
        self.held: dict[str, _Holding] = {}
        self.cf = cost_factor(cfg.paper)
        for row in self.book.open_trades():                # survive restarts
            self.held[row["address"]] = _Holding(row["symbol"], row["entry_ts"],
                                                 row["entry_price"] / (1 + cfg.paper.slippage_pct / 100),
                                                 row["entry_mcap"], "")
            self.seen_buy.add(row["address"])

    # ------------------------------------------------------------ intake
    def on_new_token(self, mint: str, symbol: str) -> None:
        if not self.r.enabled or mint in self.fresh:
            return
        self.fresh[mint] = (time.time(), symbol)
        while len(self.fresh) > self.r.max_tracked:
            self.fresh.popitem(last=False)

    @staticmethod
    def _pump_pair(pairs: list[dict], mint: str) -> dict | None:
        mine = [p for p in pairs if (p.get("baseToken") or {}).get("address") == mint]
        curve = [p for p in mine if p.get("dexId") == "pumpfun"]
        return curve[0] if curve else (mine[0] if mine else None)

    @staticmethod
    def _f(x) -> float:
        try:
            return float(x)
        except (TypeError, ValueError):
            return 0.0

    def mcap_sol(self, p: dict) -> float:
        px, nat, mc = self._f(p.get("priceUsd")), self._f(p.get("priceNative")), self._f(p.get("marketCap") or p.get("fdv"))
        q = ((p.get("quoteToken") or {}).get("symbol") or "").upper()
        return mc * nat / px if px and nat and q in ("SOL", "WSOL") else 0.0

    async def _prices(self, http: Http, mints: list[str]) -> dict[str, list[dict]]:
        out: dict[str, list[dict]] = {}
        for i in range(0, len(mints), 30):
            data = await http.get(f"{DEX}/tokens/v1/solana/{','.join(mints[i:i + 30])}")
            for p in data if isinstance(data, list) else []:
                out.setdefault((p.get("baseToken") or {}).get("address"), []).append(p)
        return out

    # ------------------------------------------------------------ buying
    def _entry_ok(self, p: dict, age_min: float) -> str:
        """'' if it's a buy, else why not (for logging)."""
        r = self.r
        sol = self.mcap_sol(p)
        if p.get("dexId") != "pumpfun":
            return "not on the bonding curve"
        if not (r.buy_min_sol <= sol <= r.buy_max_sol):
            return f"market cap {sol:.0f} SOL outside buy zone"
        if self._f(p.get("marketCap") or p.get("fdv")) < r.min_mcap_usd:
            return "under the minimum market cap"
        if age_min > r.max_age_minutes:
            return "too old"
        if age_min < r.min_age_minutes:
            return "too new"
        tx = (p.get("txns") or {}).get("m5") or {}
        buys, sells = int(self._f(tx.get("buys"))), int(self._f(tx.get("sells")))
        if buys < r.min_buys_m5:
            return f"only {buys} buys in 5 min"
        ratio = buys / max(sells, 1)
        if ratio < r.min_buy_sell_ratio:
            return f"buys {buys} vs sells {sells}: not enough demand"
        if ratio > r.max_buy_sell_ratio:
            return f"buys {buys} vs sells {sells}: one-sided"
        vol = self._f((p.get("volume") or {}).get("m5"))
        if r.min_usd_per_buy > 0 and vol / max(buys + sells, 1) < r.min_usd_per_buy:
            return "tiny bot buys"
        m5 = self._f((p.get("priceChange") or {}).get("m5"))
        if m5 <= 0:
            return "not rising"
        if r.max_m5_pct and m5 > r.max_m5_pct:
            return f"spiking (+{m5:.0f}% in 5 min)"
        return ""

    async def scan(self, http: Http) -> None:
        if not self.r.enabled or not self.fresh:
            return
        now = time.time()
        for mint, (born, _) in list(self.fresh.items()):          # forget old coins
            if now - born > self.r.track_minutes * 60:
                self.fresh.pop(mint, None)
        mints = [m for m in self.fresh if m not in self.seen_buy and m not in self.rejected]
        prices = await self._prices(http, mints)
        for mint in mints:
            p = self._pump_pair(prices.get(mint, []), mint)
            born, sym = self.fresh.get(mint, (now, ""))
            age = (now - born) / 60
            if not p:
                continue
            if age > self.r.dead_after_minutes and self.mcap_sol(p) < self.r.dead_below_sol:
                self.fresh.pop(mint, None)                       # going nowhere: stop polling it
                continue
            why_not = self._entry_ok(p, age)
            if why_not:
                continue
            verdict = await safety.check(http, "solana", mint, self.cfg.safety)
            if verdict is None:
                continue                                         # RugCheck had no answer; retry next poll
            real = [x for x in verdict.reasons if not x.startswith(IGNORED_RISKS)]
            if real:
                log.info("grad: skip %s: %s", sym or mint[:6], "; ".join(real))
                self.rejected.add(mint)
                continue
            await self._buy(mint, p, sym)

    async def _buy(self, mint: str, p: dict, sym: str) -> None:
        sym = sym or (p.get("baseToken") or {}).get("symbol", "")
        price = self._f(p.get("priceUsd"))
        mc = self._f(p.get("marketCap") or p.get("fdv"))
        msg = self.book.buy("solana", mint, sym, price, mcap=mc)
        self.seen_buy.add(mint)
        if not msg or "skipped" in msg:
            if msg:
                log.info(msg.replace("\n", " | "))
            return
        self.held[mint] = _Holding(sym, time.time(), price, mc, p.get("pairAddress", ""))
        sol = self.mcap_sol(p)
        tx = (p.get("txns") or {}).get("m5") or {}
        await self.send(msg + f"\n🎓 graduation play: {sol / self.r.graduation_sol:.0%} of the way to graduation "
                        f"({sol:.0f} of ~{self.r.graduation_sol:.0f} SOL) · {tx.get('buys')} buys / "
                        f"{tx.get('sells')} sells in 5 min\nsells at ~{self.r.sell_at_sol / self.r.graduation_sol:.0%} "
                        f"of the way, at -{self.r.stop_loss_pct:.0f}% of your money, or after "
                        f"{self.r.max_hold_minutes:.0f} min", sym, mint, p.get("pairAddress", ""))

    # ------------------------------------------------------------ selling
    async def manage(self, http: Http) -> None:
        if not self.held:
            return
        prices = await self._prices(http, list(self.held))
        now = time.time()
        for mint, h in list(self.held.items()):
            pairs = prices.get(mint, [])
            p = self._pump_pair(pairs, mint)
            if not p:
                continue
            price = self._f(p.get("priceUsd"))
            graduated = p.get("dexId") != "pumpfun" or any(x.get("dexId") != "pumpfun" for x in pairs)
            if graduated:                                         # use the open-market price
                live = [x for x in pairs if x.get("dexId") != "pumpfun"]
                if live:
                    p = max(live, key=lambda x: self._f((x.get("liquidity") or {}).get("usd")))
                    price = self._f(p.get("priceUsd")) or price
            if not price:
                continue
            money = (price / h.entry_price * self.cf - 1) * 100
            sol = self.mcap_sol(p)
            h.high = max(h.high or h.entry_price, price)
            best = (h.high / h.entry_price * self.cf - 1) * 100
            trailing = (self.r.trail_pct and best >= self.r.trail_arm_pct
                        and price <= h.high * (1 - self.r.trail_pct / 100))
            reason = ""
            if graduated:
                reason = "GRADUATED"
            elif sol >= self.r.sell_at_sol:
                reason = f"NEAR GRADUATION ({sol / self.r.graduation_sol:.0%})"
            elif money <= -self.r.stop_loss_pct:
                reason = "STOP LOSS"
            elif trailing:
                reason = f"TRAILING STOP ({(1 - price / h.high) * 100:.0f}% below its high, best +{best:.0f}%)"
            elif now - h.entry_ts > self.r.max_hold_minutes * 60:
                reason = "TIME'S UP"
            if reason:
                msg = self.book.sell("solana", mint, price, reason)
                self.held.pop(mint, None)
                if msg:
                    await self.send(msg, h.symbol, mint, h.pair or p.get("pairAddress", ""))
