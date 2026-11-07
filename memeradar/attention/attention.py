"""Timestamped social attention ingestion. Explicit authorization required per source.

No ticker-only attribution: shared ticker symbols are ambiguous. A 32-byte
Solana mint must appear in the observed message. No inferred audience sizes.
"""
from __future__ import annotations

import asyncio
import base64
from datetime import datetime, timezone
import logging
import os
import re
import time

from . import db as evidence

log = logging.getLogger(__name__)
_BASE58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
_RX = re.compile(r"(?<![a-zA-Z0-9])[1-9A-HJ-NP-Za-km-z]{32,44}(?![a-zA-Z0-9])")
X_SEARCH = "solana (contract OR CA OR pump.fun) -is:retweet"
REDDIT_SUBS = ("solanamemecoins", "memecoins", "CryptoMoonShots")


def valid_mint(s: str) -> bool:
    if not isinstance(s, str) or not 32 <= len(s) <= 44:
        return False
    n = 0
    for ch in s:
        i = _BASE58.find(ch)
        if i < 0:
            return False
        n = n * 58 + i
    leading_zeros = len(s) - len(s.lstrip("1"))
    return leading_zeros + ((n.bit_length()+7)//8) == 32


def mints(text: str) -> set[str]:
    return {v for v in _RX.findall(text or "") if valid_mint(v)}


def parse_time(s):
    if not s:
        return None
    return datetime.fromisoformat(s.replace("Z", "+00:00")).timestamp()


def ingest(db, source, external_id, text, *, posted_ts=None, engagement=None,
           audience=None, now=None):
    now = time.time() if now is None else now
    # Backfilled/stale posts are not usable as "early discovery" even if first seen now.
    if posted_ts is not None and (posted_ts > now + 300 or posted_ts < now - 900):
        return 0
    count = 0
    for mint in mints(text):
        if evidence.record_hit(db, source=source, post_id=external_id, mint=mint,
                               observed_ts=now, posted_ts=posted_ts,
                               engagement=engagement, audience=audience):
            count += 1
    return count


async def poll_x(http, db):
    """X API is pay-per-read. Disabled unless an explicit daily post budget is set."""
    token = os.environ.get("ATTENTION_X_BEARER_TOKEN")
    cap = int(os.environ.get("ATTENTION_X_POST_BUDGET_PER_DAY", "0"))
    if not token or cap <= 0:
        evidence.source_status(db, "x", "disabled", detail="bearer token or daily paid-read budget missing")
        return 0
    now = time.time()
    day = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%d")
    count_state = f"x_usage_{day}"
    used = int(evidence.source_cursor(db, count_state) or 0)
    if used + 10 > cap:
        evidence.source_status(db, "x", "budget_exhausted", detail=f"{used}/{cap} posts budgeted")
        return 0
    params = {"query":X_SEARCH, "max_results":min(100, cap-used),
              "tweet.fields":"created_at,public_metrics"}
    prev = evidence.source_cursor(db, "x")
    if prev:
        params["since_id"] = prev
    result = await http.get("https://api.x.com/2/tweets/search/recent",
                            params=params,headers={"Authorization":f"Bearer {token}"})
    if not isinstance(result, dict) or not isinstance(result.get("meta"),dict):
        evidence.source_status(db, "x", "error", detail="API unavailable/unauthorized; no data assumed")
        return 0
    items = result.get("data") or []
    used += len(items)  # approximate billable fetched posts; audit provider invoice separately
    evidence.source_status(db, count_state, "count", cursor=str(used), ok=True)
    hits=0
    for item in items:
        metrics=item.get("public_metrics") or {}
        engagement=sum(float(metrics.get(k) or 0) for k in
                       ("like_count","retweet_count","quote_count","reply_count"))
        hits+=ingest(db,"x",item.get("id"),item.get("text",""),
                     posted_ts=parse_time(item.get("created_at")),engagement=engagement,now=now)
    newest=(result.get("meta") or {}).get("newest_id")
    evidence.source_status(db,"x","ok",cursor=newest or prev,
                           detail=f"{len(items)} posts fetched, {hits} mints; cost applies",ok=True)
    return hits


async def reddit_token(http):
    import urllib.parse
    client=os.environ.get("ATTENTION_REDDIT_CLIENT_ID")
    secret=os.environ.get("ATTENTION_REDDIT_CLIENT_SECRET")
    if not client or not secret:
        return None
    basic=base64.b64encode(f"{client}:{secret}".encode()).decode()
    data=await http.post("https://www.reddit.com/api/v1/access_token",
                         data={"grant_type":"client_credentials"},
                         headers={"Authorization":f"Basic {basic}",
                                  "Content-Type":"application/x-www-form-urlencoded"})
    return data.get("access_token") if isinstance(data,dict) else None


async def poll_reddit(http, db):
    """Only use Reddit if API access and this research use case were authorized."""
    if os.environ.get("ATTENTION_REDDIT_APPROVED") != "1":
        evidence.source_status(db,"reddit","disabled",detail="approved Data API access/use required")
        return 0
    token=await reddit_token(http)
    if not token:
        evidence.source_status(db,"reddit","error",detail="OAuth credentials unavailable")
        return 0
    headers={"Authorization":f"Bearer {token}"}
    hits=0; succeeded=0; now=time.time()
    for sub in REDDIT_SUBS:
        data=await http.get(f"https://oauth.reddit.com/r/{sub}/new",
                            params={"limit":100},headers=headers)
        if not isinstance(data,dict) or not isinstance(data.get("data"),dict):
            continue
        succeeded += 1
        for item in (data["data"].get("children") or []):
            post=item.get("data") or {}
            # Reddit's published timestamp cannot be substituted for when we saw the post.
            hits += ingest(db,"reddit",post.get("id"),
                           f"{post.get('title','')} {post.get('selftext','')}",
                           posted_ts=post.get("created_utc"),
                           engagement=(post.get("score") or 0)+(post.get("num_comments") or 0),now=now)
    evidence.source_status(db,"reddit","ok" if succeeded==len(REDDIT_SUBS) else "partial_error",
                           detail=f"{succeeded}/{len(REDDIT_SUBS)} communities, {hits} mint hits",
                           ok=succeeded>0)
    return hits


async def listen_telegram(db):
    """User-authorized public-channel listener; secret StringSession stays in env."""
    session=os.environ.get("ATTENTION_TELEGRAM_SESSION")
    app_id=os.environ.get("ATTENTION_TELEGRAM_API_ID")
    app_hash=os.environ.get("ATTENTION_TELEGRAM_API_HASH")
    channels=[x.strip() for x in os.environ.get("ATTENTION_TELEGRAM_CHANNELS","").split(",") if x.strip()]
    if not (session and app_id and app_hash and channels):
        evidence.source_status(db,"telegram","disabled",
                               detail="user session, API ID/hash or joined channels missing")
        return
    try:
        from telethon import TelegramClient, events
        from telethon.sessions import StringSession
    except ImportError:
        evidence.source_status(db,"telegram","error",detail="install telethon from requirements-attention.txt")
        return
    try:
        async with TelegramClient(StringSession(session),int(app_id),app_hash) as client:
            if not await client.is_user_authorized():
                evidence.source_status(db,"telegram","error",detail="session not authorized")
                return
            entities=[]
            for channel in channels:
                try:
                    entities.append(await client.get_entity(channel))
                except Exception:
                    log.warning("Telegram channel unavailable: %s",channel)
            if not entities:
                evidence.source_status(db,"telegram","error",detail="no joined/accessible channels")
                return

            @client.on(events.NewMessage(chats=entities))
            async def on_message(event):
                msg=event.message
                posted=msg.date.timestamp() if msg.date else None
                count=ingest(db,"telegram",f"{event.chat_id}:{msg.id}",msg.raw_text or "",
                             posted_ts=posted,engagement=getattr(msg,"views",None))
                if count:
                    log.info("telegram: captured %d validated mint(s)",count)
                evidence.source_status(db,"telegram","ok",
                                       detail=f"listening to {len(entities)} channels",ok=True)

            evidence.source_status(db,"telegram","ok",
                                   detail=f"listening to {len(entities)} channels",ok=True)
            await client.run_until_disconnected()
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        log.warning("Telegram listener disconnected: %s",type(exc).__name__)
        evidence.source_status(db,"telegram","error",detail=type(exc).__name__)


async def poll_social(http, db):
    for fn in (poll_x,poll_reddit):
        try:
            await fn(http,db)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("attention source poll crashed")
