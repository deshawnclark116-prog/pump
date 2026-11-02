"""Feature extraction + composite score. Every feature is normalised to 0..1."""
from __future__ import annotations

import math
from collections import defaultdict

from .config import Weights
from .models import RollingEvents, TokenState

RECENT = 30 * 60      # social "now" window
BASELINE = 120 * 60   # social baseline window


def sat(x: float, half: float) -> float:
    """Saturating curve: 0 -> 0, half -> 0.5, inf -> 1."""
    return x / (x + half) if x > 0 else 0.0


def clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


def gini(values: list[float]) -> float:
    """0 = perfectly even, 1 = one wallet holds everything."""
    vals = sorted(v for v in values if v > 0)
    n = len(vals)
    if n < 2:
        return 1.0 if n == 1 else 0.0
    cum = sum((i + 1) * v for i, v in enumerate(vals))
    return (2 * cum) / (n * sum(vals)) - (n + 1) / n


class MentionIndex:
    """Rolling social mentions keyed by symbol and by contract address (authors kept to discount spam)."""

    def __init__(self) -> None:
        self.by_symbol: dict[str, RollingEvents] = defaultdict(lambda: RollingEvents(BASELINE + RECENT))
        self.by_address: dict[str, RollingEvents] = defaultdict(lambda: RollingEvents(BASELINE + RECENT))
        self._seen: set[str] = set()

    def add(self, post_id: str, author: str, symbols, addresses, ts: float | None = None) -> bool:
        if post_id in self._seen:
            return False
        self._seen.add(post_id)
        for s in symbols:
            self.by_symbol[s.upper()].add(author, ts)
        for a in addresses:
            self.by_address[a].add(author, ts)
        return True

    def stats(self, symbol: str, address: str, now: float | None = None) -> tuple[float, float]:
        """(recent unique authors, baseline unique authors per RECENT window). Address hits count
        fully; bare ticker hits count half because tickers collide across tokens."""
        def uniq(ev: RollingEvents | None, a: float, b: float) -> int:
            return len(set(ev.window(a, b, now))) if ev else 0

        sym = self.by_symbol.get(symbol.upper()) if symbol else None
        adr = self.by_address.get(address)
        recent = uniq(adr, RECENT, 0) + 0.5 * uniq(sym, RECENT, 0)
        base = uniq(adr, RECENT + BASELINE, RECENT) + 0.5 * uniq(sym, RECENT + BASELINE, RECENT)
        return recent, base * RECENT / BASELINE


def social_velocity(recent: float, baseline: float) -> float:
    accel = max(0.0, recent - baseline)
    return sat(recent + 2 * accel, 4)


def volume_acceleration(t: TokenState) -> float:
    if t.vol_h1 <= 0:
        return 0.0
    depth = t.liquidity_usd or (t.safety.liquidity_usd if t.safety else None) or t.market_cap
    vliq = t.vol_h1 / depth if depth else 0.0
    age = t.age_minutes or 60
    expected_m5_share = 5 / clip(age, 5, 60)
    accel = (t.vol_m5 / t.vol_h1) / expected_m5_share   # 1.0 = steady, >1 = speeding up
    return 0.5 * sat(vliq, 1.0) + 0.3 * clip((accel - 1) / 2) + 0.2 * sat(t.vol_h1, 50_000)


def wallet_diversity(t: TokenState, window: float = 15 * 60) -> float:
    buys = [it for it in t.trades.window(window) if it and it[1] == "buy"]
    if t.buyers and t.sellers is not None:
        # unique wallets (free, GeckoTerminal): breadth of buying and buyer/seller balance
        b, s = t.buyers.get("m15", 0), t.sellers.get("m15", 0)
        balance = b / (b + s) if b + s else 0.0
        live = sat(b, 60) * clip((balance - 0.35) / 0.3)
    elif buys:
        per_wallet: dict[str, float] = defaultdict(float)
        for wallet, _, sol in buys:
            per_wallet[wallet] += sol
        concentration = gini(list(per_wallet.values()))
        live = sat(len(per_wallet), 40) * (1 - 0.7 * concentration)
    else:
        total = t.buys_h1 + t.sells_h1
        ratio = t.buys_h1 / total if total else 0.0
        live = sat(t.buys_h1, 150) * clip((ratio - 0.3) / 0.4)
    holders = t.safety.holders if t.safety and t.safety.holders else 0
    spread = 1 - (t.safety.top10_pct or 0) / 100 if t.safety and t.safety.top10_pct is not None else 0.5
    return 0.6 * live + 0.25 * sat(holders, 500) + 0.15 * spread


def liquidity_depth(t: TokenState) -> float:
    liq = t.liquidity_usd or (t.safety.liquidity_usd if t.safety else None)
    if not liq or liq <= 0:
        return 0.0
    return clip((math.log10(liq) - math.log10(5_000)) / 2)   # $5k -> 0, $500k -> 1


def smart_money(t: TokenState) -> float:
    return sat(len(t.smart_buys), 1.5)


def compute_features(t: TokenState, mentions: MentionIndex, now: float | None = None) -> dict[str, float]:
    recent, base = mentions.stats(t.symbol, t.address, now)
    return {
        "social_velocity": round(social_velocity(recent, base), 4),
        "volume_acceleration": round(volume_acceleration(t), 4),
        "wallet_diversity": round(wallet_diversity(t), 4),
        "liquidity_depth": round(liquidity_depth(t), 4),
        "smart_money": round(smart_money(t), 4),
        "mentions_recent": recent,
    }


def composite(features: dict[str, float], weights: Weights | dict[str, float]) -> float:
    w = weights.as_dict() if isinstance(weights, Weights) else weights
    total = sum(w.values()) or 1.0
    return round(sum(w[k] * features.get(k, 0.0) for k in w) / total, 4)
