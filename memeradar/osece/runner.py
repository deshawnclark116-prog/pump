"""Run OSECE alongside, not inside, the existing trading strategies."""
from __future__ import annotations

import asyncio
import logging
import os
import time

from .. import alerts
from . import core, collectors

log=logging.getLogger(__name__)


async def interval(delay, fn):
    while True:
        try:
            await fn()
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("OSECE %s failed: %s",getattr(fn,"__name__","poll"),type(exc).__name__)
        await asyncio.sleep(delay)


async def notify(http,db,cfg):
    if cfg is None or not (cfg.ntfy_topic or cfg.discord_webhook_url or
            (cfg.telegram_bot_token and cfg.telegram_chat_id)):
        return 0
    core.ensure(db)
    events=db.execute("""
      SELECT * FROM (
        SELECT t.id trade_id,t.variant,t.mint,l.name,l.symbol,l.term_key,
               'BUY' action,t.entry_ts event_ts,t.entry_price price,
               t.usd_in,NULL usd_out,NULL reason
        FROM osece_trades t JOIN osece_launches l ON l.mint=t.mint
        UNION ALL
        SELECT t.id,t.variant,t.mint,l.name,l.symbol,l.term_key,
               'SELL',t.exit_ts,t.exit_price,t.usd_in,t.usd_out,t.exit_reason
        FROM osece_trades t JOIN osece_launches l ON l.mint=t.mint
        WHERE t.exit_ts IS NOT NULL
      ) e WHERE NOT EXISTS (
         SELECT 1 FROM osece_notifications n
         WHERE n.trade_id=e.trade_id AND n.action=e.action
      ) ORDER BY e.event_ts,e.trade_id LIMIT 30
    """).fetchall()
    count=0
    for e in events:
        kind=("COLLISION" if e["variant"]==core.COLLISION else
              "CROSS-SOURCE EARLY (EXPERIMENT)" if e["variant"]==core.COLLISION_EARLY else
              "EARLY LIQUIDITY" if e["variant"]==core.EARLY_LIQ else "EARLY SIGNAL")
        if e["action"]=="BUY":
            message=(f"🧪 OSECE PAPER BUY | {kind} | {e['name']}\n"
                     f"{e['symbol']} · USD 10 SIMULATED @ {e['price']:.10g}\n"
                     f"Prelaunch phrase: {e['term_key']} · no real swap\n"
                     f"{e['mint']}")
        else:
            profit=e["usd_out"]-e["usd_in"]
            message=(f"🧪 OSECE PAPER SELL | {kind} | {e['name']}\n"
                     f"{e['symbol']} · net P&L USD {profit:+.2f} ({e['reason']})\n"
                     f"No real swap\n{e['mint']}")
        try:
            await alerts.send_text(http,cfg,message,symbol=e["symbol"] or "?",
               address=e["mint"],link=alerts.chart_link("solana",e["mint"]),
               kind="OSECE PAPER")
        except Exception:
            log.exception("OSECE notification failed trade %s",e["trade_id"])
            continue
        db.execute("INSERT OR IGNORE INTO osece_notifications "
                   "(trade_id,action,sent_ts) VALUES (?,?,?)",
                   (e["trade_id"],e["action"],time.time()))
        db.commit()
        count+=1
    return count


async def run(http,db,cfg=None):
    core.ensure(db)
    core.started_at(db,time.time())

    async def market():
        result=await collectors.poll_market(http,db)
        if result["tracked"]:
            log.info("OSECE JUPITER %s tracked %s quoted",
                     result["tracked"],result["quoted"])
        if os.environ.get("MICRO_PAPER_ENABLED")=="1":
            from .. import micro_paper
            changes=micro_paper.tick(db)
            if changes["bought"] or changes["sold"]:
                log.info("OSECE MICRO PAPER: %s",changes)
        await notify(http,db,cfg)

    async def prune():
        core.prune(db)

    tasks=[
        collectors.listen_bluesky(http,db),
        collectors.listen_wikimedia(http,db),
        collectors.listen_nostr(http,db),
        interval(300,lambda:collectors.poll_rss(http,db)),
        interval(300,lambda:collectors.poll_hackernews(http,db)),
        interval(180,lambda:collectors.poll_jupiter_recent(http,db)),
        interval(180,lambda:collectors.poll_gecko_new(http,db)),
        interval(45,market),
        interval(21600,prune)
    ]
    await asyncio.gather(*tasks)
