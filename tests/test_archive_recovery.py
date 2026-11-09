"""Regression: recover a dropped run despite forked autoincrement SQLite IDs."""
from memeradar.attention.db import connect
from memeradar.attention import explore
from memeradar.attention.archive_recovery import reconcile
from memeradar.osece import core

MINT1="7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr"
MINT2="So11111111111111111111111111111111111111112"


def db():
    c=connect(":memory:")
    explore.ensure(c)
    core.ensure(c)
    return c


def social(c,mint,entry,closed=True):
    cur=c.execute(
        "INSERT INTO attention_explore_signals "
        "(variant,mint,source,source_ts,signal_ts,signal_price,"
        "signal_liquidity,buy_proxy_m5,buyers_m5,sellers_m5,"
        "source_rank,status,details) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (explore.VERSIONS[0],mint,"bluesky",entry-60,entry-40,
         1,40000,1500,20,15,1,"paper_bought","{}"))
    c.execute(
        "INSERT INTO attention_explore_trades "
        "(variant,mint,source,signal_id,entry_ts,entry_price,usd_in,"
        "exit_ts,exit_price,usd_out,reason) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (explore.VERSIONS[0],mint,"bluesky",cur.lastrowid,
         entry,1,10,entry+180 if closed else None,
         .6 if closed else None,5.7 if closed else None,
         "STOP_LOSS_NET_20" if closed else None))
    c.commit()


def osece_trade(c,mint,closed=True):
    c.execute(
        "INSERT INTO osece_launches "
        "(mint,launch_source,name,symbol,created_ts,observed_ts,"
        "term_key,sources_json,source_count,evidence_count) "
        "VALUES (?,?,?,?,?,?,?,?,?,?)",
        (mint,"pumpportal","Hawk Tuah","HAWKTUAH",1000,1000,
         "hawktuah",'{"bluesky":3}',1,3))
    cur=c.execute(
        "INSERT INTO osece_signals "
        "(variant,mint,launch_ts,term_key,source_count,state) "
        "VALUES (?,?,?,?,?,?)",(core.EARLY_LIQ,mint,1000,"hawktuah",1,"paper_bought"))
    c.execute(
        "INSERT INTO osece_trades "
        "(variant,signal_id,mint,entry_ts,entry_price,entry_liquidity,"
        "usd_in,exit_ts,exit_price,usd_out,exit_reason)"
        " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (core.EARLY_LIQ,cur.lastrowid,mint,1100,1,9500,10,
         1300 if closed else None,1.4 if closed else None,
         13.2 if closed else None,"TAKE_NET_20" if closed else None))
    c.commit()


def test_social_recover_when_same_autoincrement_ids_belong_to_different_mints():
    src=db();live=db()
    social(src,MINT1,1200)
    social(live,MINT2,1250) # both first rows have id=1
    out=reconcile(live,src,"run77")
    assert out["social_added"]==1
    rows=live.execute("SELECT mint,entry_ts,usd_out FROM attention_explore_trades "
                      "ORDER BY entry_ts").fetchall()
    assert len(rows)==2
    assert [r["mint"] for r in rows]==[MINT1,MINT2]
    assert all(r["usd_out"]==5.7 for r in rows)
    assert live.execute(
        "SELECT COUNT(*) FROM attention_explore_notifications").fetchone()[0]==2
    assert reconcile(live,src,"run77")["skipped"]=="already imported"
    assert live.execute(
        "SELECT COUNT(*) FROM attention_explore_trades").fetchone()[0]==2


def test_existing_open_social_position_gets_archived_exit_without_duplicate_entry():
    src=db();live=db()
    social(src,MINT1,1200,closed=True)
    social(live,MINT1,1200,closed=False)
    out=reconcile(live,src,"oldclosed")
    assert out["social_added"]==0
    assert out["social_completed"]==1
    assert live.execute(
        "SELECT usd_out FROM attention_explore_trades").fetchone()[0]==5.7


def test_osece_recovery_preserves_contract_and_does_not_reuse_old_signal_ids():
    src=db();live=db()
    osece_trade(src,MINT1)
    # Collision: a different OSECE position has the same ID in the live DB.
    osece_trade(live,MINT2)
    out=reconcile(live,src,"prior-exit")
    assert out["osece_added"]==1
    assert live.execute("SELECT COUNT(*) FROM osece_trades").fetchone()[0]==2
    assert live.execute("SELECT COUNT(*) FROM osece_launches").fetchone()[0]==2
    assert live.execute("SELECT COUNT(*) FROM osece_notifications").fetchone()[0]==2
    assert reconcile(live,src,"prior-exit")["skipped"]=="already imported"


def test_osece_open_position_remains_unresolved_without_future_quote():
    src=db();live=db()
    osece_trade(src,MINT1,closed=False)
    out=reconcile(live,src,"unfinished")
    assert out["osece_added"]==1
    assert live.execute(
        "SELECT exit_ts,usd_out FROM osece_trades").fetchone()==(None,None) or (
        tuple(live.execute("SELECT exit_ts,usd_out FROM osece_trades").fetchone())
        ==(None,None))
