"""In-memory token state with rolling windows (replaces Redis for the free setup)."""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


class RollingEvents:
    """Timestamped events kept for `horizon` seconds."""

    def __init__(self, horizon: float = 3600) -> None:
        self.horizon = horizon
        self.items: deque[tuple[float, object]] = deque()

    def add(self, item: object = None, ts: float | None = None) -> None:
        self.items.append((ts if ts is not None else time.time(), item))
        self.prune()

    def prune(self, now: float | None = None) -> None:
        cutoff = (now or time.time()) - self.horizon
        while self.items and self.items[0][0] < cutoff:
            self.items.popleft()

    def window(self, start_ago: float, end_ago: float = 0, now: float | None = None) -> list:
        now = now or time.time()
        lo, hi = now - start_ago, now - end_ago
        return [it for ts, it in self.items if lo <= ts and (ts < hi or end_ago == 0)]

    def count(self, start_ago: float, end_ago: float = 0, now: float | None = None) -> int:
        return len(self.window(start_ago, end_ago, now))


@dataclass
class Safety:
    passed: bool
    reasons: list[str] = field(default_factory=list)
    top10_pct: float | None = None
    holders: int | None = None
    lp_locked_pct: float | None = None
    liquidity_usd: float | None = None
    checked_at: float = field(default_factory=time.time)


@dataclass
class TokenState:
    chain: str
    address: str
    symbol: str = ""
    name: str = ""
    source: str = ""
    first_seen: float = field(default_factory=time.time)
    last_activity: float = field(default_factory=time.time)

    # market data (DexScreener snapshot)
    pair_address: str = ""
    dex_id: str = ""
    price_usd: float | None = None
    liquidity_usd: float | None = None
    market_cap: float | None = None
    vol_m5: float = 0.0
    vol_h1: float = 0.0
    buys_m5: int = 0
    sells_m5: int = 0
    buys_h1: int = 0
    sells_h1: int = 0
    pair_created_at: float | None = None
    market_updated_at: float = 0.0

    # live trades from PumpPortal: item = (wallet, side, sol_amount)
    trades: RollingEvents = field(default_factory=lambda: RollingEvents(3600))
    smart_buys: set[str] = field(default_factory=set)

    safety: Safety | None = None
    score: float = 0.0
    features: dict[str, float] = field(default_factory=dict)
    last_alert: float = 0.0

    @property
    def key(self) -> tuple[str, str]:
        return (self.chain, self.address)

    @property
    def age_minutes(self) -> float | None:
        if self.pair_created_at:
            return (time.time() - self.pair_created_at) / 60
        return None
