"""10-minute GitHub Issue dashboard for the *actual* running $25 paper ledger.

No source control pushes, repeated artifacts, or reliance on unavailable
in-progress GitHub Actions logs. A single Issue #12 comment is updated
in-place by the long-running radar job. No account secrets exposed.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import json
import logging
import os
import urllib.error
import urllib.request

from . import micro_paper

log=logging.getLogger(__name__)
MARKER="<!-- meme-micro-paper-dashboard-v1 -->"


def _money(value):
    return "$"+f"{value:,.2f}"


def markdown(snapshot,run_id="",repo=""):
    now=snapshot["asof_utc"]
    lines=[
        MARKER,
        "## Live $25 paper trading — twelve independent accounts",
        f"**Last observed:** {now} · **Radar run:** "+
        (f"[{run_id}](https://github.com/{repo}/actions/runs/{run_id})" if run_id and repo else "unknown"),
        "",
        "Each model has **two independent $25 paper accounts** ($1 and $5 stake). No real trades.",
        "The figures below reflect actual recorded paper fills; no bankroll top-ups.",
        "",
        "| Strategy | Stake | Cash | Open | Closed (W–L) | Realized net | Estimated equity |",
        "|---|---:|---:|---:|---:|---:|---:|"
    ]
    for r in snapshot["accounts"]:
        label={
            micro_paper.SOCIAL:"Social Attention (control)",
            micro_paper.SOCIAL_GUARDED:"Social Guarded (prospective)",
            micro_paper.OSECE_SINGLE:"OSECE Early Signal",
            micro_paper.OSECE_COLLISION:"OSECE Collision",
            micro_paper.OSECE_LIQ:"OSECE Early Liquidity",
            micro_paper.OSECE_COLLISION_LIQ:"OSECE Collision Early (prospective)",
        }[r["model"]]
        eq=_money(r["estimated_equity_usd"]) if r["estimated_equity_usd"] is not None else "Unpriced position"
        lines.append(
            f"| {label} | {_money(r['stake_usd'])} | {_money(r['cash_usd'])} | "
            f"{r['open']} | {r['closed']} ({r['wins']}–{r['losses']}) | "
            f"{_money(r['net_realized_pnl_usd'])} | {eq} |"
        )
    recent=[]
    for a in snapshot["accounts"]:
        for t in a["trades"]:
            recent.append((t["entry_ts"] or 0,a["model"],a["stake_usd"],t))
    recent.sort(reverse=True,key=lambda x:x[0])
    lines+=["","### Latest recorded micro trades",
            "| Model | Stake | Contract | Entry UTC | Exit result |",
            "|---|---:|---|---|---|"]
    if not recent:
        lines.append("| No qualifying trades yet | — | — | — | — |")
    for ts,model,stake,t in recent[:20]:
        when=datetime.fromtimestamp(ts,timezone.utc).strftime("%m-%d %H:%M") if ts else "—"
        result=("OPEN" if t["status"]=="open" else
                f"{_money(t['usd_out']-stake)} ({t['exit_reason']})")
        lines.append(
            f"| {model} | {_money(stake)} | `{t['mint'][:12]}…` | "
            f"{when} | {result} |")
    lines+=["","---",
            "### Modeling and integrity",
            "Fees: 1.25% + 1% slippage **each side**, plus **estimated $0.02 each side** fixed overhead. "
            "The fixed overhead is an assumption, not verified on-chain.",
            "No signer, no swaps, no actual executions. Unpriced open positions have **no estimated equity**, "
            "not a fictitious zero-value exit.",
            "Signal discovery is independent by model. This is an *unvalidated forward paper experiment*.",
            "A stale timestamp means the GitHub runner/report is not updating; don't assume the strategy is inactive.",
            f"**Experiment activated:** {snapshot['activated_utc'] or 'not yet active'}"]
    return "\n".join(lines)


def _request(url,token,method="GET",data=None):
    req=urllib.request.Request(
        url,
        data=json.dumps(data).encode("utf-8") if data is not None else None,
        method=method,
        headers={"Authorization":f"Bearer {token}",
                 "Accept":"application/vnd.github+json",
                 "X-GitHub-Api-Version":"2022-11-28",
                 "User-Agent":"meme-radar-micro-paper-dashboard",
                 "Content-Type":"application/json"})
    with urllib.request.urlopen(req,timeout=15) as response:
        content=response.read(1500000)
    return json.loads(content) if content else {}


def publish(text,*,repo=None,issue=12,token=None):
    repo=repo or os.environ.get("GITHUB_REPOSITORY","")
    token=token or os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if not repo or not token or "/" not in repo:
        return {"status":"unavailable","reason":"GitHub token or repo missing"}
    url=f"https://api.github.com/repos/{repo}/issues/{issue}/comments"
    try:
        comments=_request(url+"?per_page=100",token)
        ours=next((c for c in comments if MARKER in c.get("body","")),None)
        if ours:
            _request(ours["url"],token,"PATCH",{"body":text})
            return {"status":"updated","comment_id":ours["id"]}
        c=_request(url,token,"POST",{"body":text})
        return {"status":"created","comment_id":c["id"]}
    except (urllib.error.HTTPError,urllib.error.URLError,TimeoutError,
            ValueError,KeyError) as ex:
        log.warning("Micro dashboard upload failed (%s), trading continues",
                    type(ex).__name__)
        return {"status":"error","reason":type(ex).__name__}


async def publish_async(db):
    # SQL reads must stay on the event-loop thread: sqlite3 check_same_thread.
    snapshot=micro_paper.report(db)
    payload=markdown(snapshot,
                     os.environ.get("GITHUB_RUN_ID",""),
                     os.environ.get("GITHUB_REPOSITORY",""))
    return await asyncio.to_thread(publish,payload)
