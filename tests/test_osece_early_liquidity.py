"""Prospective comparison: frozen OSECE versus early-liquidity paper variant."""
from memeradar.attention.db import connect
from memeradar.osece import core

MINT="7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr"


def setup():
    db=connect(":memory:")
    core.ensure(db)
    core.started_at(db,900)
    core.record_event(db,source="google_trends",external_id="term1",
                      actor="trends",text="Hawk Tuah",curated=True,observed_ts=1000)
    core.record_launch(db,mint=MINT,name="Hawk Tuah",symbol="HAWKTUAH",
                       created_ts=1100,observed_ts=1100)
    return db


def quote(db,ts,price=1.0,buy=1000,sell=350,liq=9500,score=0,traders=20):
    token={"id":MINT,"usdPrice":price,"liquidity":liq,"organicScore":score,
           "stats5m":{"buyVolume":buy,"sellVolume":sell,
                      "numTraders":traders},
           "audit":{"mintAuthorityDisabled":True,
                    "freezeAuthorityDisabled":True}}
    core.record_jupiter_quote(db,token,now=ts)


def passed(db,ts=1250):
    db.execute("INSERT INTO attention_safety_history VALUES (?,?,?,?)",
               (MINT,ts,"passed","[]"))
    db.commit()


def test_early_liquidity_works_without_nonzero_organic(monkeypatch):
    monkeypatch.setenv("OSECE_PAPER_ENABLED","1")
    monkeypatch.delenv("OSECE_EXIT_ONLY",raising=False)
    db=setup()
    passed(db)
    for ts,px in [(1500,1.0),(1550,1.02),(1590,1.025)]:
        quote(db,ts,px)
        core.tick(db,now=ts)
    rows=db.execute("SELECT variant,entry_ts FROM osece_trades").fetchall()
    assert len(rows)==1
    assert rows[0]["variant"]==core.EARLY_LIQ
    assert rows[0]["entry_ts"]==1590
    # Original two unaltered experiments still correctly reject zero score.
    assert db.execute(
        "SELECT COUNT(*) FROM osece_signals WHERE variant IN (?,?) "
        "AND state='watch'",(core.SINGLE,core.COLLISION)).fetchone()[0]==1
    quote(db,1630,1.35)
    assert core.tick(db,now=1630)["sold"]==1
    assert core.report(db)["books"][2]["closed"]==1


def test_early_liquidity_needs_real_flow_and_rugcheck(monkeypatch):
    monkeypatch.setenv("OSECE_PAPER_ENABLED","1")
    monkeypatch.delenv("OSECE_EXIT_ONLY",raising=False)
    db=setup()
    for ts in (1500,1550,1590):
        quote(db,ts)
        core.tick(db,now=ts)
    assert db.execute("SELECT COUNT(*) FROM osece_trades").fetchone()[0]==0
    # Independent risk checks must fail on low liquidity, unknown audit,
    # and missing dollar-volume fields.
    base={"price":1.0,"liquidity":9500,"buy_usd_m5":1000,
          "sell_usd_m5":350,"num_traders":20,"audit_ok":1}
    for overrides in [{"liquidity":4000},{"audit_ok":None},
                      {"buy_usd_m5":None},{"sell_usd_m5":None},
                      {"num_traders":None},{"num_traders":5}]:
        row={**base,**overrides}
        assert not core._market_entry_ok(row,core.EARLY_LIQ)


def test_original_strict_books_unchanged():
    quote_data={"price":1,"liquidity":21000,"buy_usd_m5":1800,
                "sell_usd_m5":1100,"organic_score":0,
                "num_traders":50,"audit_ok":1}
    assert not core._market_entry_ok(quote_data,core.SINGLE)
    assert not core._market_entry_ok(quote_data,core.COLLISION)
    assert core._market_entry_ok(quote_data,core.EARLY_LIQ)
