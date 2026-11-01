"""Phase 4: replay stored snapshots, measure forward returns, and tune W1..W5.

Uses only data the radar collected itself, so it improves the longer you run it.
"""
from __future__ import annotations

import json
import random
from collections import defaultdict
from statistics import median

from .scoring import composite

FEATURES = ("social_velocity", "volume_acceleration", "wallet_diversity", "liquidity_depth", "smart_money")


def build_rows(snapshots: list[dict], horizon_s: float = 3600) -> list[dict]:
    """Attach max forward return within `horizon_s` to every snapshot that has later prices."""
    by_token: dict[tuple, list[dict]] = defaultdict(list)
    for s in snapshots:
        by_token[(s["chain"], s["address"])].append(s)
    rows = []
    for key, snaps in by_token.items():
        snaps.sort(key=lambda s: s["ts"])
        for i, s in enumerate(snaps):
            later = [x["price_usd"] for x in snaps[i + 1:] if x["ts"] - s["ts"] <= horizon_s and x["price_usd"]]
            if not later or not s["price_usd"]:
                continue
            rows.append({"token": key, "ts": s["ts"],
                         "features": json.loads(s["features"]) if isinstance(s["features"], str) else s["features"],
                         "fwd_max": max(later) / s["price_usd"] - 1})
    return rows


def evaluate(rows: list[dict], weights: dict[str, float], threshold: float, win: float = 0.5) -> dict:
    """First time each token crosses `threshold` = one simulated alert."""
    fired: dict[tuple, dict] = {}
    for r in sorted(rows, key=lambda r: r["ts"]):
        if r["token"] not in fired and composite(r["features"], weights) >= threshold:
            fired[r["token"]] = r
    rets = [r["fwd_max"] for r in fired.values()]
    return {"alerts": len(rets),
            "hit_rate": sum(x >= win for x in rets) / len(rets) if rets else 0.0,
            "median_fwd_max": median(rets) if rets else 0.0}


def tune(rows: list[dict], threshold: float, trials: int = 2000, min_alerts: int = 5,
         seed: int = 0) -> tuple[dict[str, float], dict]:
    rng = random.Random(seed)
    best_w, best_m, best_obj = None, None, -1.0
    for _ in range(trials):
        raw = [rng.random() for _ in FEATURES]
        w = {k: round(v / sum(raw), 3) for k, v in zip(FEATURES, raw)}
        m = evaluate(rows, w, threshold)
        if m["alerts"] < min_alerts:
            continue
        obj = m["hit_rate"] * (m["alerts"] ** 0.5)  # reward precision, but not on 1-2 lucky alerts
        if obj > best_obj:
            best_w, best_m, best_obj = w, m, obj
    return best_w or {}, best_m or {}
