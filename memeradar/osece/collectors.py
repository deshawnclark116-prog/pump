"""Zero-key, source-level collectors for OSECE. Each independently fail-closed.

Jetstream, Wikimedia and Nostr supply cultural events. RSS/HN add slower
independent context. PumpPortal launches are delivered via the EXISTING
radar connection, not by a second websocket. Jupiter supplies measured
five-minute buy/sell USD and market snapshots, never trade execution.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import json
import logging
import time
import xml.etree.ElementTree as ET

import aiohttp

from ..attention.attention import parse_time, valid_mint
from ..attention.chain import check_safety
from . import core

log=logging.getLogger(__name__)
BLUESKY = "wss://jetstream2.us-east.bsky.network/subscribe?wantedCollections=app.bsky.feed.post"
WIKIMEDIA = "https://stream.wikimedia.org/v2/stream/recentchange"
NOSTR_RELAYS = ("wss://relay.primal.net","wss://nos.lol")
GOOGLE_TRENDS = "https://trends.google.com/trending/rss?geo=US"
GDELT = "https://data.gdeltproject.org/gdeltv3/gal/feed.rss"
HN_NEW = "https://hacker-news.firebaseio.com/v0/newstories.json"
HN_ITEM = "https://hacker-news.firebaseio.com/v0/item/"
GECKO_NEW = "https://api.geckoterminal.com/api/v2/networks/solana/new_pools"
JUP_SEARCH = "https://api.jup.ag/tokens/v2/search"
JUP_RECENT = "https://api.jup.ag/tokens/v2/recent"


def _post_time(s):
    if not s:
        return None
    try:
        return parse_time(s)
    except (ValueError,TypeError,OverflowError):
        return None


def _rss_time(s):
    try:
        return parsedate_to_datetime(s).astimezone(timezone.utc).timestamp() if s else None
    except (TypeError,ValueError,OverflowError,IndexError):
        return None


def read_bluesky(msg, now=None):
    """Handle the tested v1 Jetstream and documented v2 event envelopes."""
    now=time.time() if now is None else now
    if not isinstance(msg,dict):
        return None
    if msg.get("kind")=="commit":
        commit=msg.get("commit") or {}
        record=commit.get("record") or {}
        if commit.get("operation")!="create" or commit.get("collection") not in (None,"app.bsky.feed.post"):
            return None
        did=msg.get("did")
        rkey=commit.get("rkey")
    else:
        payload=msg.get("payload") or {}
        if payload.get("operation") not in ("create","update"):
            return None
        record=payload.get("record") or {}
        did=payload.get("did") or msg.get("did")
        rkey=payload.get("rkey") or payload.get("recordKey")
    if not did or not rkey or not isinstance(record,dict):
        return None
    text=record.get("text")
    if not isinstance(text,str) or not text:
        return None
    published=_post_time(record.get("createdAt"))
    if published is None or not 0<=now-published<=900:
        return None
    return {"source":"bluesky","external_id":f"{did}:{rkey}","actor":did,
            "text":text,"observed_ts":now,"published_ts":published}


async def listen_bluesky(http,db):
    backoff=2
    while True:
        try:
            session=await http.session()
            async with session.ws_connect(BLUESKY,heartbeat=30,timeout=12,
                                          max_msg_size=524288) as ws:
                core.source_state(db,"bluesky","connected","live v1 Jetstream")
                backoff=2
                found=0;since=time.monotonic()
                async for frame in ws:
                    if frame.type!=aiohttp.WSMsgType.TEXT:
                        continue
                    try:
                        event=read_bluesky(json.loads(frame.data))
                        if event:
                            found+=core.record_event(db,**event)
                    except (ValueError,TypeError):
                        continue
                    if time.monotonic()-since>=120:
                        core.source_state(db,"bluesky","connected",f"{found} extracted phrase receipts in 2m")
                        found=0;since=time.monotonic()
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            core.source_state(db,"bluesky","error",type(ex).__name__)
            log.warning("OSECE bluesky disconnected: %s",type(ex).__name__)
        await asyncio.sleep(backoff)
        backoff=min(backoff*2,60)


def read_wikimedia(item,now=None):
    now=time.time() if now is None else now
    if not isinstance(item,dict) or item.get("type")!="new":
        return None
    if item.get("wiki")!="enwiki" or item.get("bot") is True:
        return None
    if item.get("namespace") not in (0,None):
        return None
    title=item.get("title")
    eid=item.get("id") or item.get("rev_id")
    if not title or eid is None:
        return None
    published=item.get("timestamp")
    if isinstance(published,(int,float)):
        published=float(published)
    else:
        published=_post_time(published)
    if published is None or not 0<=now-published<=900:
        return None
    return {"source":"wikimedia","external_id":f"enwiki:{eid}",
            "text":title,"actor":f"enwiki:{eid}","observed_ts":now,
            "published_ts":published,"curated":True}


async def listen_wikimedia(http,db):
    backoff=3
    while True:
        try:
            session=await http.session()
            timeout=aiohttp.ClientTimeout(total=None,sock_read=120,connect=15)
            async with session.get(WIKIMEDIA,headers={"Accept":"text/event-stream"},
                                   timeout=timeout) as resp:
                if resp.status!=200:
                    core.source_state(db,"wikimedia","http_error",str(resp.status))
                    await asyncio.sleep(backoff)
                    backoff=min(backoff*2,90)
                    continue
                core.source_state(db,"wikimedia","connected","live recentchange SSE")
                backoff=3
                data_lines=[]
                async for raw in resp.content:
                    if len(raw)>524288:
                        data_lines=[];continue
                    line=raw.decode("utf-8",errors="replace").strip()
                    if line.startswith("data:"):
                        data_lines.append(line[5:].strip())
                    elif not line:
                        if data_lines:
                            try:
                                obj=json.loads("\n".join(data_lines))
                                event=read_wikimedia(obj)
                                if event:
                                    core.record_event(db,**event)
                            except (ValueError,TypeError):
                                pass
                        data_lines=[]
                    if len(data_lines)>30:
                        data_lines=[]
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            core.source_state(db,"wikimedia","error",type(ex).__name__)
        await asyncio.sleep(backoff)
        backoff=min(backoff*2,90)


def read_nostr(frame,now=None):
    now=time.time() if now is None else now
    if not isinstance(frame,list) or len(frame)<3 or frame[0]!="EVENT":
        return None
    item=frame[2]
    if not isinstance(item,dict) or item.get("kind")!=1:
        return None
    posted=item.get("created_at")
    if not isinstance(posted,(int,float)) or not 0<=now-posted<=900:
        return None
    return {"source":"nostr","external_id":item.get("id"),
            "actor":item.get("pubkey"),"text":item.get("content") or "",
            "published_ts":float(posted),"observed_ts":now}


async def listen_nostr(http,db):
    backoff=4
    relay_index=0
    while True:
        relay=NOSTR_RELAYS[relay_index % len(NOSTR_RELAYS)]
        relay_index+=1
        try:
            session=await http.session()
            async with session.ws_connect(relay,heartbeat=35,timeout=12,
                                          max_msg_size=524288) as ws:
                core.source_state(db,"nostr","connected",f"{relay} kind-1")
                backoff=4
                await ws.send_json(["REQ","osece",{"kinds":[1],
                      "since":int(time.time())-5,"limit":100}])
                found=0;since=time.monotonic()
                async for frame in ws:
                    if frame.type!=aiohttp.WSMsgType.TEXT:
                        continue
                    try:
                        event=read_nostr(json.loads(frame.data))
                        if event and event["external_id"] and event["actor"]:
                            found+=core.record_event(db,**event)
                    except (ValueError,TypeError):
                        continue
                    if time.monotonic()-since>120:
                        core.source_state(db,"nostr","connected",f"{found} phrase receipts in 2m")
                        found=0;since=time.monotonic()
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            core.source_state(db,"nostr","error",type(ex).__name__)
        await asyncio.sleep(backoff)
        backoff=min(60,backoff*2)


def parse_rss(xml,source,now=None,limit=60):
    now=time.time() if now is None else now
    try:
        root=ET.fromstring(xml)
    except (ValueError,ET.ParseError):
        return []
    found=[]
    for item in root.findall(".//item")[:limit]:
        title=item.findtext("title")
        guid=item.findtext("guid") or item.findtext("link")
        published=_rss_time(item.findtext("pubDate"))
        max_age=6*3600 if source=="google_trends" else 900
        if not title or not guid or published is None or not 0<=now-published<=max_age:
            continue
        found.append({"source":source,"external_id":guid,
                      "actor":guid,"text":title,"observed_ts":now,
                      "published_ts":published,"curated":True})
    return found


async def poll_rss(http,db):
    outcome={}
    for source,url in (("google_trends",GOOGLE_TRENDS),("gdelt",GDELT)):
        try:
            raw=await http.get(url,as_text=True)
            if not isinstance(raw,str):
                core.source_state(db,source,"error","RSS unavailable")
                outcome[source]=None
                continue
            events=parse_rss(raw,source,limit=30 if source=="google_trends" else 40)
            count=0
            for e in events:
                count+=core.record_event(db,**e)
            core.source_state(db,source,"ok",f"{len(events)} recent items; {count} phrases")
            outcome[source]=count
        except asyncio.CancelledError:
            raise
        except Exception as ex:
            core.source_state(db,source,"error",type(ex).__name__)
            outcome[source]=None
    return outcome


async def poll_hackernews(http,db):
    items=await http.get(HN_NEW)
    if not isinstance(items,list):
        core.source_state(db,"hackernews","error","newstories unavailable")
        return 0
    now=time.time();count=0;looked=0
    for iid in items[:8]:
        data=await http.get(f"{HN_ITEM}{iid}.json")
        if not isinstance(data,dict) or not data.get("title"):
            continue
        posted=data.get("time")
        if not isinstance(posted,(int,float)) or now-posted>900 or posted>now+120:
            continue
        looked+=1
        count+=core.record_event(
            db,source="hackernews",external_id=iid,actor=iid,
            text=data["title"],published_ts=float(posted),
            observed_ts=now,curated=True)
    core.source_state(db,"hackernews","ok",f"{looked} fresh new items; {count} phrases")
    return count


def _first_pool_time(token):
    first=token.get("firstPool")
    if not isinstance(first,dict):
        return None
    for k in ("createdAt","created_at","poolCreatedAt"):
        value=first.get(k)
        if isinstance(value,(int,float)):
            return float(value)/1000 if value>1e11 else float(value)
        if isinstance(value,str):
            t=_post_time(value)
            if t is not None:
                return t
    return None


async def poll_jupiter_recent(http,db):
    """Fallback source requires explicit first pool timestamp; never infer it."""
    data=await http.get(JUP_RECENT)
    if not isinstance(data,list):
        core.source_state(db,"jupiter_recent","error","recent tokens unavailable")
        return 0
    now=time.time();created=0
    for token in data[:30]:
        if not isinstance(token,dict):
            continue
        created_at=_first_pool_time(token)
        if created_at is None:
            continue
        record=core.record_launch(db,mint=token.get("id"),
             name=token.get("name"),symbol=token.get("symbol") or "",
             created_ts=created_at,observed_ts=now,source="jupiter_recent")
        created+=int(record["status"]=="matched")
    core.source_state(db,"jupiter_recent","ok",f"{len(data)} recent tokens; {created} matched")
    return created


async def poll_gecko_new(http,db):
    payload=await http.get(GECKO_NEW)
    pools=payload.get("data") if isinstance(payload,dict) else None
    if not isinstance(pools,list):
        core.source_state(db,"gecko_new","error","new pools unavailable")
        return 0
    tokens={}
    for item in payload.get("included") or []:
        if item.get("type")=="token":
            tokens[item.get("id")]=item.get("attributes") or {}
    matched=0; now=time.time()
    for pool in pools[:30]:
        if not isinstance(pool,dict):
            continue
        rel=(pool.get("relationships") or {}).get("base_token") or {}
        tid=(rel.get("data") or {}).get("id") or ""
        network,sep,mint=tid.partition("_")
        if network!="solana" or not sep or not valid_mint(mint):
            continue
        details=tokens.get(tid) or {}
        stamp=(pool.get("attributes") or {}).get("pool_created_at")
        published=_post_time(stamp)
        if not details.get("name") or published is None:
            continue
        r=core.record_launch(db,mint=mint,name=details["name"],
             symbol=details.get("symbol") or "",created_ts=published,
             observed_ts=now,source="gecko_new")
        matched+=int(r["status"]=="matched")
    core.source_state(db,"gecko_new","ok",f"{len(pools)} pools; {matched} matched")
    return matched


async def poll_market(http,db):
    """Jupiter keyless batch 100 mints, maximum one request per 45s iteration.

    Quotes are persisted with receipt timestamps; a missing token does not
    become a zero or a simulated fill. RugCheck is independent and fail-closed.
    """
    mints=core.tracked_mints(db)
    if not mints:
        return {"tracked":0,"quoted":0,"trades":core.tick(db)}
    now=time.time()
    data=await http.get(JUP_SEARCH,params={"query":",".join(mints[:80])})
    valid={t["id"]:t for t in data if isinstance(t,dict) and valid_mint(t.get("id"))} if isinstance(data,list) else {}
    if not isinstance(data,list):
        core.source_state(db,"jupiter_market","error","batch token lookup unavailable")
    else:
        for mint in mints:
            if mint in valid:
                core.record_jupiter_quote(db,valid[mint],now=now)
        core.source_state(db,"jupiter_market","ok",
                          f"{len(valid)}/{len(mints)} tracked with token info")
    # No RugCheck for every Pump creation; just watched and indexed mints,
    # at most three calls per poll and only if no recent passing result.
    checks=0
    for mint in mints:
        if checks>=3:
            break
        row=db.execute(
            "SELECT state,checked_ts FROM attention_safety WHERE mint=?",
            (mint,)).fetchone()
        if row and (row["state"]=="failed" or
                    now-row["checked_ts"]<(3600 if row["state"]=="passed" else 900)):
            continue
        if mint not in valid:
            continue
        try:
            await check_safety(http,db,mint)
            checks+=1
        except asyncio.CancelledError:
            raise
        except Exception:
            log.warning("OSECE RugCheck unavailable for %s",mint)
    result=core.tick(db)
    if any(result.get(k) for k in ("watching","queued","bought","sold","rejected")):
        log.info("OSECE tick: %s",result)
    return {"tracked":len(mints),"quoted":len(valid),"safety_checks":checks,
            "trades":result}
