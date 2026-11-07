"""Live forward *paper* journal only. No wallet, Jupiter, signer, or RPC swaps.

Disabled until an independent model passes the frozen retrospective gate
AND an operator opts into paper collection after the data-only stage.
"""
from __future__ import annotations

import json
import os
import time

from . import db as evidence
from .backtest import FEE, SLIPPAGE, realized_usd, percent_return, buy_arrived, eligible, signals
from .model import latest_model


def enabled():
    return os.environ.get("ATTENTION_PAPER_ENABLED")=="1"


def paper_tick(db, now=None):
    now=time.time() if now is None else now
    model=latest_model(db)
    if not model:
        return {"status":"data_collection_only","reason":"no retrospective model passed validation"}
    if not enabled():
        return {"status":"data_collection_only","reason":"paper toggle not enabled"}
    spec=json.loads(model["model_json"])
    version=model["version"]
    completed=0;entries=0;queued=0

    # First manage positions, fail closed on missing/old quotes.
    for pos in db.execute("SELECT * FROM attention_paper_trades WHERE exit_ts IS NULL").fetchall():
        row=db.execute("SELECT * FROM attention_chain WHERE mint=? AND observed_ts>? AND observed_ts<=? "
                       "ORDER BY observed_ts DESC LIMIT 1",
                       (pos["mint"],pos["entry_ts"],now)).fetchone()
        if not row or now-row["observed_ts"]>90:
            continue
        all_rows=[dict(r) for r in db.execute(
            "SELECT * FROM attention_chain WHERE mint=? AND observed_ts>? AND observed_ts<=? "
            "ORDER BY observed_ts",(pos["mint"],pos["entry_ts"],now))]
        arrived=buy_arrived(all_rows,pos["entry_ts"],now)
        if arrived is None:
            continue  # missing quotes: mark-to-market unknown, NOT an invented fill
        pct=percent_return(pos["entry_price"],row["price_usd"])
        reason=("STOP" if pct<=-30 else
                "75PCT_ARRIVED" if arrived>=.75*pos["predicted_buy_proxy"] else
                "TIME" if now-pos["entry_ts"]>=3600 else None)
        if reason:
            usd_out=realized_usd(pos["entry_price"],row["price_usd"],pos["usd_in"])
            db.execute("UPDATE attention_paper_trades SET exit_ts=?,exit_price=?,usd_out=?,reason=? "
                       "WHERE id=?",(row["observed_ts"],row["price_usd"],usd_out,reason,pos["id"]))
            completed+=1

    # A signal must wait for the NEXT 30-second quote; no same-quote instant fills.
    for signal in db.execute("SELECT * FROM attention_predictions WHERE gate_reason='queued' "
                             "AND model_version=? ORDER BY observed_ts",(version,)).fetchall():
        if now-signal["observed_ts"]>180:
            db.execute("UPDATE attention_predictions SET gate_reason='expired' "
                       "WHERE mint=? AND observed_ts=? AND model_version=?",
                       (signal["mint"],signal["observed_ts"],version))
            continue
        row=db.execute("SELECT * FROM attention_chain WHERE mint=? AND observed_ts>=? "
                       "AND observed_ts<=? ORDER BY observed_ts LIMIT 1",
                       (signal["mint"],signal["observed_ts"]+20,now)).fetchone()
        if not row or now-row["observed_ts"]>90:
            continue
        if not eligible(db,signal["mint"],row["observed_ts"],row["liquidity_usd"]):
            reason="risk_rejected"
        elif db.execute("SELECT 1 FROM attention_paper_trades WHERE mint=? AND exit_ts IS NULL",
                        (signal["mint"],)).fetchone():
            reason="already_open"
        elif db.execute("SELECT count(*) FROM attention_paper_trades WHERE exit_ts IS NULL").fetchone()[0]>=5:
            reason="capacity"
        else:
            used=db.execute("SELECT COALESCE(SUM(usd_in),0),COALESCE(SUM(usd_out),0) "
                            "FROM attention_paper_trades").fetchone()
            cash=250-used[0]+used[1]
            if cash<10:
                reason="no_cash"
            else:
                db.execute("INSERT INTO attention_paper_trades "
                           "(mint,model_version,entry_ts,entry_price,usd_in,predicted_buy_proxy,entry_buy_proxy) "
                           "VALUES (?,?,?,?,?,?,?)",
                           (signal["mint"],version,row["observed_ts"],row["price_usd"],
                            10,signal["predicted_future_buy_proxy"],
                            row["buy_volume_proxy_m5"]))
                reason="paper_entry";entries+=1
        db.execute("UPDATE attention_predictions SET gate_reason=? "
                   "WHERE mint=? AND observed_ts=? AND model_version=?",
                   (reason,signal["mint"],signal["observed_ts"],version))

    # Run signals after positions; no possibility of entry before source receipt.
    # At most one qualifying signal per mint per 5 minutes and one hour per exit.
    if now>model["created_ts"]:
        candidates,_=signals(db,spec,max(model["created_ts"],now-75),now+1)
        for s in candidates:
            last=db.execute("SELECT MAX(observed_ts) FROM attention_predictions WHERE mint=? AND "
                            "model_version=?",(s["mint"],version)).fetchone()[0]
            last_exit=db.execute("SELECT MAX(exit_ts) FROM attention_paper_trades WHERE mint=?",
                                 (s["mint"],)).fetchone()[0]
            if (last is not None and s["ts"]-last<300) or (last_exit and s["ts"]-last_exit<3600):
                continue
            db.execute("INSERT OR IGNORE INTO attention_predictions "
                       "(mint,observed_ts,model_version,predicted_future_buy_proxy,"
                       "already_seen_buy_proxy,waiting_buy_proxy,liquidity_usd,gate_reason) "
                       "VALUES (?,?,?,?,?,?,?,?)",
                       (s["mint"],s["ts"],version,s["predicted"],s["seen"],
                        s["predicted"],s["liq"],"queued"))
            queued+=1
    db.commit()
    return {"status":"forward_paper_only","model":version,"queued":queued,
            "entries":entries,"exits":completed}
