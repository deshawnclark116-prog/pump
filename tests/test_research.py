import json
import sqlite3

import pytest

from memeradar.models import Safety, TokenState
from memeradar.paper import PaperRules, PaperTrader
from memeradar.research import Rules, audit, eligible, replay, timestamp
from memeradar.store import Store


def row(t, price, address="mint", **updates):
    ts = 2000 + t
    out = dict(ts=ts, chain="solana", address=address, price_usd=price,
               liquidity_usd=60_000, market_cap=200_000, vol_m5=10_000,
               features={"stage": "RUNNING", "research_v1": {
                   "market_received_at": ts, "breadth_received_at": ts,
                   "safety_checked_at": ts, "safety_passed": True,
                   "buyers": 45, "sellers": 30, "age_minutes": 60,
                   "dex_id": "pumpswap", "m5_pct": 10}})
    out.update(updates)
    return out


def recovery_rows():
    return [row(t, p) for t, p in [(0, 100), (30, 90), (60, 92), (90, 95), (120, 96)]]


def test_falling_dip_is_not_an_entry():
    assert replay([row(t, p) for t, p in [(0, 100), (30, 90), (60, 89), (90, 88)]])["unresolved"] == 0


def test_recovery_waits_for_later_fill_and_cannot_see_future():
    rows = recovery_rows()
    before = replay(rows[:-1])
    assert before["pending_signals"] == 1 and before["unresolved"] == 0
    after = replay(rows)
    h = after["unresolved_positions"][0]
    assert h["signal_ts"] == 2090 and h["entry_ts"] == 2120 and h["entry_price"] == 96
    assert after["cash"] == 90


def test_take_profit_signal_can_fill_at_loss_after_crash():
    result = replay(recovery_rows() + [row(150, 140), row(180, 50)])
    trade = result["trades"][0]
    assert trade["reason"] == "take_profit" and trade["pnl"] < -5
    assert trade["exit_price"] == 50


def test_stop_precedes_later_peak_and_fills_below_stop():
    result = replay(recovery_rows() + [row(150, 75), row(180, 70), row(210, 200)])
    assert result["closed"] == 1 and result["wins"] == 0
    assert result["trades"][0]["exit_price"] == 70


def test_gap_never_disappears_or_fills_at_later_winning_quote():
    result = replay(recovery_rows() + [row(400, 200), row(430, 210)])
    assert result["closed"] == 0 and result["unresolved"] == 1
    assert result["all_unresolved_zero_value_pnl"] == -10
    assert result["unresolved_positions"][0]["unresolved_reason"] == "observation gap"


@pytest.mark.parametrize("field,value", [("liquidity_usd", 1000), ("market_cap", 9000)])
def test_recheck_filters_at_entry(field, value):
    rows = recovery_rows()
    rows[-1][field] = value
    assert replay(rows)["unresolved"] == 0


def test_no_invented_historical_evidence_or_stale_breadth():
    old = row(0, 100, features={"stage": "EARLY"})
    assert not eligible(old, Rules())
    stale = row(0, 100)
    stale["features"]["research_v1"]["breadth_received_at"] -= 61
    assert not eligible(stale, Rules())
    assert replay([old])["legacy_rows_without_entry_evidence"] == 1


def test_cash_and_positions_shared_across_coins():
    rows = [row(t, 100, str(i)) for t in (0, 30) for i in range(10)]
    result = replay(rows, "momentum")
    assert result["unresolved"] == 5 and result["cash"] == 50
    assert result["capacity_skips"] == 5


def test_audit_uses_partial_proceeds_once_and_keeps_open_separate():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    trader = PaperTrader(db, PaperRules(fee_pct=0, slippage_pct=0))
    trader.buy("solana", "a", "A", 1)
    trader.take_profit("solana", "a", 2, 50)
    trader.sell("solana", "a", 1, "test")
    trader.buy("solana", "b", "B", 1)
    result = audit(db)["fast"]
    assert result["realized_pnl"] == 5 and result["open"] == 1
    assert result["closed"] == 1


def test_snapshot_preserves_entry_evidence_without_mutating_score(tmp_path):
    store = Store(str(tmp_path / "db"))
    t = TokenState("solana", "a", safety=Safety(True), features={"stage": "EARLY"},
                   buyers={"m5": 45}, sellers={"m5": 30}, buyers_updated_at=100)
    store.snapshot(t)
    t.buyers["m5"] = 900
    f = json.loads(store.db.execute("SELECT features FROM snapshots").fetchone()[0])
    assert f["research_v1"]["buyers"] == 45
    assert "research_v1" not in t.features


def test_timezone_required():
    with pytest.raises(ValueError):
        timestamp("2026-11-04T00:00:00")
    assert timestamp("2026-11-04T00:00:00Z") > 0
