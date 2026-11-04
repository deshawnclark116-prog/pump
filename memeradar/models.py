"""In-memory token state with rolling windows (replaces Redis for the free setup)."""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field

from .stage import StageResult


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
    pair_created_at: float | None = None   # when the main (deepest) pool opened
    token_created_at: float | None = None  # oldest pool of the coin = its real age
    start_mcap: float | None = None        # lowest starting market cap seen across its open-market pools
    history_at: float = 0.0                # when token_created_at/start_mcap were last fetched
    market_updated_at: float = 0.0
    price_native: float | None = None
    quote_symbol: str = ""
    price_change: dict[str, float] = field(default_factory=dict)   # m5/h1/h6/h24 %, from DexScreener

    # unique buyer/seller counts per window (m5, m15, m30, h1), from GeckoTerminal
    buyers: dict[str, int] | None = None
    sellers: dict[str, int] | None = None

    buyers_updated_at: float = 0.0  # reception time, not provider event time

    # where did it start, how high has it been
    launch_mcap_sol: float | None = None   # pump.fun create event
    first_price: float | None = None
    peak_price: float | None = None
    stage: StageResult | None = None
    momentum: float = 0.0                  # score before the stage discount

    # live trades from PumpPortal: item = (wallet, side, sol_amount)
    trades: RollingEvents = field(default_factory=lambda: RollingEvents(3600))
    smart_buys: set[str] = field(default_factory=set)

    safety: Safety | None = None
    score: float = 0.0
    features: dict[str, float] = field(default_factory=dict)
    last_alert: float = 0.0
    # "wait before alerting": when it first qualified, and the price/liquidity at that moment
    candidate_at: float = 0.0
    candidate_price: float | None = None
    candidate_liq: float | None = None
    candidate_cancelled_at: float = 0.0
    confirm_note: str = ""

    @property
    def key(self) -> tuple[str, str]:
        return (self.chain, self.address)

    def reference_price(self) -> float | None:
        """Best guess of the price at launch, to measure how far it has already run."""
        refs = []
        if self.first_price:
            refs.append(self.first_price)
        if self.price_usd:
            # DexScreener % change: for coins under 24h old this is the change since launch;
            # for older coins it is "how much has it already ripped today"
            for w in ("h6", "h24"):
                pc = self.price_change.get(w)
                if pc and pc > -100:
                    refs.append(self.price_usd / (1 + pc / 100))
            if self.start_mcap and self.market_cap:
                # a coin that ran on an older pool, then opened a new main pool (e.g. moved to Raydium)
                refs.append(self.price_usd * self.start_mcap / self.market_cap)
            if self.launch_mcap_sol and self.market_cap and self.price_native and self.quote_symbol == "SOL":
                sol_usd = self.price_usd / self.price_native
                refs.append(self.price_usd * self.launch_mcap_sol * sol_usd / self.market_cap)
        refs = [r for r in refs if r and r > 0]
        return min(refs) if refs else None

    @property
    def age_minutes(self) -> float | None:
        """Age of the coin itself (its oldest pool), not of whichever pool is biggest now."""
        born = self.token_created_at or self.pair_created_at
        return (time.time() - born) / 60 if born else None

    @property
    def pool_age_minutes(self) -> float | None:
        return (time.time() - self.pair_created_at) / 60 if self.pair_created_at else None

