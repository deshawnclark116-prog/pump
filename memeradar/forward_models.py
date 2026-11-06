"""Independent forward paper models.

Three different entry hypotheses evaluated on the same safety-checked token stream.
Every model keeps a separate ledger and $250 virtual capital. No wallet/real orders.
Signals use only contemporaneously received observations; historical reports must
not be presented as validated execution returns.
"""
from __future__ import annotations

import logging
import math
import time

from .paper import PaperRules
from .scalp import ScalpRules, Scalper

log = logging.getLogger(__name__)


def _data(t, now, *, min_liq, min_mcap, min_vol, min_buyers,
          min_ratio=1.1, allow_late=False):
    """Fresh, real-pool evidence only. No guessed missing buyer or price data."""
    if t.chain != "solana" or not t.dex_id or t.dex_id == "pumpfun":
        return None
    if not t.safety or not t.safety.passed or not 0 <= now - t.safety.checked_at <= 900:
        return None
    if not 0 <= now - (t.market_updated_at or 0) <= 45:
        return None
    if not 0 <= now - (t.buyers_updated_at or 0) <= 90:
        return None
    if t.stage and t.stage.stage == "DUMPING":
        return None
    if not allow_late and t.stage and t.stage.stage == "LATE":
        return None
    px = t.price_usd
    if not isinstance(px, (int, float)) or not math.isfinite(px) or px <= 0:
        return None
    if (t.liquidity_usd or 0) < min_liq or (t.market_cap or 0) < min_mcap:
        return None
    if (t.vol_m5 or 0) < min_vol:
        return None
    b, s = (t.buyers or {}).get("m5"), (t.sellers or {}).get("m5")
    if not isinstance(b, (int, float)) or not isinstance(s, (int, float)) or s < 2 or b < min_buyers:
        return None
    ratio = b / s
    if not (min_ratio <= ratio <= 5):
        return None
    m5 = (t.price_change or {}).get("m5")
    if not isinstance(m5, (int, float)) or not math.isfinite(m5):
        return None
    return dict(px=float(px), b=float(b), s=float(s), ratio=ratio, m5=float(m5))


class _ForwardBook(Scalper):
    """Same observable-cost exit engine, but independent capital and signal rules."""

    def __init__(self, db, send, *, table, label, tp, stop, hold, cooldown):
        rules = ScalpRules(enabled=True, buy_the_dip=False,
                           take_profit_pct=tp, stop_loss_pct=stop,
                           max_hold_minutes=hold, cooldown_minutes=cooldown,
                           slippage_pct=1.0, start_balance_usd=250,
                           trade_usd=10, max_open=8)
        super().__init__(rules, PaperRules(fee_pct=1.25), db, send, table=table, label=label)
        self.watches = {}
        # Persist cooldowns across runner/cache restarts.
        for row in self.book.closed_trades():
            address, exit_ts = row["address"], row["exit_ts"] or 0
            self.last_exit[address] = max(self.last_exit.get(address, 0), exit_ts)

    def available(self, addr, now):
        return addr not in self.held and now - self.last_exit.get(addr, 0) >= self.r.cooldown_minutes * 60

    def expire(self, now, seconds):
        for addr, watch in list(self.watches.items()):
            if now - watch["at"] > seconds or now - watch["last"] > 100:
                self.watches.pop(addr, None)

    async def enter(self, t, observation, reason):
        await self._buy(t, observation["px"], reason)
        if t.address in self.held:
            log.info("paper model %s entered %s: %s", self.book.tag, t.address, reason)


class PulseBreakout(_ForwardBook):
    """Confirmed near-term acceleration; buy strength instead of a dip."""

    def __init__(self, db, send):
        super().__init__(db, send, table="paper_trades_pulse",
                         label="pulse breakout", tp=22, stop=12, hold=8, cooldown=15)

    async def consider(self, t):
        now = time.time()
        self.expire(now, 180)
        addr = t.address
        if not self.available(addr, now):
            self.watches.pop(addr, None)
            return
        e = _data(t, now, min_liq=12_000, min_mcap=20_000,
                  min_vol=3_500, min_buyers=18, min_ratio=1.15)
        if e is None or not 2 <= e["m5"] <= 35:
            self.watches.pop(addr, None)
            return
        w = self.watches.get(addr)
        if w is None:
            self.watches[addr] = dict(at=now, last=now, first=e["px"],
                                      prev=e["px"], buyers=e["b"], up=0)
            return
        if now - w["last"] < 12:
            return
        if e["px"] < w["first"] * 0.93 or e["px"] > w["first"] * 1.20:
            self.watches.pop(addr, None)
            return
        w["up"] = w["up"] + 1 if e["px"] > w["prev"] * 1.002 else 0
        w["last"], w["prev"] = now, e["px"]
        if (now - w["at"] >= 35 and w["up"] >= 2
                and e["px"] >= w["first"] * 1.025 and e["b"] >= w["buyers"]):
            self.watches.pop(addr, None)
            await self.enter(t, e, f"Pulse: two rising observations, +{(e['px']/w['first']-1)*100:.1f}% "
                                      f"since watch; {e['b']:.0f}/{e['s']:.0f} unique buyers/sellers")


class FlowAccumulation(_ForwardBook):
    """Enter modest price moves when unique buyer participation expands."""

    def __init__(self, db, send):
        super().__init__(db, send, table="paper_trades_flow",
                         label="buyer flow", tp=16, stop=11, hold=12, cooldown=15)

    async def consider(self, t):
        now = time.time()
        self.expire(now, 180)
        addr = t.address
        if not self.available(addr, now):
            self.watches.pop(addr, None)
            return
        e = _data(t, now, min_liq=12_000, min_mcap=20_000,
                  min_vol=2_500, min_buyers=15, min_ratio=1.10)
        if e is None or not -4 <= e["m5"] <= 15:
            self.watches.pop(addr, None)
            return
        w = self.watches.get(addr)
        if w is None:
            self.watches[addr] = dict(at=now, last=now, first=e["px"],
                                      buyers=e["b"], prev_b=e["b"])
            return
        if now - w["last"] < 12:
            return
        if e["px"] < w["first"] * 0.96 or e["px"] > w["first"] * 1.12:
            self.watches.pop(addr, None)
            return
        if e["b"] < w["buyers"] * .85:
            self.watches[addr] = dict(at=now, last=now, first=e["px"],
                                      buyers=e["b"], prev_b=e["b"])
            return
        rising = e["b"] >= w["buyers"] + max(4, w["buyers"] * .10)
        strengthening = e["b"] >= w["prev_b"]
        w["last"], w["prev_b"] = now, e["b"]
        if (now - w["at"] >= 35 and rising and strengthening
                and e["b"] - e["s"] >= 6 and e["px"] >= w["first"] * .99):
            self.watches.pop(addr, None)
            await self.enter(t, e, f"Flow: unique buyers {w['buyers']:.0f} -> {e['b']:.0f}, "
                                      f"price {(e['px']/w['first']-1)*100:+.1f}% since watch")


class DipReclaim(_ForwardBook):
    """Initial impulse, 5-23% controlled pullback, then two-price recovery."""

    def __init__(self, db, send):
        super().__init__(db, send, table="paper_trades_reclaim",
                         label="dip reclaim", tp=20, stop=12, hold=10, cooldown=20)

    async def consider(self, t):
        now = time.time()
        self.expire(now, 540)
        addr = t.address
        if not self.available(addr, now):
            self.watches.pop(addr, None)
            return
        w = self.watches.get(addr)
        # After impulse, a temporary negative five-minute change must NOT
        # erase the watch, which was the main weakness of the strict experiment.
        e = _data(t, now, min_liq=10_000 if w else 12_000,
                  min_mcap=18_000 if w else 20_000,
                  min_vol=1_500 if w else 3_000,
                  min_buyers=12 if w else 16,
                  min_ratio=1.02 if w else 1.12, allow_late=bool(w))
        if e is None:
            self.watches.pop(addr, None)
            return
        if w is None:
            if not 3 <= e["m5"] <= 40:
                return
            self.watches[addr] = dict(at=now, last=now, high=e["px"],
                                      low=e["px"], prev=e["px"], dip_at=None,
                                      rising=0, signal_at=None)
            return
        if now - w["last"] < 12:
            return
        w["last"] = now
        if w["signal_at"] is not None:
            if (now - w["signal_at"] >= 15
                    and now - w["signal_at"] <= 90
                    and e["px"] >= w["low"] * 1.025
                    and e["b"] > e["s"]):
                self.watches.pop(addr, None)
                await self.enter(t, e, f"Reclaim: 5-23% dip, two upward prints and rebound "
                                          f"{(e['px']/w['low']-1)*100:.1f}% from observed low")
            elif now - w["signal_at"] > 90 or e["px"] < w["low"] * .98:
                self.watches.pop(addr, None)
            return
        if w["dip_at"] is None:
            w["high"] = max(w["high"], e["px"])
        drawdown = (1 - e["px"] / w["high"]) * 100
        if drawdown > 23:
            self.watches.pop(addr, None)
            return
        if w["dip_at"] is None:
            if drawdown >= 5:
                w["dip_at"], w["low"], w["rising"] = now, e["px"], 0
        else:
            if e["px"] < w["low"]:
                w["low"], w["dip_at"], w["rising"] = e["px"], now, 0
            elif e["px"] > w["prev"] * 1.002:
                w["rising"] += 1
            elif e["px"] < w["prev"]:
                w["rising"] = 0
            if (w["rising"] >= 2 and now - w["dip_at"] >= 25
                    and e["px"] >= w["low"] * 1.03 and e["b"] > e["s"]):
                w["signal_at"] = now
        w["prev"] = e["px"]
