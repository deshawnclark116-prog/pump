"""Forward-only paper candidates. Independent ledgers; never submit transactions."""
import math
import time
from dataclasses import replace

from .paper import PaperRules
from .research import Rules, eligible
from .scalp import Scalper, ScalpRules


class UnderTwoScalper(Scalper):
    def __init__(self, rules, paper, db, send):
        super().__init__(replace(rules, enabled=True, start_balance_usd=100, trade_usd=10),
                         paper, db, send, "paper_trades_scalp_under2", "scalp under 2x")
        for t in self.book.closed_trades():
            self.last_exit[t["address"]] = t["exit_ts"]

    @staticmethod
    def under_two(t, price):
        ref = t.reference_price()
        return bool(ref and ref > 0 and price and math.isfinite(price) and 0 < price / ref < 2)

    async def consider(self, t):
        if self.under_two(t, t.price_usd):
            await super().consider(t)

    async def _buy(self, t, price, note):
        # Check again at the actual dip quote, not just when starting the watch.
        if self.under_two(t, price):
            await super()._buy(t, price, note + "\nentry run-up below 2x")


class RecoveryPaper(Scalper):
    def __init__(self, db, send):
        self.recipe = Rules()
        r = self.recipe
        super().__init__(ScalpRules(take_profit_pct=r.take_profit, stop_loss_pct=r.stop_loss,
                                   max_hold_minutes=r.hold_seconds / 60, slippage_pct=r.slippage_pct),
                         PaperRules(fee_pct=r.fee_pct), db, send,
                         "paper_trades_recovery", "recovery")
        self.recover = {}
        # One entry per mint for this forward experiment, including across restarts.
        self.used = {t["address"] for t in self.book.open_trades() + self.book.closed_trades()}

    async def consider(self, t):
        now = time.time()
        r, addr, px = self.recipe, t.address, t.price_usd
        for key, w in list(self.recover.items()):
            if now - w["last"] > r.max_gap_seconds or now - w["start"] > r.watch_seconds:
                self.recover.pop(key, None)
        if addr in self.used or addr in self.held:
            return
        row = {"ts": now, "chain": t.chain, "price_usd": px,
               "liquidity_usd": t.liquidity_usd, "market_cap": t.market_cap, "vol_m5": t.vol_m5,
               "features": {"stage": t.stage.stage if t.stage else "UNKNOWN", "research_v1": {
                   "market_received_at": t.market_updated_at, "breadth_received_at": t.buyers_updated_at,
                   "safety_checked_at": t.safety.checked_at if t.safety else 0,
                   "safety_passed": bool(t.safety and t.safety.passed),
                   "buyers": (t.buyers or {}).get("m5"), "sellers": (t.sellers or {}).get("m5"),
                   "age_minutes": t.age_minutes, "dex_id": t.dex_id, "m5_pct": t.price_change.get("m5")}}}
        if not px or not math.isfinite(px) or px <= 0 or not eligible(row, r):
            self.recover.pop(addr, None)
            return
        w = self.recover.get(addr)
        if w is None:
            if eligible(row, r, impulse=True):
                self.recover[addr] = dict(start=now, last=now, high=px, low=px, prev=px,
                                          dip_ts=None, rises=0, signal=None)
            return
        w["last"] = now
        if w["signal"] is not None:
            if now - w["signal"] >= r.delay_seconds:
                self.recover.pop(addr, None)
                if now - w["signal"] <= r.max_gap_seconds:
                    await super()._buy(t, px, "Recovery: dip followed by confirmed rebound (paper experiment)")
                    if addr in self.held:
                        self.used.add(addr)
            return
        if w["dip_ts"] is None:
            w["high"] = max(w["high"], px)
        off = (1 - px / w["high"]) * 100
        if off > r.max_dip:
            self.recover.pop(addr, None)
            return
        if w["dip_ts"] is None and off >= r.dip:
            w.update(dip_ts=now, low=px, rises=0)
        elif w["dip_ts"] is not None:
            if px < w["low"]:
                w.update(low=px, dip_ts=now, rises=0)
            elif px > w["prev"]:
                w["rises"] += 1
            elif px < w["prev"]:
                w["rises"] = 0
            if (w["rises"] >= 2 and now - w["dip_ts"] >= r.recovery_seconds
                    and px >= w["low"] * (1 + r.rebound / 100)):
                w["signal"] = now
        w["prev"] = px
