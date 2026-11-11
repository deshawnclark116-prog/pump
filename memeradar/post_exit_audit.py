"""Read-only audit of micro-paper prices observed after the actual exits.

The observed DB cannot be retroactively filled in: missing quotes are UNKNOWN.
No original trading rule changes. A present-day DexScreener spot check is
separate from historical quote coverage, never claimed as a historical peak.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import time
from typing import Optional

from .micro_paper import proceeds, SOCIAL, SOCIAL_GUARDED
from .attention.chain import best_pair

HORIZONS = (300, 900, 1800, 3600, 7200)
MAX_LAG = 180  # 3-minute maximum lag after the fixed horizon
PER_REASON_SAMPLE = 4


def _finite(value):
    try:
        num = float(value)
        return num if math.isfinite(num) and num > 0 else None
    except (TypeError, ValueError, OverflowError):
        return None


def _utc(ts):
    return datetime.fromtimestamp(float(ts), timezone.utc).isoformat()


def _quote_rows(db, mint, model, exit_ts, end_ts):
    if model in (SOCIAL, SOCIAL_GUARDED):
        rows = db.execute(
            "SELECT observed_ts,price_usd price,liquidity_usd liquidity,pair "
            "FROM attention_chain WHERE mint=? AND observed_ts>? "
            "AND observed_ts<=? ORDER BY observed_ts",
            (mint, exit_ts, end_ts)).fetchall()
    else:
        rows = db.execute(
            "SELECT observed_ts,price,liquidity,NULL pair "
            "FROM osece_quotes WHERE mint=? AND observed_ts>? "
            "AND observed_ts<=? ORDER BY observed_ts",
            (mint, exit_ts, end_ts)).fetchall()
    return [dict(r) for r in rows if _finite(r["price"]) is not None]


def audit_trade(trade, quotes):
    """Only timestamps AFTER the exit qualify for retrospective observation."""
    exit_ts = float(trade["exit_ts"])
    entry = _finite(trade["entry_price"])
    at_exit = _finite(trade["exit_price"])
    size = float(trade["stake"])
    if entry is None or at_exit is None or size <= 0:
        raise ValueError("invalid_original_paper_trade")
    out = {
        "model":trade["model"], "stake":size, "mint":trade["mint"],
        "entry_utc":_utc(trade["entry_ts"]), "exit_utc":_utc(exit_ts),
        "exit_reason":trade["exit_reason"], "entry_price":entry,
        "exit_price":at_exit, "actual_net_usd":round(float(trade["usd_out"])-size, 6),
        "actual_hold_minutes":round((exit_ts-float(trade["entry_ts"]))/60, 2),
        "quotes_after_exit":0, "earliest_post_exit_quote_utc":None,
        "latest_post_exit_quote_utc":None, "fixed_horizon":{},
        "peak_post_exit_pct_at_2h":None, "trough_post_exit_pct_at_2h":None,
        "observability":"no_post_exit_quotes_recorded"
    }
    valid = [
        q for q in quotes
        if exit_ts < float(q["observed_ts"]) <= exit_ts+HORIZONS[-1]+MAX_LAG
        and _finite(q["price"]) is not None
    ]
    valid.sort(key=lambda q:q["observed_ts"])
    out["quotes_after_exit"] = len(valid)
    if valid:
        out["earliest_post_exit_quote_utc"] = _utc(valid[0]["observed_ts"])
        out["latest_post_exit_quote_utc"] = _utc(valid[-1]["observed_ts"])
        out["observability"] = "partial_or_full_historical_price_coverage"
    first_2h = [q for q in valid if q["observed_ts"] <= exit_ts + HORIZONS[-1]]
    if first_2h:
        out["peak_post_exit_pct_at_2h"] = round(
            100*(max(q["price"] for q in first_2h)/at_exit-1), 2)
        out["trough_post_exit_pct_at_2h"] = round(
            100*(min(q["price"] for q in first_2h)/at_exit-1), 2)

    for h in HORIZONS:
        # Fixed-horizon exit at FIRST post-target quote, not best available price.
        candidate = next(
            (q for q in valid if exit_ts+h <= q["observed_ts"]
             <= exit_ts+h+MAX_LAG), None)
        key = str(h//60) + "m"
        if candidate is None:
            out["fixed_horizon"][key] = {
                "status":"unobserved", "quote":None,
                "additional_net_usd":None}
        else:
            counterfactual = proceeds(entry, candidate["price"], size)-size
            actual = float(trade["usd_out"])-size
            out["fixed_horizon"][key] = {
                "status":"observed",
                "observed_at_utc":_utc(candidate["observed_ts"]),
                "quote":candidate["price"],
                "price_change_from_exit_pct":round(
                    100*(candidate["price"]/at_exit-1),2),
                "hypothetical_total_net_usd":round(counterfactual,6),
                "additional_net_usd":round(counterfactual-actual,6),
                "liquidity_usd":candidate.get("liquidity"),
                "note":"counterfactual_quote_not_executable_swap"
            }
    return out


def analyze(db, *, limit=400, sample_per_reason=PER_REASON_SAMPLE,
            now=None):
    """Audit all latest closed paper trades; then select reason-stratified examples."""
    now = time.time() if now is None else now
    table = db.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='micro_intents'"
    ).fetchone()
    if table is None:
        return {"status":"missing_paper_ledger","trades":[],"samples":[]}
    trades = [dict(r) for r in db.execute(
        "SELECT model,stake,mint,entry_ts,entry_price,exit_ts,exit_price,usd_out,"
        "exit_reason FROM micro_intents "
        "WHERE status='closed' AND entry_ts IS NOT NULL AND "
        "exit_ts IS NOT NULL AND exit_price>0 AND usd_out IS NOT NULL "
        "ORDER BY exit_ts DESC LIMIT ?", (limit,)).fetchall()]
    audited = []
    for trade in trades:
        quotes = _quote_rows(
            db, trade["mint"], trade["model"], trade["exit_ts"],
            min(now, trade["exit_ts"]+HORIZONS[-1]+MAX_LAG))
        audited.append(audit_trade(trade, quotes))

    by_reason = defaultdict(list)
    for t in audited:
        by_reason[t["exit_reason"]].append(t)
    reasons = {}
    for reason, rows in sorted(by_reason.items()):
        horizons = {}
        for h in HORIZONS:
            key = str(h//60)+"m"
            observed = [t["fixed_horizon"][key] for t in rows
                        if t["fixed_horizon"][key]["status"]=="observed"]
            horizons[key] = {
                "observed_count":len(observed),
                "total_exits":len(rows),
                "better_than_actual_count":sum(
                    o["additional_net_usd"]>0 for o in observed),
                "avg_additional_usd_observed_only":(
                    round(sum(o["additional_net_usd"] for o in observed)/len(observed),4)
                    if observed else None)}
        reasons[reason] = {
            "count":len(rows),
            "any_post_exit_quotes":sum(t["quotes_after_exit"]>0 for t in rows),
            "fixed_horizons":horizons}

    # Sample reasons independently, including cases with no follow-on price,
    # rather than cherry-picking only impressive rallies.
    samples = []
    sample_seen = set()
    for reason, rows in sorted(by_reason.items()):
        added = 0
        for row in rows:
            key = (row["mint"],row["exit_utc"],reason)
            if key in sample_seen:
                continue
            samples.append(row)
            sample_seen.add(key)
            added += 1
            if added >= sample_per_reason:
                break
    return {
        "status":"complete_with_missing_quotes_explicitly_unknown",
        "asof_utc":_utc(now),
        "total_audited_exits":len(audited),
        "unique_coin_count":len(set(t["mint"] for t in audited)),
        "exits_with_any_later_observed_price":sum(
            t["quotes_after_exit"]>0 for t in audited),
        "exits_without_later_observed_price":sum(
            t["quotes_after_exit"]==0 for t in audited),
        "by_exit_reason":reasons,
        "samples":samples, "trades":audited,
        "method":"Stored post-exit receipts ONLY, quote latency capped at 3 min. "
                 "Same initial paper entry, actual exit versus later close quote. "
                 "No price interpolation, no assumed liquidity/executability, "
                 "no inference from missing observations.",
    }


def markdown(report):
    if report["status"]=="missing_paper_ledger":
        return "# Post-exit investigation\n\nNo usable micro paper ledger.\n"
    lines = [
        "# Did the meme coins keep climbing after we sold?",
        "",
        "**READ-ONLY, actual paper exit ledger. No new trades or rule changes.**",
        "Analyzed at: " + report["asof_utc"], "",
        f"Closed exits: {report['total_audited_exits']} across "
        f"{report['unique_coin_count']} distinct mints.",
        f"With any post-exit recorded quote: {report['exits_with_any_later_observed_price']}.",
        f"**Without any recorded post-exit quote: "
        f"{report['exits_without_later_observed_price']} (UNKNOWN, not zero return).**",
        "",
        "## Coverage and alternative 5/15/30/60/120 minute holds",
        "",
        "| Exit reason | Exits | 30m observed | 30m later better | "
        "60m observed | 60m later better |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for reason, r in report["by_exit_reason"].items():
        h30 = r["fixed_horizons"]["30m"]
        h60 = r["fixed_horizons"]["60m"]
        lines.append(
            f"| {reason} | {r['count']} | {h30['observed_count']} | "
            f"{h30['better_than_actual_count']} | {h60['observed_count']} | "
            f"{h60['better_than_actual_count']} |")
    lines += ["", "## Reason-stratified coin case studies", "",
              "| Model | Mint | Exit reason | Actual net | "
              "5m later change | 30m later change | 60m later change |",
              "|---|---|---|---:|---:|---:|---:|"]
    for t in report["samples"]:
        def v(k):
            obj=t["fixed_horizon"][k]
            return (f"{obj['price_change_from_exit_pct']:+.1f}%"
                    if obj["status"]=="observed" else "unknown")
        lines.append(
            f"| {t['model']} (\${t['stake']:.0f}) | "
            f"[{t['mint'][:12]}…](https://dexscreener.com/solana/{t['mint']}) | "
            f"{t['exit_reason']} | \${t['actual_net_usd']:+.3f} | "
            f"{v('5m')} | {v('30m')} | {v('60m')} |")
    lines += [
        "",
        "### Interpretation",
        "An observed price is NOT a tradeable swap; future candle peaks are",
        "not achievable exits unless a prospective rule could actually identify them.",
        "If most cells say unknown, the existing radar did NOT retain sufficient",
        "after-sale tracking. Do not infer time-limit exits were correct.",
        "",
        "No automatic extension of live hold periods; requires a clean forward",
        "experiment with continuous tracking, same entries, same fees and",
        "same max concurrent exposure.",
    ]
    return "\n".join(lines)+"\n"


def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument("--db",default="memeradar.db")
    p.add_argument("--output-dir",default="post_exit_output")
    p.add_argument("--limit",type=int,default=400)
    args=p.parse_args(argv)
    db=sqlite3.connect(f"file:{Path(args.db).resolve()}?mode=ro",uri=True)
    db.row_factory=sqlite3.Row
    report=analyze(db,limit=max(1,min(5000,args.limit)))
    folder=Path(args.output_dir)
    folder.mkdir(parents=True,exist_ok=True)
    (folder/"post_exit.json").write_text(json.dumps(report,indent=2)+"\n")
    (folder/"post_exit.md").write_text(markdown(report))
    print(markdown(report))
    return 0

if __name__=="__main__":
    raise SystemExit(main())
