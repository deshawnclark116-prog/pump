"""Forward experimental paper-trading invariants: no future data or fabricated fills."""
import json

import pytest

from memeradar.attention import attention, db as evidence, explore

MINT="7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr"


def db():
    return evidence.connect(":memory:")


def price(c, ts, px=1.0, liq=100000, vol=10000, buys=50, sells=25):
    c.execute("INSERT INTO attention_chain VALUES (?,?,?,?,?,?,?,?,?,?)",
              (MINT,ts,px,liq,250000,vol,buys,sells,
               vol*buys/(buys+sells),"pool"))
    c.commit()


def passed(c, when):
    c.execute("INSERT INTO attention_safety_history VALUES (?,?,?,?)",
              (MINT,when,"passed","[]"))
    c.commit()


def trend(c, when, rank=4):
    evidence.record_trend(c,source="geckoterminal_trending",source_id="pool",
                          observed_ts=when,mint=MINT,rank=rank)


def hit(c,when):
    assert attention.ingest(c,"bluesky","at://example/"+str(when),
                            MINT,now=when,posted_ts=when-5)==1


def trades(c):
    return [dict(r) for r in c.execute(
        "SELECT * FROM attention_explore_trades ORDER BY entry_ts,variant")]


def test_experiment_disabled_by_default(monkeypatch):
    monkeypatch.delenv("ATTENTION_EXPERIMENTAL_PAPER",raising=False)
    c=db()
    result=explore.paper_tick(c,now=1000)
    assert result["status"]=="disabled"
    assert not c.execute(
        "SELECT name FROM sqlite_master WHERE name='attention_explore_trades'").fetchone()


def test_first_signal_is_not_a_same_tick_fill_and_profit_is_net(monkeypatch):
    monkeypatch.setenv("ATTENTION_EXPERIMENTAL_PAPER","1")
    c=db()
    assert explore.paper_tick(c,now=1000)["entries"]==0
    trend(c,1010)
    passed(c,1035)
    price(c,1040)
    assert explore.paper_tick(c,now=1040)=={
        "status":"untrained_exploratory_paper","queued":1,"entries":0,"exits":0}
    assert trades(c)==[]
    price(c,1070,px=1.01)
    step=explore.paper_tick(c,now=1070)
    assert step["entries"]==1
    assert len(trades(c))==1 and trades(c)[0]["entry_ts"]==1070
    assert trades(c)[0]["exit_ts"] is None
    price(c,1100,px=1.30)
    assert explore.paper_tick(c,now=1100)["exits"]==1
    t=trades(c)[0]
    assert t["reason"]=="TAKE_PROFIT_NET_20"
    assert 1.7<t["usd_out"]-t["usd_in"]<2.8  # after both sides' fees+slippage
    assert c.execute("SELECT COUNT(*) FROM attention_paper_trades").fetchone()[0]==0
    rep=explore.report(c)
    trendbook=next(r for r in rep["books"] if r["model"]==explore.VERSIONS[1])
    assert trendbook["wins"]==1 and trendbook["closed"]==1
    assert trendbook["trades"][0]["mint"]==MINT
    assert rep["status"]=="exploratory_forward_paper_not_validated"


def test_social_and_trending_get_independent_paper_ledgers(monkeypatch):
    monkeypatch.setenv("ATTENTION_EXPERIMENTAL_PAPER","1")
    c=db()
    explore.paper_tick(c,now=1000)
    trend(c,1010)
    hit(c,1010)
    passed(c,1030)
    price(c,1040)
    assert explore.paper_tick(c,now=1040)["queued"]==2
    price(c,1070,1.0)
    assert explore.paper_tick(c,now=1070)["entries"]==2
    versions={r["variant"] for r in trades(c)}
    assert versions==set(explore.VERSIONS)
    assert sum(explore._cash(c,v) for v in explore.VERSIONS)==480


def test_prelaunch_source_does_not_generate_pseudo_forward_trade(monkeypatch):
    monkeypatch.setenv("ATTENTION_EXPERIMENTAL_PAPER","1")
    c=db()
    trend(c,900)
    passed(c,950)
    price(c,1020)
    assert explore.paper_tick(c,now=1000)["queued"]==0
    assert explore.paper_tick(c,now=1020)["queued"]==0
    assert trades(c)==[]
    trend(c,1030)
    price(c,1040)
    assert explore.paper_tick(c,now=1040)["queued"]==1


def test_missing_safety_and_missing_followup_quotes_never_fill(monkeypatch):
    monkeypatch.setenv("ATTENTION_EXPERIMENTAL_PAPER","1")
    c=db()
    explore.paper_tick(c,now=1000)
    hit(c,1010)
    price(c,1040)
    assert explore.paper_tick(c,now=1040)["queued"]==0
    passed(c,1045)
    price(c,1070)
    assert explore.paper_tick(c,now=1070)["queued"]==1
    assert explore.paper_tick(c,now=1280)["entries"]==0
    assert trades(c)==[]
    assert c.execute("SELECT status FROM attention_explore_signals").fetchone()[0]=="expired"


def test_ambiguous_ticker_never_creates_trade(monkeypatch):
    monkeypatch.setenv("ATTENTION_EXPERIMENTAL_PAPER","1")
    c=db()
    explore.paper_tick(c,now=1000)
    assert attention.ingest(c,"bluesky","ticker","$PUMP to the moon",
                            posted_ts=1005,now=1010)==0
    passed(c,1020);price(c,1040)
    assert explore.paper_tick(c,now=1040)["queued"]==0
    assert trades(c)==[]


def test_real_net_stop_cannot_be_marked_as_a_win(monkeypatch):
    monkeypatch.setenv("ATTENTION_EXPERIMENTAL_PAPER","1")
    c=db()
    explore.paper_tick(c,now=1000)
    trend(c,1010);passed(c,1030);price(c,1040)
    explore.paper_tick(c,now=1040)
    price(c,1070,1.0);explore.paper_tick(c,now=1070)
    price(c,1100,0.76)
    assert explore.paper_tick(c,now=1100)["exits"]==1
    assert trades(c)[0]["reason"]=="STOP_LOSS_NET_20"
    assert trades(c)[0]["usd_out"]<trades(c)[0]["usd_in"]
    assert explore.report(c)["books"][1]["losses"]==1


def test_no_new_trade_on_duplicate_source_and_enforce_cooldown(monkeypatch):
    monkeypatch.setenv("ATTENTION_EXPERIMENTAL_PAPER","1")
    c=db();explore.paper_tick(c,now=1000)
    trend(c,1010);passed(c,1030);price(c,1040)
    explore.paper_tick(c,now=1040)
    price(c,1070);explore.paper_tick(c,now=1070)
    price(c,1100,1.3);explore.paper_tick(c,now=1100)
    # New trending observation, but the coin is under its one-hour cooldown.
    trend(c,1130);price(c,1160,1.2)
    assert explore.paper_tick(c,now=1160)["queued"]==0
    assert len(trades(c))==1


def test_stale_quote_cannot_close_position_even_after_time_limit(monkeypatch):
    monkeypatch.setenv("ATTENTION_EXPERIMENTAL_PAPER","1")
    c=db();explore.paper_tick(c,now=1000)
    trend(c,1010);passed(c,1030);price(c,1040)
    explore.paper_tick(c,now=1040)
    price(c,1070);explore.paper_tick(c,now=1070)
    assert explore.paper_tick(c,now=5000)["exits"]==0
    assert trades(c)[0]["exit_ts"] is None


def test_phone_notifications_cover_buy_and_sell_once_even_after_restart(monkeypatch):
    import asyncio
    from memeradar.attention import runner
    monkeypatch.setenv("ATTENTION_EXPERIMENTAL_PAPER","1")
    sent=[]
    async def send_text(http,cfg,message,**kw):
        sent.append((message,kw))
    monkeypatch.setattr(runner.alerts,"send_text",send_text)
    class Cfg:
        ntfy_topic="configured"
        discord_webhook_url=""
        telegram_bot_token=""
        telegram_chat_id=""
    c=db()
    explore.paper_tick(c,now=1000)
    hit(c,1010)
    passed(c,1030)
    price(c,1040)
    explore.paper_tick(c,now=1040)
    price(c,1070,1.0)
    explore.paper_tick(c,now=1070)
    assert asyncio.run(runner.notify_experimental_trades(None,c,Cfg()))==1
    assert "PAPER BUY" in sent[0][0] and "bluesky" in sent[0][0]
    assert asyncio.run(runner.notify_experimental_trades(None,c,Cfg()))==0
    price(c,1100,1.30)
    explore.paper_tick(c,now=1100)
    assert asyncio.run(runner.notify_experimental_trades(None,c,Cfg()))==1
    assert "PAPER SELL" in sent[1][0] and "Net P&L" in sent[1][0]
    assert asyncio.run(runner.notify_experimental_trades(None,c,Cfg()))==0
    assert c.execute("SELECT COUNT(*) FROM attention_explore_notifications").fetchone()[0]==2
