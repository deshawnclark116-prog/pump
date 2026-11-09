"""Non-cherry-picked Social Attention loss analysis; never changes trade rules.

Use only saved actual paper receipts and the signal observation recorded
BEFORE entry. Group by independent mint and entry date. Outputs all closed
trades with modeled-cost net P&L. Thresholds are labeled *shadow* hypotheses:
do not promote them without independent future holdout results.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import math
import sqlite3

from . import explore


def _stats(rows):
    closed=[x for x in rows if x["pnl"] is not None]
    winners=[x for x in closed if x["pnl"]>0]
    losers=[x for x in closed if x["pnl"]<=0]
    return {
        "closed":len(closed),"wins":len(winners),"losses":len(losers),
        "win_rate_pct":round(100*len(winners)/len(closed),2) if closed else None,
        "net_pnl":round(sum(x["pnl"] for x in closed),2),
        "gross_winner_pnl":round(sum(x["pnl"] for x in winners),2),
        "gross_loser_pnl":round(sum(x["pnl"] for x in losers),2),
        "open":len(rows)-len(closed),
    }


def analyze(db,limit=2000):
    explore.ensure(db)
    rows=db.execute(
        "SELECT t.*,s.signal_price,s.signal_ts,s.source_ts,"
        "s.signal_liquidity,s.buyers_m5,s.sellers_m5,"
        "s.buy_proxy_m5,s.source_rank "
        "FROM attention_explore_trades t "
        "JOIN attention_explore_signals s ON s.id=t.signal_id "
        "WHERE t.variant=? ORDER BY t.entry_ts",
        (explore.VERSIONS[0],)).fetchall()
    trades=[]
    for r in rows:
        pnl=r["usd_out"]-r["usd_in"] if r["exit_ts"] is not None and r["usd_out"] is not None else None
        change=(r["entry_price"]/r["signal_price"]-1)*100 if r["signal_price"] else None
        buyers=r["buyers_m5"]
        sells=r["sellers_m5"]
        trades.append({
            "mint":r["mint"],"source":r["source"],
            "entry_utc":datetime.fromtimestamp(r["entry_ts"],timezone.utc).isoformat(),
            "entry_ts":r["entry_ts"],
            "date_utc":datetime.fromtimestamp(r["entry_ts"],timezone.utc).strftime("%Y-%m-%d"),
            "pnl":round(pnl,5) if pnl is not None else None,
            "reason":r["reason"],
            "net_return_pct":round(100*pnl/r["usd_in"],2) if pnl is not None else None,
            "signal_liquidity_usd":r["signal_liquidity"],
            "buyer_tx_count":buyers,"seller_tx_count":sells,
            "buy_sell_count_ratio":round(buyers/sells,3) if sells else None,
            "buy_volume_proxy_m5":r["buy_proxy_m5"],
            "entry_vs_signal_pct":round(change,3) if change is not None else None,
            "source_to_signal_s":round(r["signal_ts"]-r["source_ts"],2),
            "hold_s":round(r["exit_ts"]-r["entry_ts"],2) if r["exit_ts"] else None,
        })
    totals=_stats(trades)
    grouped=defaultdict(list)
    for t in trades:
        grouped[t["date_utc"]].append(t)
    daily={day:_stats(v) for day,v in sorted(grouped.items())}
    closed=[t for t in trades if t["pnl"] is not None]
    losers=[t for t in closed if t["pnl"]<=0]
    high_chase=[t for t in trades if t["entry_vs_signal_pct"] is not None
                and t["entry_vs_signal_pct"]>5]
    low_chase=[t for t in trades if t["entry_vs_signal_pct"] is not None
               and t["entry_vs_signal_pct"]<=5]
    repeat=defaultdict(list)
    for t in trades:
        repeat[t["mint"]].append(t)
    repeat_mints={
        mint:{"entries":len(t),"net_pnl":round(sum(x["pnl"] or 0 for x in t),2),
              "losing_entries":sum(x["pnl"] is not None and x["pnl"]<=0 for x in t)}
        for mint,t in repeat.items() if len(t)>1}
    severe=[t for t in losers if t["net_return_pct"] is not None
            and t["net_return_pct"]<=-40]
    reasons=dict(Counter(t["reason"] for t in closed))
    return {
        "strategy":"attention_social_probe_v0","status":"retrospective_diagnostic_not_validation",
        "total":totals,"daily":daily,"exit_reasons":reasons,
        "slippage_gap": {
            "paper_losses_worse_than_40pct":len(severe),
            "sum_pnl":round(sum(t["pnl"] for t in severe),2),
            "interpretation":"20pct net stop is checked only on later quotes; losses can gap"
        },
        "shadow_hypotheses":{
            "entry_chase_over_5pct":_stats(high_chase),
            "entry_chase_at_most_5pct":_stats(low_chase),
            "warning":"Observed on already-consumed data. Not an optimized live rule or validated causal improvement."
        },
        "repeated_mints":dict(sorted(repeat_mints.items(),
                              key=lambda pair:pair[1]["net_pnl"])),
        "worst_trades":sorted(losers,key=lambda t:t["pnl"])[:30],
        "trades":trades[-limit:],
        "provenance":"source trading ledger + matched, pre-entry signal snapshots",
        "warning":"Separate paper books and modeled fills; do not infer real executable returns"
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--db",default="memeradar.db")
    p.add_argument("--output")
    args=p.parse_args()
    db=sqlite3.connect(args.db);db.row_factory=sqlite3.Row
    out=json.dumps(analyze(db),indent=2,allow_nan=False)
    if args.output:
        with open(args.output,"w") as f:
            f.write(out)
    else:
        print(out)


if __name__=="__main__":
    main()
