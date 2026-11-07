"""Leak-resistant attention -> future *proxy* buy volume forecasting.

A five-minute rolling DexScreener volume cannot reveal actual buy USD.
Our target integrates observed rolling buy-volume proxies in 30s steps,
requires near-complete future coverage, and is labeled "proxy" everywhere.
No trained predictions are eligible for trading until chronological,
token-disjoint holdout validation passes. Forward paper is opt-in separately.
"""
from __future__ import annotations

from bisect import bisect_left, bisect_right
from collections import defaultdict
from datetime import datetime, timezone
import json
import math
import statistics
import time

from .db import ensure

HORIZON=3600
FEATURES=("x_posts","telegram_posts","reddit_posts","log_engagement","log_audience",
          "log_buy_proxy_m5","log_liquidity","buy_ratio","price_change_5m")
SOURCE_ORDER=("x","telegram","reddit")
MIN_DAYS=14
MIN_TRAIN=300
MIN_HOLDOUT=100
MIN_MINTS=50
MIN_SOURCES=2


def features_for(row,hits,earlier=None):
    counts={source:0 for source in SOURCE_ORDER}
    engagement=0.; audience=0.
    for hit in hits:
        if row["observed_ts"]-300 < hit["observed_ts"] <= row["observed_ts"]:
            counts[hit["source"]]=counts.get(hit["source"],0)+1
            engagement+=min(100000,max(0,hit["engagement"] or 0))
            audience+=min(1e7,max(0,hit["audience"] or 0))
    buys,sells=row["buys_m5"],row["sells_m5"]
    prior_price=earlier["price_usd"] if earlier else row["price_usd"]
    return [float(counts.get(s,0)) for s in SOURCE_ORDER]+[
        math.log1p(engagement),math.log1p(audience),
        math.log1p(max(0,row["buy_volume_proxy_m5"])),
        math.log1p(max(0,row["liquidity_usd"])),
        buys/max(1,buys+sells),
        max(-1,min(5,row["price_usd"]/max(prior_price,1e-15)-1))]


def future_proxy(rows,start,horizon=HORIZON):
    """Forward-only coverage: a partial or stale horizon is NOT a zero label."""
    last=start; amount=0.; covered=0.
    for row in rows:
        ts=row["observed_ts"]
        if ts<=start:
            continue
        if ts>start+horizon:
            break
        dt=ts-last
        if dt>120:
            return None
        if dt<=0:
            continue
        # Each five-minute rolling volume is integrated over NEW time only.
        amount+=row["buy_volume_proxy_m5"]*(dt/300.)
        covered+=dt
        last=ts
    return amount if covered>=horizon-120 and covered>=0.95*horizon else None


def examples(db,until=None):
    ensure(db)
    until=time.time() if until is None else until
    all_hits=defaultdict(list)
    for hit in db.execute("SELECT source,mint,observed_ts,engagement,audience FROM attention_hits "
                          "WHERE observed_ts<=? ORDER BY mint,observed_ts",(until,)):
        all_hits[hit["mint"]].append(dict(hit))
    grouped=defaultdict(list)
    for r in db.execute("SELECT * FROM attention_chain WHERE observed_ts<=? "
                        "ORDER BY mint,observed_ts",(until,)):
        if r["mint"] in all_hits:
            grouped[r["mint"]].append(dict(r))
    out=[]
    for mint,chain in grouped.items():
        hits=all_hits[mint]; hts=[h["observed_ts"] for h in hits]
        timestamps=[r["observed_ts"] for r in chain]
        last_sample=-1e15
        for i,row in enumerate(chain):
            ts=row["observed_ts"]
            if ts-last_sample<300 or ts+HORIZON>until:
                continue
            lo=bisect_right(hts,ts-300);hi=bisect_right(hts,ts)
            if lo==hi:
                continue
            label=future_proxy(chain[i+1:],ts)
            if label is None:
                continue
            earlier_idx=bisect_right(timestamps,ts-300)-1
            prior=chain[earlier_idx] if earlier_idx>=0 else None
            x=features_for(row,hits[lo:hi],prior)
            out.append(dict(mint=mint,ts=ts,x=x,y=label,seen=row["buy_volume_proxy_m5"],
                            liquidity=row["liquidity_usd"]))
            last_sample=ts
    return sorted(out,key=lambda x:(x["ts"],x["mint"]))


def forecast(spec,x):
    """JSON coefficients; never unpickle unknown model data."""
    z=spec["intercept"]+sum(c*(v-m)/s for v,c,m,s in
                           zip(x,spec["coef"],spec["mean"],spec["scale"]))
    return max(0.,math.expm1(max(0.,min(25.,z))))


def mae_log(pred,actual):
    if not actual:
        return None
    return sum(abs(math.log1p(max(0,p))-math.log1p(max(0,y)))
               for p,y in zip(pred,actual))/len(actual)


def _split(rows):
    if not rows:
        return [],[],None
    # Temporal split + 2-hour embargo, and no mint shared across train/holdout.
    cutoff=rows[int(len(rows)*.75)]["ts"]
    valid=[r for r in rows if r["ts"]>=cutoff]
    valid_mints={r["mint"] for r in valid}
    train=[r for r in rows if r["ts"]<cutoff-7200 and r["mint"] not in valid_mints]
    return train,valid,cutoff


def train(db,now=None):
    """Train once on frozen history, never retune parameters against a used holdout."""
    now=time.time() if now is None else now
    rows=examples(db,until=now)
    train_rows,holdout,cutoff=_split(rows)
    days=(max(r["ts"] for r in rows)-min(r["ts"] for r in rows))/86400 if rows else 0
    sources={SOURCE_ORDER[i] for r in train_rows for i in range(3) if r["x"][i]>0}
    summary={"status":"insufficient_data","rows":len(rows),"days":round(days,2),
             "train":len(train_rows),"holdout":len(holdout),
             "train_mints":len({r["mint"] for r in train_rows}),
             "holdout_mints":len({r["mint"] for r in holdout}),
             "sources":sorted(sources),"required":{"days":MIN_DAYS,
                "train":MIN_TRAIN,"holdout":MIN_HOLDOUT,
                "mints":MIN_MINTS,"sources":MIN_SOURCES}}
    if (days<MIN_DAYS or len(train_rows)<MIN_TRAIN or len(holdout)<MIN_HOLDOUT
            or len({r["mint"] for r in train_rows})<MIN_MINTS
            or len(sources)<MIN_SOURCES):
        return summary
    # Optional ML dependency is required only when there is enough real evidence.
    from sklearn.linear_model import Ridge
    from sklearn.preprocessing import StandardScaler
    scaler=StandardScaler()
    xt=scaler.fit_transform([r["x"] for r in train_rows])
    yy=[math.log1p(r["y"]) for r in train_rows]
    fit=Ridge(alpha=20.0)
    fit.fit(xt,yy)
    spec={"features":list(FEATURES),"mean":scaler.mean_.tolist(),
          "scale":scaler.scale_.tolist(),"intercept":float(fit.intercept_),
          "coef":fit.coef_.tolist()}
    predicted=[forecast(spec,r["x"]) for r in holdout]
    y=[r["y"] for r in holdout]
    baseline_last=[r["seen"]*12 for r in holdout]
    typical=math.expm1(statistics.median(yy))
    baseline_constant=[typical]*len(holdout)
    err=mae_log(predicted,y)
    base=min(mae_log(baseline_last,y),mae_log(baseline_constant,y))
    summary.update({"status":"research_only","model_mae_log":round(err,4),
                    "best_baseline_mae_log":round(base,4),
                    "relative_improvement":round(1-err/base,4) if base>0 else None})
    if base>0 and err<=base*.95:
        summary["status"]="retrospective_pass"
    version=datetime.fromtimestamp(now,timezone.utc).strftime("attention_ridge_%Y%m%d_%H%M%S")
    db.execute("INSERT INTO attention_models VALUES (?,?,?,?,?,?,?,?)",
               (version,now,cutoff,HORIZON,json.dumps(FEATURES),
                json.dumps(spec),json.dumps(summary),summary["status"]))
    db.commit()
    summary["version"]=version
    return summary


def latest_model(db,required_status="retrospective_pass"):
    row=db.execute("SELECT * FROM attention_models WHERE status=? "
                   "ORDER BY created_ts DESC LIMIT 1",(required_status,)).fetchone()
    return dict(row) if row else None
