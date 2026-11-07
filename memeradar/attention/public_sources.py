"""Credential-free public attention research collectors (no trading).

Social events require an explicit, valid Solana mint in a recent public post.
Market-side trending is persisted separately; ticker-only CoinGecko rankings are
NOT joined to a mint and must never be used as a token buy instruction.
"""
from __future__ import annotations

import asyncio
from email.utils import formatdate
import html
import logging
import re
import time

from . import db as evidence
from .attention import ingest, parse_time, valid_mint

log = logging.getLogger(__name__)
BLUESKY = "https://api.bsky.app/xrpc/app.bsky.feed.searchPosts"
BIZ = "https://a.4cdn.org/biz/catalog.json"
GECKO = "https://api.geckoterminal.com/api/v2/networks/solana/trending_pools"
COINGECKO = "https://api.coingecko.com/api/v3/search/trending"
_HTML = re.compile(r"<[^>]*>")
SEARCHES = ("solana", "pump.fun")


async def bluesky(http, db, *, now=None):
    now = time.time() if now is None else now
    hits = posts = successful = 0
    for query in SEARCHES:
        response = await http.get(BLUESKY, params={"q": query, "limit": 100, "sort": "latest"})
        if not isinstance(response, dict) or not isinstance(response.get("posts"), list):
            continue
        successful += 1
        for post in response["posts"]:
            if not isinstance(post, dict):
                continue
            record = post.get("record") or {}
            if not isinstance(record, dict) or not post.get("uri"):
                continue
            posts += 1
            engagement = sum(int(post.get(f"{key}Count") or 0)
                             for key in ("like", "repost", "reply", "quote"))
            hits += ingest(db, "bluesky", post["uri"], record.get("text") or "",
                           posted_ts=parse_time(record.get("createdAt") or post.get("indexedAt")),
                           engagement=engagement, now=now)
    evidence.source_status(db, "bluesky", "ok" if successful else "error",
                           detail=f"{successful}/{len(SEARCHES)} searches; {posts} posts; {hits} mint hits",
                           ok=successful > 0)
    return hits


def _plain_post(post):
    return html.unescape(_HTML.sub(" ", str(post.get("sub") or "") + " " + str(post.get("com") or "")))


async def biz(http, db, *, now=None):
    now = time.time() if now is None else now
    last = db.execute("SELECT last_success_ts FROM attention_source_state WHERE source='4chan_biz'").fetchone()
    headers = {}
    if last and last["last_success_ts"]:
        headers["If-Modified-Since"] = formatdate(last["last_success_ts"], usegmt=True)
    response = await http.get(BIZ, headers=headers)
    if isinstance(response, dict) and response.get("_not_modified") is True:
        evidence.source_status(db, "4chan_biz", "not_modified",
                               detail="HTTP 304; retained prior post evidence")
        return 0
    if not isinstance(response, list):
        evidence.source_status(db, "4chan_biz", "error", detail="catalog unavailable")
        return 0
    hits = posts = 0
    for page in response:
        if not isinstance(page, dict):
            continue
        for thread in page.get("threads") or []:
            if not isinstance(thread, dict):
                continue
            for post in [thread] + list(thread.get("last_replies") or []):
                if not isinstance(post, dict) or not post.get("no") or not post.get("time"):
                    continue
                posts += 1
                hits += ingest(db, "4chan_biz", f"biz:{post['no']}", _plain_post(post),
                               posted_ts=float(post["time"]), now=now)
    evidence.source_status(db, "4chan_biz", "ok",
                           detail=f"{posts} catalog/preview posts; {hits} mint hits", ok=True)
    return hits


def gecko_mint(pool):
    rel = (pool.get("relationships") or {}).get("base_token") or {}
    token_id = ((rel.get("data") or {}).get("id") or "")
    network, separator, mint = token_id.partition("_")
    return mint if separator and network == "solana" and valid_mint(mint) else None


async def gecko_trending(http, db, *, now=None):
    now = time.time() if now is None else now
    response = await http.get(GECKO)
    pools = response.get("data") if isinstance(response, dict) else None
    if not isinstance(pools, list):
        evidence.source_status(db, "geckoterminal_trending", "error",
                               detail="trending pools unavailable")
        return 0
    recorded = validated = 0
    for rank, pool in enumerate(pools, 1):
        if not isinstance(pool, dict) or not pool.get("id"):
            continue
        mint = gecko_mint(pool)
        if mint:
            validated += 1
        recorded += int(evidence.record_trend(
            db, source="geckoterminal_trending", source_id=str(pool["id"]),
            observed_ts=now, mint=mint, rank=rank))
    evidence.source_status(db, "geckoterminal_trending", "ok",
                           detail=f"{len(pools)} pools; {validated} validated base mints; {recorded} observations",
                           ok=True)
    return validated


async def coingecko_trending(http, db, *, now=None):
    now = time.time() if now is None else now
    response = await http.get(COINGECKO)
    coins = response.get("coins") if isinstance(response, dict) else None
    if not isinstance(coins, list):
        evidence.source_status(db, "coingecko_trending", "error",
                               detail="trending searches unavailable")
        return 0
    recorded = 0
    for rank, item in enumerate(coins, 1):
        coin = item.get("item") if isinstance(item, dict) else None
        if not isinstance(coin, dict) or not coin.get("id"):
            continue
        # CoinGecko search rankings typically provide symbol/id, not a Solana
        # mint. Never attribute them to a similarly named token.
        recorded += int(evidence.record_trend(
            db, source="coingecko_trending", source_id=str(coin["id"]),
            observed_ts=now, symbol=str(coin.get("symbol") or ""), rank=rank))
    evidence.source_status(db, "coingecko_trending", "ok",
                           detail=f"{recorded} ranked coins (unresolved: no mint supplied)",
                           ok=True)
    return recorded


async def poll_public(http, db):
    """Run each source independently; no missing feed may fabricate observations."""
    result = {}
    for name, fn in (("bluesky", bluesky), ("4chan_biz", biz),
                     ("geckoterminal_trending", gecko_trending),
                     ("coingecko_trending", coingecko_trending)):
        try:
            result[name] = await fn(http, db)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("%s public attention collector failed", name)
            evidence.source_status(db, name, "error", detail=type(exc).__name__)
            result[name] = None
    log.info("public attention polls: %s", result)
    return result
