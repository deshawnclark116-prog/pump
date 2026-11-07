"""Run the attention collectors inside the existing radar process/SQLite DB."""
from __future__ import annotations

import asyncio
import logging
import time

from . import db as evidence
from . import attention, chain, trader

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

    async def market():
        result=await chain.poll_chain(http,db)
        if result["tracked"]:
            log.info("attention chain: %s",result)
        outcome=trader.paper_tick(db)
        if outcome["status"]=="forward_paper_only" and (outcome["entries"] or outcome["exits"]):
            log.info("attention paper: %s",outcome)

    tasks=[interval(60,social),interval(30,market),attention.listen_telegram(db)]
    await asyncio.gather(*tasks)
