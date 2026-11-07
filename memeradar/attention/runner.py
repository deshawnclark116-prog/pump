"""Run the attention collectors inside the existing radar process/SQLite DB."""
from __future__ import annotations

import asyncio
import logging
import time

from . import db as evidence
from . import attention, chain, trader, public_sources, explore

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


async def run_collectors(http,db):
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
