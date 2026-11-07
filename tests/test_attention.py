"""Evidence integrity tests for attention collector and research-only execution."""
import asyncio
import sqlite3

import pytest

from memeradar.attention import attention, backtest, chain, db as evidence, model, trader

MINT="7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr"
OTHER="So11111111111111111111111111111111111111112"


def db():
    return evidence.connect(":memory:")


def test_validates_complete_solana_mint_not_ticker_or_evm():
    assert attention.valid_mint(MINT)
    assert attention.valid_mint(OTHER)
    assert not attention.valid_mint("abc")
    assert attention.mints("BUY $COGE, not an address") == set()
    assert attention.mints("0x0123456789abcdef0123456789abcdef01234567")==set()
    assert attention.mints("CA "+MINT+" and "+MINT)=={MINT}


def test_receipt_time_dedupe_future_and_stale_sources():
    c=db()
    assert attention.ingest(c,"telegram","chat:1","CA "+MINT,now=1000,
                            posted_ts=998,engagement=3)==1
    assert attention.ingest(c,"telegram","chat:1","CA "+MINT,now=2000,
                            posted_ts=998,engagement=200)==0
    row=c.execute("SELECT * FROM attention_hits").fetchone()
    assert row["observed_ts"]==1000 and row["engagement"]==3
    assert attention.ingest(c,"x","post2","CA "+MINT,now=2000,posted_ts=100)==0
    assert attention.ingest(c,"x","post3","CA "+MINT,now=2000,posted_ts=2500)==0
    assert evidence.remove_post(c,"telegram","chat:1")==1
    assert not c.execute("SELECT 1 FROM attention_hits").fetchone()


def test_market_parse_only_deep_real_base_pair():
    good={"chainId":"solana","dexId":"raydium","pairAddress":"POOL",
          "baseToken":{"address":MINT},"priceUsd":"0.0003",
          "liquidity":{"usd":30000},"volume":{"m5":10000},
          "txns":{"m5":{"buys":60,"sells":40}},"marketCap":150000}
    wrong=dict(good,baseToken={"address":OTHER})
    weak=dict(good,liquidity={"usd":12000})
    bonding=dict(good,dexId="pumpfun",liquidity={"usd":500000})
    p=chain.best_pair([wrong,weak,bonding,good],MINT)
    assert p is good
    s=chain.parse_snapshot(good,MINT,1234)
    assert s[0]==MINT and s[2]==.0003 and s[8]==6000
    assert chain.parse_snapshot(dict(good,volume={}),MINT,1234) is None


def test_rugcheck_unknown_fails_closed_and_success_audited():
    c=db()
    class Fake:
        async def get(self,url):
            if url.endswith("/report"):
                return {"mint":MINT,"markets":[],"topHolders":[],"risks":[]}
            return []
    assert asyncio.run(chain.check_safety(Fake(),c,MINT,now=3000))=="passed"
    row=c.execute("SELECT * FROM attention_safety_history").fetchone()
    assert row["checked_ts"]==3000 and row["state"]=="passed"
    assert backtest.eligible(c,MINT,2999,50000) is False
    assert backtest.eligible(c,MINT,3500,50000) is True
    assert backtest.eligible(c,MINT,7001,50000) is False
    assert asyncio.run(chain.check_safety(Fake(),c,MINT,now=7000))=="passed"
    assert len(c.execute("SELECT * FROM attention_safety_history").fetchall())==2


def test_future_coverage_requires_complete_sequence():
    rows=[{"observed_ts":1000+i*30,"buy_volume_proxy_m5":500}
          for i in range(1,121)]
    assert backtest.buy_arrived(rows,1000,4600)==6000
    assert model.future_proxy(rows,1000)==pytest.approx(6000)
    assert model.future_proxy(rows[:10],1000) is None
    assert model.future_proxy(rows[2:],1000) is None # first gap 90 is permitted, but partial < horizon


def test_no_model_means_no_fabricated_paper_trades():
    c=db()
    assert model.train(c,now=100000)["status"]=="insufficient_data"
    assert trader.paper_tick(c,now=100000)["status"]=="data_collection_only"
    assert backtest.simulate(c,"missing",since=1,until=1000)["status"]=="blocked"
    assert c.execute("SELECT count(*) FROM attention_paper_trades").fetchone()[0]==0


def test_round_trip_cost_and_delayed_signal_safety():
    assert 0 < backtest.realized_usd(1,1) < 10
    assert backtest.percent_return(1,.65)<-30
    c=db()
    evidence.record_hit(c,source="telegram",post_id="s",mint=MINT,observed_ts=1000,
                        posted_ts=999,engagement=50)
    c.execute("INSERT INTO attention_chain VALUES (?,?,?,?,?,?,?,?,?,?)",
              (MINT,1000,1,20000,100000,100000,60,40,60000,"pool"))
    c.execute("INSERT INTO attention_safety_history VALUES (?,?,?,?)",
              (MINT,1010,"passed","[]"))
    c.commit()
    spec={"intercept":12,"coef":[0]*9,"mean":[0]*9,"scale":[1]*9}
    found,_=backtest.signals(c,spec,1000,1001)
    assert not found # checked only after the signal: no look-ahead


@pytest.mark.parametrize("source",["x","telegram","reddit"])
def test_missing_credentials_do_not_create_social_signal(source,monkeypatch):
    c=db()
    if source=="x":
        monkeypatch.delenv("ATTENTION_X_BEARER_TOKEN",raising=False)
        class H:
            async def get(self,*a,**k):
                raise AssertionError("unexpected paid read")
        assert asyncio.run(attention.poll_x(H(),c))==0
    elif source=="reddit":
        monkeypatch.delenv("ATTENTION_REDDIT_APPROVED",raising=False)
        class H:
            async def get(self,*a,**k):
                raise AssertionError("unexpected API read")
        assert asyncio.run(attention.poll_reddit(H(),c))==0
    else:
        monkeypatch.delenv("ATTENTION_TELEGRAM_SESSION",raising=False)
        asyncio.run(attention.listen_telegram(c))
    assert c.execute("SELECT COUNT(*) FROM attention_hits").fetchone()[0]==0
