"""Stage detector: is this move just starting, or are we buying someone else's exit?

Uses only the coin's own data (price history + unique buyer/seller counts) – no outside wallets.

  EARLY    hasn't run far yet, new buyers arriving faster than before, buyers > sellers
  RUNNING  moving, still being bought, but already up a fair amount
  LATE     already up a lot, or new buyers are drying up while price is high
  DUMPING  well off its peak, or sellers clearly outnumber buyers
"""
from __future__ import annotations

from dataclasses import dataclass

EARLY, RUNNING, LATE, DUMPING, UNKNOWN = "EARLY", "RUNNING", "LATE", "DUMPING", "UNKNOWN"
# how much of the momentum score survives at each stage
FRESHNESS = {EARLY: 1.0, RUNNING: 0.75, UNKNOWN: 0.6, LATE: 0.3, DUMPING: 0.0}


@dataclass
class StageRules:
    early_max_runup: float = 3.0       # EARLY only if price is < 3x where we/launch first saw it
    late_runup: float = 8.0            # >= 8x already -> LATE
    dump_drawdown: float = 0.35        # >= 35% below the peak we saw -> DUMPING
    late_drawdown: float = 0.20        # >= 20% below peak -> at best LATE
    early_max_age_minutes: float = 240  # older coins can be RUNNING (a second leg) but never EARLY
    min_buyers_m5: int = 10            # need real participation to call anything EARLY
    accel_ratio: float = 1.3           # buyers in last 5m vs avg 5m before that: >1.3 = speeding up
    fade_ratio: float = 0.6            # <0.6 = drying up
    sell_pressure: float = 1.3         # sellers > 1.3x buyers in last 5m -> DUMPING


@dataclass
class StageResult:
    stage: str
    runup: float | None          # current price / reference price (launch or first seen)
    drawdown: float              # 0..1 below observed peak
    buyer_accel: float | None    # buyers last 5m / average 5m over the 10m before
    reasons: list[str]

    @property
    def freshness(self) -> float:
        return FRESHNESS[self.stage]


def classify(*, price: float | None, ref_price: float | None, peak_price: float | None,
             buyers: dict[str, int] | None, sellers: dict[str, int] | None,
             age_minutes: float | None = None, rules: StageRules = StageRules()) -> StageResult:
    reasons: list[str] = []
    runup = price / ref_price if price and ref_price else None
    drawdown = 1 - price / peak_price if price and peak_price and peak_price > 0 else 0.0
    drawdown = max(0.0, drawdown)

    b5 = (buyers or {}).get("m5")
    b15 = (buyers or {}).get("m15")
    s5 = (sellers or {}).get("m5")
    accel = None
    if b5 is not None and b15 is not None:
        prior_avg = max(b15 - b5, 0) / 2          # avg buyers per 5m over the previous 10m
        accel = b5 / prior_avg if prior_avg > 0 else (2.0 if b5 >= rules.min_buyers_m5 else 1.0)

    # --- worst stages first
    if drawdown >= rules.dump_drawdown:
        reasons.append(f"{drawdown:.0%} below its peak")
        return StageResult(DUMPING, runup, drawdown, accel, reasons)
    if b5 is not None and s5 is not None and s5 > rules.sell_pressure * max(b5, 1) and s5 >= 5:
        reasons.append(f"sellers {s5} vs buyers {b5} in last 5m")
        return StageResult(DUMPING, runup, drawdown, accel, reasons)

    late = []
    if runup is not None and runup >= rules.late_runup:
        late.append(f"already {runup:.1f}x")
    if drawdown >= rules.late_drawdown:
        late.append(f"{drawdown:.0%} off peak")
    if accel is not None and accel < rules.fade_ratio and (runup or 1) >= 2:
        late.append("new buyers drying up")
    if late:
        return StageResult(LATE, runup, drawdown, accel, late)

    if b5 is None:
        return StageResult(UNKNOWN, runup, drawdown, accel, ["no buyer data yet"])

    young = age_minutes is not None and age_minutes <= rules.early_max_age_minutes
    if (young and b5 >= rules.min_buyers_m5 and (accel or 0) >= rules.accel_ratio
            and (runup is None or runup < rules.early_max_runup) and b5 >= (s5 or 0)):
        reasons.append(f"{b5} buyers in 5m, {accel:.1f}x faster than before")
        if runup is not None:
            reasons.append(f"only {runup:.1f}x so far")
        return StageResult(EARLY, runup, drawdown, accel, reasons)

    if runup is not None:
        reasons.append(f"up {runup:.1f}x")
    if accel is not None:
        reasons.append(f"buyer pace {accel:.1f}x")
    return StageResult(RUNNING, runup, drawdown, accel, reasons)
