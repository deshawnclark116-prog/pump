"""Second signal: steady climbers.

The main radar waits for a *burst* of new buyers (fast money). This one looks for coins that are
climbing steadily instead: up a healthy amount over the last hour, still rising in the last few
minutes without spiking, with more buyers than sellers over the whole hour and real money in the
pool. They get their own alert (📈 STEADY) and their own fake-money balance, so the two styles
can be compared.
"""
from __future__ import annotations

from dataclasses import dataclass

from .models import TokenState
from .stage import DUMPING, LATE


@dataclass
class SteadyRules:
    enabled: bool = True
    min_age_minutes: float = 30
    max_age_hours: float = 24
    min_h1_change_pct: float = 15.0      # up at least this much in the last hour...
    max_h1_change_pct: float = 80.0      # ...but not a rocket (that's the fast radar's job / too late)
    min_m5_change_pct: float = -3.0      # still holding up in the last 5 minutes
    max_m5_change_pct: float = 15.0      # not spiking right now (don't buy the top of a spike)
    max_h6_change_pct: float = 300.0     # not already 4x'd today
    min_liquidity_usd: float = 20_000
    min_volume_h1_usd: float = 20_000
    min_buyers_h1: int = 100


def qualifies(t: TokenState, r: SteadyRules) -> str:
    """Return a short human reason if `t` is a steady climber, else ""."""
    if not r.enabled or not t.price_usd:
        return ""
    age = t.age_minutes
    if age is None or age < r.min_age_minutes or age > r.max_age_hours * 60:
        return ""
    if t.stage and t.stage.stage in (LATE, DUMPING):
        return ""
    pc = t.price_change or {}
    h1, m5, h6 = pc.get("h1"), pc.get("m5"), pc.get("h6")
    if h1 is None or m5 is None:
        return ""
    if not (r.min_h1_change_pct <= h1 <= r.max_h1_change_pct):
        return ""
    if not (r.min_m5_change_pct <= m5 <= r.max_m5_change_pct):
        return ""
    if h6 is not None and h6 > r.max_h6_change_pct:
        return ""
    if (t.liquidity_usd or 0) < r.min_liquidity_usd or t.vol_h1 < r.min_volume_h1_usd:
        return ""
    b, s = t.buyers or {}, t.sellers or {}
    if b.get("h1", 0) < r.min_buyers_h1:
        return ""
    for w in ("m30", "h1"):
        if b.get(w, 0) <= s.get(w, 0):
            return ""
    return (f"up {h1:+.0f}% in 1h, {m5:+.0f}% in the last 5 min · "
            f"buyers ahead of sellers all hour ({b.get('h1', 0)} vs {s.get('h1', 0)})")
