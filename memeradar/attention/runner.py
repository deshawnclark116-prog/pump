"""Run the attention collectors inside the existing radar process/SQLite DB."""
from __future__ import annotations

import asyncio
import logging
import time

from . import db as evidence
from . import attention, chain, trader, public_sources, explore, stress_survivor
from .. import alerts

log=logging.getLogger(__name__)


async def interval(seconds,fn):
    while True:
        try:
            await fn()
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("attention collector failed")
        await asyncio.sleep(seconds)




async def notify_experimental_trades(http, db, cfg):
    """Send only newly recorded BUY/SELL receipts; persist dedupe across restarts."""
    if cfg is None:
        return 0
    if not (cfg.ntfy_topic or cfg.discord_webhook_url or
            (cfg.telegram_bot_token and cfg.telegram_chat_id)):
        return 0
    explore.ensure(db)
    rows = db.execute("""
        SELECT event.* FROM (
            SELECT id AS trade_id,variant,mint,source,'BUY' AS action,
                   entry_ts AS event_ts,entry_price AS price,usd_in,
                   NULL AS usd_out,NULL AS reason
            FROM attention_explore_trades
            UNION ALL
            SELECT id,variant,mint,source,'SELL',exit_ts,exit_price,
                   usd_in,usd_out,reason
            FROM attention_explore_trades WHERE exit_ts IS NOT NULL
        ) AS event WHERE NOT EXISTS (
            SELECT 1 FROM attention_explore_notifications n
            WHERE n.trade_id=event.trade_id AND n.action=event.action
        ) ORDER BY event.event_ts,event.trade_id LIMIT 40
    """).fetchall()
    delivered = 0
    for row in rows:
        label = "SOCIAL" if row["variant"] == explore.VERSIONS[0] else "GECKO TREND"
        mint = row["mint"]
        symbol = ""
        try:
            match = db.execute(
                "SELECT symbol FROM tokens WHERE chain='solana' AND address=?",
                (mint,)).fetchone()
            symbol = match[0] if match and match[0] else ""
        except Exception:
            pass
        name = "$"+symbol if symbol else mint[:9]+"..."
        if row["action"] == "BUY":
            message = (f"🧪 ATTENTION PAPER BUY | {label} | {name}\n"
                       f"$10 fake money at ${row['price']:.10g} | source {row['source']}\n"
                       f"Simulated 20s+ delayed entry; no live swap.\n{mint}")
        else:
            pnl = row["usd_out"]-row["usd_in"]
            message = (f"🧪 ATTENTION PAPER SELL | {label} | {name}\n"
                       f"Net P&L {pnl:+.2f} USD | {row['reason']}\n"
                       f"Source {row['source']} | price ${row['price']:.10g}\n{mint}")
        try:
            await alerts.send_text(
                http, cfg, message, symbol=symbol or "?",
                address=mint, link=alerts.chart_link("solana",mint),
                kind="ATTENTION PAPER")
        except Exception:
            log.exception("attention paper notification failed for trade %s",row["trade_id"])
            continue  # keep this receipt eligible for a future retry
        db.execute(
            "INSERT OR IGNORE INTO attention_explore_notifications "
            "(trade_id,action,notified_ts) VALUES (?,?,?)",
            (row["trade_id"],row["action"],time.time()))
        db.commit()
        delivered += 1
    return delivered

async def run_collectors(http,db,cfg=None):
    evidence.ensure(db)

    async def social():
        await attention.poll_social(http,db)

    async def public():
        await public_sources.poll_public(http, db)

    async def market():
        result=await chain.poll_chain(http,db)
        if result["tracked"]:
            log.info("attention chain: %s",result)
        outcome=trader.paper_tick(db)
        if outcome["status"]=="forward_paper_only" and (outcome["entries"] or outcome["exits"]):
            log.info("attention paper: %s",outcome)
        exploratory=explore.paper_tick(db)
        if exploratory.get("entries") or exploratory.get("exits") or exploratory.get("queued"):
            log.info("ATTENTION EXPLORE PAPER: %s",exploratory)
        stress=stress_survivor.paper_tick(db)
        if any(stress.get(k) for k in ("watched","confirmed","entered","closed")):
            log.info("STRESS SURVIVOR PAPER: %s",stress)
        await notify_experimental_trades(http,db,cfg)

    async def security():
        result=await chain.poll_safety(http,db)
        if result["safety_checked"]:
            log.info("attention RugCheck: %s",result)

    async def maintenance():
        # Collect 2-4 weeks of full-resolution evidence, then bound cache growth.
        cutoff=time.time()-45*86400
        db.execute("DELETE FROM attention_chain WHERE observed_ts < ?",(cutoff,))
        db.execute("DELETE FROM attention_hits WHERE observed_ts < ?",(cutoff,))
        db.execute("DELETE FROM attention_trends WHERE observed_ts < ?",(cutoff,))
        db.execute("DELETE FROM attention_safety_history WHERE checked_ts < ?",(cutoff,))
        db.commit()

    tasks=[interval(60,social),interval(600,public),interval(30,market),interval(60,security),
           interval(21600,maintenance),attention.listen_telegram(db)]
    await asyncio.gather(*tasks)
