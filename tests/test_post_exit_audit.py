"""Read-only post-exit investigation contract tests."""
from __future__ import annotations

import sqlite3
import unittest

from memeradar.post_exit_audit import analyze, audit_trade


def trade(**values):
    default = {
        "model":"social_attention","stake":1.0,
        "mint":"Abcd1234TESTMINT","entry_ts":1000.,
        "entry_price":1., "exit_ts":2800.,
        "exit_price":1., "usd_out":0.92,
        "exit_reason":"TIME_LIMIT"}
    return {**default, **values}


class PostExitAuditTests(unittest.TestCase):
    def test_later_price_is_not_brought_back_before_actual_exit(self):
        t=trade()
        quotes=[{"observed_ts":2700.,"price":10.},
                {"observed_ts":3100.,"price":1.2,"liquidity":9000},
                {"observed_ts":4600.,"price":1.6,"liquidity":9000}]
        result=audit_trade(t,quotes)
        self.assertEqual(result["quotes_after_exit"],2)
        self.assertEqual(result["peak_post_exit_pct_at_2h"],60.)
        self.assertEqual(result["fixed_horizon"]["5m"]["status"],"observed")
        self.assertEqual(result["fixed_horizon"]["30m"]["status"],"observed")
        self.assertAlmostEqual(
            result["fixed_horizon"]["30m"]["price_change_from_exit_pct"],60.)

    def test_missing_quotes_are_unknown_not_zero(self):
        r=audit_trade(trade(),[])
        self.assertIsNone(r["peak_post_exit_pct_at_2h"])
        self.assertIsNone(r["fixed_horizon"]["30m"]["additional_net_usd"])
        self.assertEqual(r["observability"],"no_post_exit_quotes_recorded")

    def test_observation_cannot_exceed_three_minute_tolerance(self):
        r=audit_trade(trade(),[
            {"observed_ts":2800+1800+181,"price":50.}])
        self.assertEqual(r["fixed_horizon"]["30m"]["status"],"unobserved")

    def test_loss_stop_can_be_observed_rally_or_missing(self):
        t=trade(exit_reason="STOP_LOSS_NET", exit_price=0.7, usd_out=.65)
        r=audit_trade(t,[{"observed_ts":3100.,"price":.80}])
        self.assertGreater(r["fixed_horizon"]["5m"]["additional_net_usd"],0)
        self.assertEqual(r["fixed_horizon"]["60m"]["status"],"unobserved")

    def test_entire_sqlite_ledger_analyzed_no_cherry_pick(self):
        db=sqlite3.connect(":memory:")
        db.row_factory=sqlite3.Row
        db.executescript("""
        CREATE TABLE micro_intents (
            model TEXT, stake REAL, mint TEXT, entry_ts REAL,
            entry_price REAL, exit_ts REAL, exit_price REAL,
            usd_out REAL, exit_reason TEXT, status TEXT);
        CREATE TABLE attention_chain (
            mint TEXT, observed_ts REAL, price_usd REAL,
            liquidity_usd REAL, pair TEXT);
        CREATE TABLE osece_quotes (
            mint TEXT, observed_ts REAL, price REAL, liquidity REAL);
        """)
        db.executemany(
            "INSERT INTO micro_intents VALUES (?,?,?,?,?,?,?,?,?,?)",
            [(x["model"],x["stake"],x["mint"],x["entry_ts"],x["entry_price"],
              x["exit_ts"],x["exit_price"],x["usd_out"],x["exit_reason"],"closed")
             for x in [
                 trade(),
                 trade(stake=5.0,usd_out=4.0),
                 trade(mint="Other",exit_reason="STOP_LOSS_NET",usd_out=.6)
             ]])
        db.execute("INSERT INTO attention_chain VALUES (?,?,?,?,?)",
                   ("Abcd1234TESTMINT",3100.,1.1,18000.,"PAIR"))
        result=analyze(db,limit=20,now=20000)
        self.assertEqual(result["total_audited_exits"],3)
        self.assertEqual(result["unique_coin_count"],2)
        self.assertEqual(result["exits_without_later_observed_price"],1)
        self.assertEqual(result["exits_with_any_later_observed_price"],2)
        self.assertEqual(result["by_exit_reason"]["TIME_LIMIT"]["count"],2)
        self.assertTrue(len(result["samples"])>=2)
        self.assertTrue(all(t["actual_net_usd"]<0 for t in result["trades"]))


if __name__=="__main__":
    unittest.main()
