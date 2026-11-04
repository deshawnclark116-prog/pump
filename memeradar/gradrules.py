"""Settings for the graduation play (see grad.py). Kept separate to avoid import cycles."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GradRules:
    enabled: bool = True
    graduation_sol: float = 410.0     # market cap (SOL) where a pump.fun coin graduates
    buy_min_sol: float = 70.0         # ~17% of the way to graduation: get in early...
    buy_max_sol: float = 160.0        # ...up to ~40%
    min_mcap_usd: float = 10_000      # ...but never under $10k market cap (those lost with every rule)
    sell_at_sol: float = 370.0        # ~90%: sell before the graduation dump
    max_age_minutes: float = 60.0
    min_buys_m5: int = 25
    min_buy_sell_ratio: float = 1.2   # more buys than sells...
    max_buy_sell_ratio: float = 8.0   # ...but not one-sided (fake hype)
    min_usd_per_buy: float = 0.0      # >0 skips coins whose 5-min trades average under this (off)
    stop_loss_pct: float = 25.0       # YOUR money, after fees
    max_hold_minutes: float = 30.0    # more room, since it starts further from graduation
    track_minutes: float = 30.0       # follow each new coin this long
    dead_after_minutes: float = 8.0   # drop coins still under `dead_below_sol` after this
    dead_below_sol: float = 45.0
    max_tracked: int = 450            # ~25 min of launches; 15 DexScreener calls per poll
    poll_seconds: float = 15.0
