"""DexScreener observations + independent RugCheck audit for attention mints.

DexScreener five-minute volume/transactions are overlapping rolling windows.
The buy-dollar figure is ONLY a proxy = 5-min total volume * buy transaction
fraction, NOT observed on-chain buy USD or proof of "money waiting".
"""
from __future__ import annotations

import asyncio
import json
import logging
import math
import time

from ..config import SafetyRules
from ..safety import evaluate_rugcheck
from .db import active_mints

log = logging.getLogger(__name__)
DEX="https://api.dexscreener.com/tokens/v1/solana/"
RUG="https://api.rugcheck.xyz/v1/tokens/"


def number(x, default=None):
    try:
        v=float(x)
        return v if math.isfinite(v) and v>=0 else default
    except (TypeError,ValueError,OverflowError):
        return default


def best_pair(pairs,mint):
    eligible=[p for p in pairs if
              isinstance(p,dict) and p.get("chainId")=="solana"
              and (p.get("baseToken") or {}).get("address")==mint
              and p.get("dexId")!="pumpfun"
              and number((p.get("liquidity") or {}).get("usd"),0)>0
              and number(p.get("priceUsd"),0)>0]
    return max(eligible,key=lambda p:number((p.get("liquidity") or {}).get("usd"),0),
               default=None)


def parse_snapshot(pair, mint, now):
    px=number(pair.get("priceUsd"))
    liq=number((pair.get("liquidity") or {}).get("usd"))
    vol=number((pair.get("volume") or {}).get("m5"))
    tx= (pair.get("txns") or {}).get("m5") or {}
    buys=number(tx.get("buys")); sells=number(tx.get("sells"))
    if None in (px,liq,vol,buys,sells) or px<=0 or liq<100 or buys+sells<1:
        return None
    buys,sells=int(buys),int(sells)
    return (mint, now, px, liq, number(pair.get("marketCap")),
            vol,buys,sells,vol*buys/(buys+sells),pair.get("pairAddress") or "")


async def check_safety(http,db,mint,now=None):
    now=time.time() if now is None else now
    prior=db.execute("SELECT state,checked_ts FROM attention_safety WHERE mint=?",(mint,)).fetchone()
    if prior and (prior["state"]=="failed" or
                  now-prior["checked_ts"] < (900 if prior["state"]=="unknown" else 3600)):
        return prior["state"]
    report=await http.get(f"{RUG}{mint}/report")
    if not isinstance(report,dict) or report.get("mint")!=mint:
        state,reasons="unknown",["RugCheck unavailable or incomplete; fail closed"]
    else:
        checked=evaluate_rugcheck(report,SafetyRules())
        state="passed" if checked.passed else "failed"
        reasons=checked.reasons
    db.execute("INSERT INTO attention_safety VALUES (?,?,?,?) "
               "ON CONFLICT(mint) DO UPDATE SET checked_ts=excluded.checked_ts, "
               "state=excluded.state, reasons=excluded.reasons",
               (mint,now,state,json.dumps(reasons)))
    db.execute("INSERT OR IGNORE INTO attention_safety_history VALUES (?,?,?,?)",
               (mint,now,state,json.dumps(reasons)))
    db.commit()
    return state


async def poll_chain(http,db,*,now=None,limit=150):
    now=time.time() if now is None else now
    mints=active_mints(db,now=now,limit=limit)
    if not mints:
        return {"tracked":0,"recorded":0,"safety_checked":0}
    # Bound the number of RugCheck requests per cycle, even if a burst finds 100 mints.
    checked=0
    for mint in mints:
        prior=db.execute("SELECT state,checked_ts FROM attention_safety WHERE mint=?",
                         (mint,)).fetchone()
        if prior and (prior["state"]=="failed" or
                      now-prior["checked_ts"]<(900 if prior["state"]=="unknown" else 3600)):
            continue
        if checked>=4:
            break
        try:
            await check_safety(http,db,mint,now=now)
        except asyncio.CancelledError:
            raise
        except Exception:
            log.exception("RugCheck failed for mint %s",mint)
        checked+=1
    total=0
    for i in range(0,len(mints),30):
        group=mints[i:i+30]
        response=await http.get(DEX+",".join(group))
        if not isinstance(response,list):
            log.warning("DexScreener did not return a pair list for %d mints",len(group))
            continue
        for mint in group:
            p=best_pair(response,mint)
            if p is None:
                continue
            snap=parse_snapshot(p,mint,now)
            if snap is None:
                continue
            db.execute("INSERT OR IGNORE INTO attention_chain "
                       "(mint,observed_ts,price_usd,liquidity_usd,market_cap,"
                       "vol_m5,buys_m5,sells_m5,buy_volume_proxy_m5,pair) "
                       "VALUES (?,?,?,?,?,?,?,?,?,?)",snap)
            total+=1
    db.commit()
    return {"tracked":len(mints),"recorded":total,"safety_checked":checked}
