"""Offline, chronological research. Never places orders or alters paper balances.

Prices are provider quotes, NOT executable fills. Missing observations leave capital
unresolved; they never turn into a profitable exit or disappear from the denominator.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime
import json
import math
from pathlib import Path
import sqlite3

from .paper import PaperRules, cost_factor


@dataclass(frozen=True)
class Rules:
    version: str = "recovery-v1-research"
    min_liquidity: float = 50_000
    min_mcap: float = 100_000
    min_age_minutes: float = 30
    min_volume: float = 5_000
    dip: float = 8
    max_dip: float = 20
    rebound: float = 4
    recovery_seconds: float = 30
    watch_seconds: float = 900
    delay_seconds: float = 15
    max_gap_seconds: float = 90
    hold_seconds: float = 1800
    take_profit: float = 25
    stop_loss: float = 15
    trade_usd: float = 10
    capital: float = 100
    max_open: int = 5
    fee_pct: float = 1.25
    slippage_pct: float = 2


def finite(x) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


def evidence(row):
    f = row.get("features") or {}
    if isinstance(f, str):
        f = json.loads(f)
    return f, f.get("research_v1", {})


def eligible(row, r: Rules, impulse=False):
    f, e = evidence(row)
    ts = row["ts"]
    # Receipt freshness does not establish provider event freshness; report that limitation.
    for key, limit in (("market_received_at", 30), ("breadth_received_at", 60),
                       ("safety_checked_at", 900)):
        value = e.get(key)
        if not finite(value) or value <= 0 or not 0 <= ts - value <= limit:
            return False
    if not e.get("safety_passed") or row.get("chain") != "solana":
        return False
    if not e.get("dex_id") or e["dex_id"] == "pumpfun" or f.get("stage") not in ("EARLY", "RUNNING"):
        return False
    for value, minimum in ((row.get("liquidity_usd"), r.min_liquidity),
                           (row.get("market_cap"), r.min_mcap),
                           (row.get("vol_m5"), r.min_volume),
                           (e.get("age_minutes"), r.min_age_minutes)):
        if not finite(value) or value < minimum:
            return False
    b, s = e.get("buyers"), e.get("sellers")
    if not finite(b) or not finite(s) or b < 30 or s <= 0 or not 1.2 <= b / s <= 3:
        return False
    return not impulse or (finite(e.get("m5_pct")) and 3 <= e["m5_pct"] <= 25)


def replay(rows, mode="recovery", rules=None):
    """One position per token per evaluation window, fixed shared capital.

Momentum comparator uses the SAME universe, costs and exits as recovery. It is
not a reconstruction of the production fast-radar strategy.
"""
    if mode not in ("recovery", "momentum"):
        raise ValueError("unknown mode")
    r = rules or Rules()
    cf = cost_factor(PaperRules(fee_pct=r.fee_pct, slippage_pct=r.slippage_pct))
    cash = r.capital
    watches, pending, held, last = {}, {}, {}, {}
    used, closed = set(), []
    gaps = invalid = legacy = skipped = 0
    ordered = sorted(rows, key=lambda x: (x["ts"], x["chain"], x["address"]))
    for row in ordered:
        key = (row["chain"], row["address"])
        ts, px = row["ts"], row.get("price_usd")
        if not finite(px) or px <= 0:
            invalid += 1
            continue
        if not evidence(row)[1]:
            legacy += 1
        received = evidence(row)[1].get("market_received_at")
        if not finite(received) or received <= 0 or not 0 <= ts - received <= 30:
            watches.pop(key, None)
            pending.pop(key, None)
            if key in held:
                held[key]["unresolved_reason"] = "stale or unknown quote receipt time"
            continue
        prev_ts = last.get(key)
        if prev_ts is not None and ts <= prev_ts:
            continue
        gap = prev_ts is not None and ts - prev_ts > r.max_gap_seconds
        last[key] = ts
        if gap:
            gaps += 1
            watches.pop(key, None)
            pending.pop(key, None)
            if key in held:
                held[key]["unresolved_reason"] = "observation gap"
        if key in held:
            h = held[key]
            if h.get("unresolved_reason"):
                continue
            if h.get("exit_signal"):
                if ts - h["exit_signal"] >= r.delay_seconds:
                    proceeds = r.trade_usd * px / h["entry_price"] * cf
                    cash += proceeds
                    closed.append({**h, "exit_ts": ts, "exit_price": px,
                                   "usd_out": proceeds, "pnl": proceeds - r.trade_usd})
                    del held[key]
                continue
            net = (px / h["entry_price"] * cf - 1) * 100
            reason = ("stop" if net <= -r.stop_loss else "take_profit" if net >= r.take_profit
                      else "time" if ts - h["entry_ts"] >= r.hold_seconds else "")
            if reason:
                h.update(exit_signal=ts, reason=reason)
            continue
        if key in pending:
            signal_ts = pending[key]
            if ts - signal_ts < r.delay_seconds:
                continue
            del pending[key]
            if ts - signal_ts > r.max_gap_seconds or not eligible(row, r):
                continue
            if len(held) >= r.max_open or cash < r.trade_usd:
                skipped += 1
                continue
            cash -= r.trade_usd
            held[key] = {"chain": key[0], "address": key[1], "signal_ts": signal_ts,
                         "entry_ts": ts, "entry_price": px, "usd_in": r.trade_usd}
            used.add(key)
            continue
        if key in used:
            continue
        if not eligible(row, r):
            watches.pop(key, None)
            continue
        if key not in watches:
            if eligible(row, r, impulse=True):
                if mode == "momentum":
                    pending[key] = ts
                else:
                    watches[key] = dict(start=ts, high=px, low=px, prev=px,
                                        dip_ts=None, rises=0)
            continue
        w = watches[key]
        if ts - w["start"] > r.watch_seconds:
            del watches[key]
            continue
        if w["dip_ts"] is None:
            w["high"] = max(w["high"], px)
        off = (1 - px / w["high"]) * 100
        if off > r.max_dip:
            del watches[key]
            continue
        if w["dip_ts"] is None and off >= r.dip:
            w.update(dip_ts=ts, low=px, rises=0)
        elif w["dip_ts"] is not None:
            if px < w["low"]:
                w.update(low=px, dip_ts=ts, rises=0)
            elif px > w["prev"]:
                w["rises"] += 1
            elif px < w["prev"]:
                w["rises"] = 0
            if (w["rises"] >= 2 and ts - w["dip_ts"] >= r.recovery_seconds
                    and px >= w["low"] * (1 + r.rebound / 100)):
                pending[key] = ts
                del watches[key]
        w["prev"] = px
    for h in held.values():
        h.setdefault("unresolved_reason", "evaluation ended before a confirmed exit")
    pnl = [t["pnl"] for t in closed]
    gains, losses = sum(max(0, x) for x in pnl), -sum(min(0, x) for x in pnl)
    return {"mode": mode, "rules": asdict(r), "closed": len(closed), "wins": sum(x > 0 for x in pnl),
            "realized_pnl": sum(pnl), "profit_factor": gains / losses if losses else None,
            "cash": cash, "unresolved": len(held), "pending_signals": len(pending),
            "all_unresolved_zero_value_pnl": cash - r.capital,
            "observation_gaps": gaps, "invalid_price_rows": invalid,
            "legacy_rows_without_entry_evidence": legacy, "capacity_skips": skipped,
            "trades": closed, "unresolved_positions": list(held.values()),
            "promotion": "NOT VALIDATED: quote simulation; no automatic promotion"}


BOOKS = {"fast": "paper_trades", "grad": "paper_trades_grad", "scalp": "paper_trades_scalp",
         "steady": "paper_trades_steady", "steady_v1": "paper_trades_steady_v1"}


def audit(db):
    """Exact realized accounting from each existing ledger, no current-price requests."""
    tables = {x[0] for x in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    result = {}
    for name, table in BOOKS.items():
        if table not in tables:
            continue
        rows = [dict(x) for x in db.execute(f'SELECT * FROM "{table}" ORDER BY exit_ts, id')]
        closed = [x for x in rows if x["exit_ts"] is not None]
        pnls = [x["usd_out"] - x["usd_in"] for x in closed]
        gains, losses = sum(max(0, x) for x in pnls), -sum(min(0, x) for x in pnls)
        winners, losers = [x for x in pnls if x > 0], [x for x in pnls if x < 0]
        aw = sum(winners) / len(winners) if winners else 0
        al = -sum(losers) / len(losers) if losers else 0
        result[name] = {"closed": len(closed), "open": len(rows) - len(closed),
                        "wins": len(winners), "losses": len(losers),
                        "breakeven_trades": sum(x == 0 for x in pnls),
                        "realized_pnl": sum(pnls), "average_win": aw, "average_loss": al,
                        "profit_factor": gains / losses if losses else None,
                        "break_even_win_rate": al / (aw + al) if aw + al else None,
                        "worst_trade": min(pnls) if pnls else None,
                        "note": "Mixed historical rule versions; not a current-strategy validation."}
    return result


def timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("use a timestamp with timezone, e.g. 2026-11-05T00:00:00Z")
    return parsed.timestamp()


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--db", default="memeradar.db")
    p.add_argument("--audit", action="store_true")
    p.add_argument("--since", type=timestamp)
    p.add_argument("--until", type=timestamp)
    p.add_argument("--output")
    args = p.parse_args()
    if not args.audit and (args.since is None or args.until is None or args.since >= args.until):
        p.error("replay requires --since and --until with an explicit, increasing evaluation window")
    with sqlite3.connect(Path(args.db).resolve().as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        if args.audit:
            out = {"ledger_audit": audit(db)}
        else:
            rows = [dict(x) for x in db.execute(
                "SELECT * FROM snapshots WHERE ts>=? AND ts<? ORDER BY ts,chain,address",
                (args.since, args.until))]
            out = {"window": {"since": args.since, "until": args.until}, "rows": len(rows),
                   "limitations": ["Scored-token universe only; not every launch.",
                                    "Provider event timestamps and executable fills unavailable.",
                                    "Insufficient evidence is not a passed test."],
                   "results": [replay(rows, mode) for mode in ("momentum", "recovery")]}
    body = json.dumps(out, indent=2, allow_nan=False)
    if args.output:
        Path(args.output).write_text(body + "\n")
    else:
        print(body)


if __name__ == "__main__":
    main()
