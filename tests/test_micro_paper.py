"""Regression tests for eight real 25-dollar PAPER accounts and live status."""
from __future__ import annotations

import asyncio
import json

import pytest

from memeradar.attention.db import connect
from memeradar.attention import explore
from memeradar.osece import core
from memeradar import micro_paper as m
from memeradar import micro_dashboard as dash

MINT="7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr"
MINT2="So11111111111111111111111111111111111111112"


def db():
    d=connect(":memory:")
    m.ensure(d);explore.ensure(d);core.ensure(d)
    m.activated(d,1000)
    return d


def social_quote(d,ts,price=1.0,mint=MINT):
    d.execute(
        "INSERT INTO attention_chain "
        "(mint,observed_ts,price_usd,liquidity_usd,market_cap,vol_m5,"
        "buys_m5,sells_m5,buy_volume_proxy_m5,pair) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (mint,ts,price,60000,80000,3000,20,10,2000,"pair"))
    d.commit()


def safety(d,mint=MINT):
    d.execute("INSERT INTO attention_safety_history VALUES (?,?,?,?)",
              (mint,1050,"passed","[]"))
    d.commit()


def jupiter_quote(d,ts,price=1.0,mint=MINT,liq=9000,score=0):
    core.record_jupiter_quote(
        d,{"id":mint,"usdPrice":price,"liquidity":liq,"organicScore":score,
           "stats5m":{"buyVolume":1500,"sellVolume":600,"numTraders":20},
           "audit":{"mintAuthorityDisabled":True,"freezeAuthorityDisabled":True}},
        now=ts)


def test_two_independent_25_balances_and_actual_fixed_costs(monkeypatch):
    monkeypatch.setenv("MICRO_PAPER_ENABLED","1")
    d=db();safety(d)
    assert m.mark_ready(d,model=m.SOCIAL,mint=MINT,
                        source_signal_ts=1010,ready_ts=1070,ready_price=1.0)==2
    social_quote(d,1080,1.0)
    assert m.tick(d,now=1080)["bought"]==0  # no same-observation fill
    social_quote(d,1105,1.01)
    assert m.tick(d,now=1105)["bought"]==2
    assert round(m.cash(d,m.SOCIAL,1),3)==24
    assert round(m.cash(d,m.SOCIAL,5),3)==20
    assert m.open_count(d,m.SOCIAL,1)==1
    assert m.open_count(d,m.SOCIAL,5)==1
    assert m.proceeds(1,1,1)<m.proceeds(1,1,5)/5 # $1 suffers bigger fixed cost percentage
    social_quote(d,1140,1.5)
    assert m.tick(d,now=1140)["sold"]==2
    r=m.report(d,now=1140)
    social=[a for a in r["accounts"] if a["model"]==m.SOCIAL]
    assert len(social)==2
    assert all(a["open"]==0 and a["closed"]==1 and a["wins"]==1 for a in social)
    assert all(a["cash_usd"]>25 for a in social)
    assert social[0]["net_realized_pnl_usd"]!=social[1]["net_realized_pnl_usd"]
    assert all(a["starting_cash_usd"]==25 for a in r["accounts"])


def test_all_four_models_generate_separate_intents(monkeypatch):
    monkeypatch.setenv("MICRO_PAPER_ENABLED","1")
    d=db()
    for model in m.MODELS:
        assert m.mark_ready(d,model=model,mint=MINT,
                            source_signal_ts=1010,ready_ts=1100,ready_price=1)==2
    assert d.execute("SELECT COUNT(*) FROM micro_intents").fetchone()[0]==8
    assert m.report(d)["accounts"][0]["cash_usd"]==25


def test_risk_checks_and_no_false_prices(monkeypatch):
    monkeypatch.setenv("MICRO_PAPER_ENABLED","1")
    d=db()
    m.mark_ready(d,model=m.SOCIAL,mint=MINT,source_signal_ts=1010,
                 ready_ts=1100,ready_price=1)
    social_quote(d,1150,1)
    assert m.tick(d,1150)["bought"]==0  # no RugCheck = no fabricated buy
    safety(d)
    social_quote(d,1170,1.3)
    assert m.tick(d,1170)["bought"]==0 # +30% chase
    assert d.execute("SELECT COUNT(*) FROM micro_intents WHERE status='open'").fetchone()[0]==0


def test_osece_early_liquidity_and_original_different_market_gates(monkeypatch):
    monkeypatch.setenv("MICRO_PAPER_ENABLED","1")
    d=db();safety(d)
    for model in (m.OSECE_LIQ,m.OSECE_SINGLE,m.OSECE_COLLISION):
        m.mark_ready(d,model=model,mint=MINT,source_signal_ts=1020,
                     ready_ts=1100,ready_price=1)
    jupiter_quote(d,1130,1.01,liq=9000,score=0)
    assert m.tick(d,1130)["bought"]==2
    assert d.execute("SELECT DISTINCT model FROM micro_intents WHERE status='open'").fetchall()[0][0]==m.OSECE_LIQ
    assert m.open_count(d,m.OSECE_SINGLE,1)==0
    assert m.open_count(d,m.OSECE_COLLISION,5)==0


def test_accounts_never_borrow_cash_or_top_up(monkeypatch):
    monkeypatch.setenv("MICRO_PAPER_ENABLED","1")
    d=db();safety(d)
    for i in range(7):
        mint=f"{i+1}"*44
        # Only manually create old observed positions to test the cash ledger.
        d.execute(
            "INSERT INTO micro_intents "
            "(model,stake,mint,source_signal_ts,ready_ts,ready_price,status,"
            "entry_ts,entry_price,entry_liquidity) "
            "VALUES (?,?,?, ?,?,?, 'open',?,?,?)",
            (m.SOCIAL,5,mint,1010+i,1100+i,1.0,1200+i,1.0,20000))
    d.commit()
    assert m.cash(d,m.SOCIAL,5)==-10  # detected overcommit; no negative-cash borrowing
    m.mark_ready(d,model=m.SOCIAL,mint=MINT,source_signal_ts=1010,
                 ready_ts=1100,ready_price=1)
    social_quote(d,1130)
    assert m.tick(d,1130)["bought"]==0


def test_no_backdated_micro_orders(monkeypatch):
    monkeypatch.setenv("MICRO_PAPER_ENABLED","1")
    d=db()
    assert m.mark_ready(d,model=m.SOCIAL,mint=MINT,
                        source_signal_ts=980,ready_ts=1100,ready_price=1)==0
    assert d.execute("SELECT COUNT(*) FROM micro_intents").fetchone()[0]==0


def test_dashboard_page_and_create_update(monkeypatch):
    d=db()
    page=dash.markdown(m.report(d,now=1100),run_id="77",repo="owner/repo")
    assert "$25.00" in page
    assert "Social Attention" in page and "OSECE Early Liquidity" in page
    assert "No qualifying trades yet" in page
    assert "on-chain" in page.lower()
    calls=[]
    def fake_request(url,token,method="GET",data=None):
        calls.append((method,url))
        if method=="GET":
            return []
        return {"id":123}
    monkeypatch.setattr(dash,"_request",fake_request)
    assert dash.publish(page,repo="owner/repo",token="fake")["status"]=="created"
    assert calls[-1][0]=="POST"
    def existing(url,token,method="GET",data=None):
        calls.append((method,url))
        if method=="GET":
            return [{"url":"https://api.github.com/comments/7",
                     "body":dash.MARKER+" previous","id":7}]
        return {}
    monkeypatch.setattr(dash,"_request",existing)
    assert dash.publish(page,repo="owner/repo",token="fake")["status"]=="updated"
    assert calls[-1][0]=="PATCH"
