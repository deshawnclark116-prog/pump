"""Stress-Survivor forward-paper tests: cross-sectional control and safety."""
import sqlite3

import pytest

from memeradar.attention import db as evidence
from memeradar.attention import stress_survivor as stress

MINT="7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr"
# Real 32-byte base58 test fixtures, deliberately NOT ticker-only guesses.
PEERS=[f"peer_{i}" for i in range(14)]


def db():
    c=evidence.connect(":memory:")
    stress.ensure(c)
    return c


def quote(c,mint,ts,price,liq=100000,mcap=250000,b=10,s=20,vol=3000):
    c.execute(
        "INSERT INTO attention_chain VALUES (?,?,?,?,?,?,?,?,?,?)",
        (mint,ts,price,liq,mcap,vol,b,s,vol*b/(b+s),"pool"))
    c.commit()


def safety(c,ts=1040):
    c.execute("INSERT INTO attention_safety_history VALUES (?,?,?,?)",
              (MINT,ts,"passed","[]"))
    c.commit()


def set_up_selloff(c):
    # Cohort baselines around T-5 minutes, and two genuine future snapshots.
    for mint in [MINT]+PEERS:
        quote(c,mint,710,1)
        quote(c,mint,800,1)
        quote(c,mint,1050,1 if mint==MINT else 0.98)
        quote(c,mint,1120,1 if mint==MINT else 0.981)


def get_trades(c):
    return [dict(r) for r in c.execute("SELECT * FROM stress_survivor_trades")]


def test_default_disabled_without_activation(monkeypatch):
    monkeypatch.delenv("STRESS_SURVIVOR_PAPER_ENABLED",raising=False)
    c=db()
    assert stress.paper_tick(c,now=1000)=={"status":"disabled"}
    assert not c.execute("SELECT 1 FROM stress_survivor_meta").fetchone()


def test_peer_selloff_survivor_not_same_quote_fill(monkeypatch):
    monkeypatch.setenv("STRESS_SURVIVOR_PAPER_ENABLED","1")
    c=db()
    assert stress.paper_tick(c,now=1000)["entered"]==0
    set_up_selloff(c);safety(c)
    a=stress.paper_tick(c,now=1050)
    assert a["cohort"]>=14 and a["watched"]==1 and a["entered"]==0
    signal=c.execute("SELECT * FROM stress_survivor_signals").fetchone()
    assert signal["peer_median_ret5"]<=-.018
    assert signal["sells_m5"]>signal["buys_m5"]
    assert signal["state"]=="watch"
    assert stress.paper_tick(c,now=1120)["confirmed"]==1
    assert get_trades(c)==[]
    quote(c,MINT,1150,1.01)
    assert stress.paper_tick(c,now=1150)["entered"]==1
    assert len(get_trades(c))==1
    assert get_trades(c)[0]["entry_ts"]==1150
    # Entry was 1.01: a 1.18 exit is <12% NET after both fees/slippage.
    quote(c,MINT,1180,1.19)
    assert stress.paper_tick(c,now=1180)["closed"]==1
    t=get_trades(c)[0]
    assert t["exit_reason"]=="TAKE_NET_12"
    assert t["usd_out"]>t["usd_in"]
    report=stress.report(c)
    assert report["closed"]==1 and report["wins"]==1
    assert report["trades"][0]["peer_median_5m_pct"]<0


def test_peer_cohort_weakness_is_required(monkeypatch):
    monkeypatch.setenv("STRESS_SURVIVOR_PAPER_ENABLED","1")
    c=db();stress.paper_tick(c,now=1000)
    # All peers flat, one coin survives, no stress: zero entries and zero watch.
    for mint in [MINT]+PEERS:
        quote(c,mint,710,1)
        quote(c,mint,1050,1)
    safety(c)
    assert stress.paper_tick(c,now=1050)["watched"]==0
    assert get_trades(c)==[]


def test_buy_dominant_coin_is_not_a_resilience_signal(monkeypatch):
    monkeypatch.setenv("STRESS_SURVIVOR_PAPER_ENABLED","1")
    c=db();stress.paper_tick(c,now=1000);set_up_selloff(c);safety(c)
    # Replacing the candidate with 80 buys and five sells is buyer momentum,
    # explicitly NOT the sell-resilience condition.
    c.execute("UPDATE attention_chain SET buys_m5=80,sells_m5=5 "
              "WHERE mint=? AND observed_ts=1050",(MINT,));c.commit()
    assert stress.paper_tick(c,now=1050)["watched"]==0


def test_no_safety_pass_or_lost_liquidity(monkeypatch):
    monkeypatch.setenv("STRESS_SURVIVOR_PAPER_ENABLED","1")
    c=db();stress.paper_tick(c,now=1000);set_up_selloff(c)
    assert stress.paper_tick(c,now=1050)["watched"]==0
    safety(c);quote(c,MINT,1070,1.0)
    assert stress.paper_tick(c,now=1070)["watched"]==1
    # Next quote collapses pool liquidity: cannot become a paper trade.
    quote(c,MINT,1140,1.0,liq=60000)
    assert stress.paper_tick(c,now=1140)["confirmed"]==0
    assert get_trades(c)==[]


def test_no_stale_or_future_quotes(monkeypatch):
    monkeypatch.setenv("STRESS_SURVIVOR_PAPER_ENABLED","1")
    c=db();stress.paper_tick(c,now=1000);set_up_selloff(c);safety(c)
    stress.paper_tick(c,now=1050)
    assert stress.paper_tick(c,now=1400)["confirmed"]==0
    assert c.execute("SELECT state FROM stress_survivor_signals").fetchone()[0]=="expired"
    assert get_trades(c)==[]


def test_stop_gap_and_cooldown_no_fake_wins(monkeypatch):
    monkeypatch.setenv("STRESS_SURVIVOR_PAPER_ENABLED","1")
    c=db();stress.paper_tick(c,now=1000);set_up_selloff(c);safety(c)
    stress.paper_tick(c,now=1050)
    stress.paper_tick(c,now=1120)
    quote(c,MINT,1150,1.0)
    stress.paper_tick(c,now=1150)
    quote(c,MINT,1180,.80)
    assert stress.paper_tick(c,now=1180)["closed"]==1
    assert get_trades(c)[0]["exit_reason"]=="STOP_NET_10"
    assert stress.report(c)["losses"]==1
    # Repeated selloff cannot re-enter the same mint inside 4 hours.
    quote(c,MINT,1200,1.0)
    assert stress.paper_tick(c,now=1200)["watched"]==0
    assert len(get_trades(c))==1


def test_midnight_and_runner_restart_do_not_backfill(monkeypatch):
    monkeypatch.setenv("STRESS_SURVIVOR_PAPER_ENABLED","1")
    c=db()
    set_up_selloff(c);safety(c)
    # Historical evidence is allowed to supply a *lookback*, but it may not
    # be re-labeled as a new forward signal when the strategy starts later.
    stress.paper_tick(c,now=1300)
    assert c.execute("SELECT COUNT(*) FROM stress_survivor_signals").fetchone()[0]==0
    assert stress.paper_tick(c,now=1301)["watched"]==0
    assert get_trades(c)==[]
