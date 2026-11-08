"""OSECE causal ordering, identity, real buy/sell stats and forward paper tests."""
import asyncio
from datetime import datetime, timezone
import json
import time

import pytest

from memeradar.attention.db import connect
from memeradar.osece import core, collectors, runner

MINT="7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr"
MINT2="So11111111111111111111111111111111111111112"


def db():
    c=connect(":memory:")
    core.ensure(c)
    return c


def terms(c,now=1000,phrase="Hawk Tuah"):
    assert core.record_event(c,source="google_trends",external_id="g1",text=phrase,
                             actor="google",observed_ts=now,curated=True)==1
    for k in range(3):
        core.record_event(c,source="bluesky",external_id=f"post{k}",actor=f"did:{k}",
                          text=f"the {phrase} meme is here",observed_ts=now+10+k)
    return c


def launch(c,seen=1120,created=None,phrase="Hawk Tuah",mint=MINT):
    return core.record_launch(c,mint=mint,name=phrase,symbol="HAWKTUAH",
                              created_ts=created or seen,observed_ts=seen)


def jup(mint=MINT,price=1.0,liquidity=40000,buy=3000,sell=1500):
    return {"id":mint,"usdPrice":price,"liquidity":liquidity,
            "organicScore":52,"stats5m":{"buyVolume":buy,"sellVolume":sell,
                                       "numTraders":22},
            "audit":{"mintAuthorityDisabled":True,
                     "freezeAuthorityDisabled":True}}


def quote(c,ts,px=1.0,buy=3000,sell=1500):
    assert core.record_jupiter_quote(c,jup(price=px,buy=buy,sell=sell),now=ts)


def safe(c,ts=1300):
    c.execute("INSERT INTO attention_safety_history VALUES (?,?,?,?)",
              (MINT,ts,"passed","[]"))
    c.commit()


def test_term_extraction_is_bounded_and_not_plain_generic_crypto():
    assert "hawktuah" in core.extract_terms("viral Hawk Tuah moment")
    assert "hawktuah" in core.extract_terms("#HawkTuah is trending")
    assert core.extract_terms("bitcoin solana crypto today")==[]
    assert core.normalize("Hawk-Tuah!")=="hawktuah"
    assert len(core.extract_terms(" ".join(f"#ViralAbcd{i}" for i in range(100))))<=12


def test_two_distinct_source_families_create_independent_books():
    c=db()
    core.started_at(c,900)
    terms(c)
    observed=launch(c)
    assert observed=={"status":"matched","sources":2,"signals":2}
    sig=c.execute("SELECT variant,state FROM osece_signals ORDER BY variant").fetchall()
    assert {r["variant"] for r in sig}==set(core.BOOKS)
    assert all(r["state"]=="watch" for r in sig)
    assert c.execute("SELECT COUNT(*) FROM osece_launches").fetchone()[0]==1
    # Duplicate provider event never creates another paper entry.
    assert launch(c)["signals"]==0


def test_single_source_no_duplicate_authors_is_not_faked_collision():
    c=db();core.started_at(c,900)
    for i in range(5):
        core.record_event(c,source="bluesky",external_id=f"post{i}",
                          actor="same-promoter",text="#HawkTuah",observed_ts=1000+i)
    assert launch(c)["status"]=="matched" # source observed, but unqualified
    assert c.execute("SELECT COUNT(*) FROM osece_signals").fetchone()[0]==0
    # One structured source can form the experimental single-source arm.
    core.record_event(c,source="google_trends",external_id="tr",actor="tr",
                      text="Hawk Tuah",observed_ts=1150,curated=True)
    assert launch(c,mint=MINT2,seen=1200)["signals"]==1


def test_future_culture_cannot_become_past_culture():
    c=db();core.started_at(c,900)
    terms(c,now=1150)
    assert launch(c,seen=1200,created=1100)["status"]=="unmatched"
    assert c.execute("SELECT COUNT(*) FROM osece_signals").fetchone()[0]==0


def test_stale_social_timestamp_never_creates_event():
    c=db()
    assert core.record_event(c,source="bluesky",external_id="stale",
                             actor="did:a",text="#HawkTuah",
                             observed_ts=2000,published_ts=400)==0
    assert core.record_event(c,source="bluesky",external_id="future",
                             actor="did:a",text="#HawkTuah",
                             observed_ts=2000,published_ts=2900)==0
    assert c.execute("SELECT COUNT(*) FROM osece_events").fetchone()[0]==0


def test_jupiter_missing_usd_or_safety_is_not_assumed_zero_or_safe(monkeypatch):
    monkeypatch.setenv("OSECE_PAPER_ENABLED","1")
    c=db();core.started_at(c,900);terms(c);launch(c);safe(c)
    q=jup();q["stats5m"].pop("sellVolume")
    core.record_jupiter_quote(c,q,now=1500)
    assert not core._market_entry_ok(c.execute(
        "SELECT * FROM osece_quotes").fetchone())
    q=jup()
    q["audit"].pop("freezeAuthorityDisabled")
    core.record_jupiter_quote(c,q,now=1560)
    assert not core._market_entry_ok(c.execute(
        "SELECT * FROM osece_quotes ORDER BY observed_ts DESC LIMIT 1").fetchone())
    assert core.tick(c,now=1560)["bought"]==0


def test_two_staged_confirmations_and_later_realized_pnl(monkeypatch):
    monkeypatch.setenv("OSECE_PAPER_ENABLED","1")
    c=db();core.started_at(c,900);terms(c);launch(c);safe(c,1300)
    quote(c,1500,1.0)
    t=core.tick(c,now=1500)
    assert t["watching"]==2 and t["bought"]==0
    quote(c,1550,1.02)
    t=core.tick(c,now=1550)
    assert t["queued"]==2 and t["bought"]==0
    quote(c,1590,1.03)
    t=core.tick(c,now=1590)
    assert t["bought"]==2 and t["sold"]==0
    all_trades=c.execute("SELECT * FROM osece_trades").fetchall()
    assert len(all_trades)==2
    assert {r["variant"] for r in all_trades}==set(core.BOOKS)
    assert all(r["entry_ts"]==1590 and r["usd_in"]==10 for r in all_trades)
    quote(c,1620,1.4)
    t=core.tick(c,now=1620)
    assert t["sold"]==2
    report=core.report(c)
    for b in report["books"]:
        assert b["closed"]==1 and b["wins"]==1
        assert b["net_realized_pnl"]>0
        assert b["trades"][0]["exit_utc"] is not None


def test_no_premarket_quote_or_stale_safety_fills(monkeypatch):
    monkeypatch.setenv("OSECE_PAPER_ENABLED","1")
    c=db();core.started_at(c,900);terms(c);launch(c)
    quote(c,1500);core.tick(c,now=1500)
    quote(c,1540);core.tick(c,now=1540)
    quote(c,1580)
    assert core.tick(c,now=1580)["bought"]==0
    assert c.execute("SELECT COUNT(*) FROM osece_trades").fetchone()[0]==0
    assert core.tick(c,now=1700)["bought"]==0


def test_quote_gap_cannot_forge_exit(monkeypatch):
    monkeypatch.setenv("OSECE_PAPER_ENABLED","1")
    c=db();core.started_at(c,900);terms(c);launch(c);safe(c)
    for ts,px in ((1500,1),(1550,1.02),(1590,1.03)):
        quote(c,ts,px);core.tick(c,now=ts)
    assert core.tick(c,now=4000)["sold"]==0
    assert c.execute("SELECT COUNT(*) FROM osece_trades WHERE exit_ts IS NULL").fetchone()[0]==2


def test_bluesky_v1_and_v2_parsing_respects_real_receipt_time():
    now=datetime(2026,11,8,tzinfo=timezone.utc).timestamp()
    stamp=datetime.fromtimestamp(now-12,timezone.utc).isoformat()
    v1={"kind":"commit","did":"did:plc:a",
        "commit":{"operation":"create","collection":"app.bsky.feed.post",
                  "rkey":"1","record":{"text":"#HawkTuah","createdAt":stamp}}}
    assert collectors.read_bluesky(v1,now)["external_id"]=="did:plc:a:1"
    assert collectors.read_bluesky(v1,now+1000) is None
    v2={"payload":{"operation":"create","did":"did:plc:a",
                   "rkey":"2","record":{"text":"#HawkTuah","createdAt":stamp}}}
    assert collectors.read_bluesky(v2,now)["external_id"]=="did:plc:a:2"


def test_rss_and_jupiter_recent_are_timestamped():
    now=2000
    xml='<rss><channel><item><title>Hawk Tuah</title><guid>unique</guid>' \
         '<pubDate>Thu, 01 Jan 1970 00:33:00 GMT</pubDate></item></channel></rss>'
    assert len(collectors.parse_rss(xml,"google_trends",now=now))==1
    assert collectors.parse_rss(xml,"google_trends",now=25000)==[]
    assert collectors._first_pool_time(
        {"firstPool":{"createdAt":"2026-11-08T09:00:00Z"}})>0


def test_paper_alert_once_per_buy_and_sell(monkeypatch):
    monkeypatch.setenv("OSECE_PAPER_ENABLED","1")
    c=db();core.started_at(c,900);terms(c);launch(c);safe(c)
    for ts,px in ((1500,1),(1550,1.02),(1590,1.03)):
        quote(c,ts,px);core.tick(c,now=ts)
    sent=[]
    async def fake_send(http,cfg,message,**kwargs):
        sent.append(message)
    monkeypatch.setattr(runner.alerts,"send_text",fake_send)
    class Cfg:
        ntfy_topic="configured"
        discord_webhook_url=""
        telegram_bot_token=""
        telegram_chat_id=""
    assert asyncio.run(runner.notify(None,c,Cfg()))==2
    assert asyncio.run(runner.notify(None,c,Cfg()))==0
    quote(c,1630,1.4);core.tick(c,now=1630)
    assert asyncio.run(runner.notify(None,c,Cfg()))==2
    assert len(sent)==4
    assert all("no real" in x.lower() for x in sent)


def test_zero_cultural_matches_no_fake_trades(monkeypatch):
    monkeypatch.setenv("OSECE_PAPER_ENABLED","1")
    c=db();core.started_at(c,1000)
    assert launch(c,seen=1200)["status"]=="unmatched"
    assert core.tick(c,now=1300)["bought"]==0
    assert core.report(c)["counts"]["osece_trades"]==0
