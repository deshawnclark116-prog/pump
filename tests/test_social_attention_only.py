"""Trading retirement guards: only Social Attention can make new fake buys.

Retired books preserve old positions and close only on fresh future quotes.
"""
import asyncio

from memeradar.attention import db as evidence, explore, stress_survivor
from memeradar.osece import core as osece
from memeradar.pipeline import Radar
from memeradar.config import Config

MINT = "7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr"


def memory():
    return evidence.connect(":memory:")


def chain(c, ts, price=1.0):
    c.execute(
        "INSERT INTO attention_chain VALUES (?,?,?,?,?,?,?,?,?,?)",
        (MINT,ts,price,100000,300000,10000,50,20,7000,"pool"))
    c.commit()


def safety(c, ts):
    c.execute("INSERT INTO attention_safety_history VALUES (?,?,?,?)",
              (MINT,ts,"passed","[]"))
    c.commit()


def test_only_social_accepts_new_entries_and_old_gecko_queue_is_retired(monkeypatch):
    monkeypatch.setenv("ATTENTION_EXPERIMENTAL_PAPER","1")
    monkeypatch.setenv("ATTENTION_EXPERIMENTAL_SOCIAL_ONLY","1")
    c=memory();explore.ensure(c)
    explore.paper_tick(c,now=1000)
    c.execute(
        "INSERT INTO attention_explore_signals "
        "(variant,mint,source,source_ts,signal_ts,signal_price,"
        "signal_liquidity,buy_proxy_m5,buyers_m5,sellers_m5,"
        "source_rank,status,details) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (explore.VERSIONS[1],MINT,"geckoterminal_trending",1005,1010,
         1.0,100000,7000,50,20,1,"queued","{}"))
    safety(c,1020)
    chain(c,1040)
    assert explore.paper_tick(c,now=1040)["entries"]==0
    assert c.execute(
        "SELECT status FROM attention_explore_signals WHERE variant=?",
        (explore.VERSIONS[1],)).fetchone()[0]=="retired_no_entry"

    # A new public social post with an exact mint still produces a real
    # forward PAPER receipt and a delayed simulated fill.
    evidence.record_hit(c,source="bluesky",post_id="test",mint=MINT,
                        observed_ts=1045,posted_ts=1040)
    chain(c,1050)
    r=explore.paper_tick(c,now=1050)
    assert r["queued"]==1 and r["entries"]==0
    chain(c,1090,1.01)
    assert explore.paper_tick(c,now=1090)["entries"]==1
    assert c.execute("SELECT variant FROM attention_explore_trades").fetchone()[0] == explore.VERSIONS[0]


def test_gecko_old_open_position_can_exit_after_retirement(monkeypatch):
    monkeypatch.setenv("ATTENTION_EXPERIMENTAL_PAPER","1")
    monkeypatch.setenv("ATTENTION_EXPERIMENTAL_SOCIAL_ONLY","1")
    c=memory();explore.ensure(c)
    c.execute(
        "INSERT INTO attention_explore_signals "
        "(variant,mint,source,source_ts,signal_ts,signal_price,"
        "signal_liquidity,buy_proxy_m5,buyers_m5,sellers_m5,"
        "source_rank,status,details) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (explore.VERSIONS[1],MINT,"geckoterminal_trending",980,990,
         1.0,100000,7000,50,20,1,"paper_bought","{}"))
    sid=c.execute("SELECT id FROM attention_explore_signals").fetchone()[0]
    c.execute(
        "INSERT INTO attention_explore_trades "
        "(variant,mint,source,signal_id,entry_ts,entry_price,usd_in)"
        " VALUES (?,?,?,?,?,?,?)",
        (explore.VERSIONS[1],MINT,"geckoterminal_trending",sid,1000,1.0,10))
    chain(c,1050,1.5)
    assert explore.paper_tick(c,now=1050)["exits"]==1
    x=c.execute("SELECT usd_out FROM attention_explore_trades").fetchone()[0]
    assert x>10


def test_stress_book_retired_can_close_but_not_fill_queued(monkeypatch):
    monkeypatch.setenv("STRESS_SURVIVOR_PAPER_ENABLED","0")
    monkeypatch.setenv("STRESS_SURVIVOR_EXIT_ONLY","1")
    c=memory();stress_survivor.ensure(c)
    c.execute(
        "INSERT INTO stress_survivor_signals "
        "(version,mint,watch_ts,watch_price,watch_liquidity,token_ret5,"
        "peer_median_ret5,peer_down_fraction,peer_count,buys_m5,sells_m5,state)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?,'queued')",
        (stress_survivor.VERSION,MINT,1000,1.0,100000,0,-.02,.8,12,10,20))
    sid=c.execute("SELECT id FROM stress_survivor_signals").fetchone()[0]
    c.execute(
        "INSERT INTO stress_survivor_trades "
        "(version,signal_id,mint,entry_ts,entry_price,entry_liquidity,usd_in)"
        " VALUES (?,?,?,?,?,?,?)",
        (stress_survivor.VERSION,sid,MINT,1000,1.0,100000,10))
    chain(c,1050,1.4)
    outcome=stress_survivor.paper_tick(c,now=1050)
    assert outcome["status"]=="retired_exit_only"
    assert outcome["closed"]==1 and outcome["entered"]==0
    assert c.execute("SELECT state FROM stress_survivor_signals").fetchone()[0]=="retired_no_entry"


def test_osece_retired_book_closes_old_positions_and_never_buys(monkeypatch):
    monkeypatch.setenv("OSECE_PAPER_ENABLED","0")
    monkeypatch.setenv("OSECE_EXIT_ONLY","1")
    c=memory();osece.ensure(c)
    c.execute(
        "INSERT INTO osece_signals "
        "(variant,mint,launch_ts,term_key,source_count,state) "
        "VALUES (?,?,?,?,?,'queued')",
        (osece.SINGLE,MINT,980,"hawktuah",1))
    sid=c.execute("SELECT id FROM osece_signals").fetchone()[0]
    c.execute(
        "INSERT INTO osece_trades "
        "(variant,signal_id,mint,entry_ts,entry_price,entry_liquidity,usd_in)"
        " VALUES (?,?,?,?,?,?,?)",
        (osece.SINGLE,sid,MINT,1000,1.0,100000,10))
    c.execute(
        "INSERT INTO osece_quotes "
        "(mint,observed_ts,price,liquidity,buy_usd_m5,sell_usd_m5,"
        "organic_score,num_traders,audit_ok) VALUES (?,?,?,?,?,?,?,?,?)",
        (MINT,1050,1.4,100000,5000,1000,50,30,1))
    c.commit()
    outcome=osece.tick(c,now=1050)
    assert outcome["status"]=="retired_exit_only"
    assert outcome["sold"]==1 and outcome["bought"]==0
    assert c.execute("SELECT state FROM osece_signals").fetchone()[0]=="retired_no_entry"


def test_main_radar_blocks_fast_entries_before_price_or_alert(monkeypatch,tmp_path):
    monkeypatch.setenv("SOCIAL_ATTENTION_ONLY","1")
    monkeypatch.delenv("OSECE_ENABLED",raising=False)
    radar=Radar(Config(db_path=str(tmp_path/"social_only.db")))
    assert radar.social_attention_only
    assert asyncio.run(radar._enter(None,1000,"fast")) is False
    assert asyncio.run(radar._enter(None,1000,"steady")) is False
    assert radar.store.db.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]>0
