import json
import time

from memeradar import backtest, scoring
from memeradar.config import SafetyRules, Weights
from memeradar.models import Safety, TokenState
from memeradar.safety import evaluate_goplus, evaluate_rugcheck
from memeradar.sources import reddit

RULES = SafetyRules()


def test_gini():
    assert scoring.gini([1, 1, 1, 1]) == 0
    assert scoring.gini([0, 0, 0, 10]) == 1.0  # single non-zero wallet
    assert 0.5 < scoring.gini([1, 1, 1, 50]) < 1


def test_rugcheck_rejects_mint_authority_and_concentration():
    report = {"mint": "M", "mintAuthority": "abc", "freezeAuthority": None, "risks": [],
              "markets": [{"pubkey": "POOL", "marketType": "pump_fun", "lp": {"lpLockedPct": 0}}],
              "topHolders": [{"owner": "POOL", "pct": 40}] + [{"owner": f"w{i}", "pct": 6} for i in range(10)],
              "totalHolders": 100}
    s = evaluate_rugcheck(report, RULES)
    assert not s.passed
    assert any("mint authority" in r for r in s.reasons)
    assert s.top10_pct == 60  # pool holder excluded, pump_fun LP exempt
    assert not any("LP" in r for r in s.reasons)


def test_rugcheck_passes_clean_token():
    report = {"mint": "M", "risks": [{"name": "Low liquidity", "level": "warn"}],
              "markets": [{"pubkey": "POOL", "marketType": "raydium", "lp": {"lpLockedPct": 100}}],
              "topHolders": [{"owner": "POOL", "pct": 30}, {"owner": "a", "pct": 5}]}
    assert evaluate_rugcheck(report, RULES).passed


def test_rugcheck_lp_uses_deepest_real_lp_pool():
    def mkt(kind, lp_mint, locked, usd):
        return {"pubkey": kind, "marketType": kind,
                "lp": {"lpMint": lp_mint, "lpLockedPct": locked, "baseUSD": usd, "quoteUSD": usd}}
    graduated = {"mint": "M", "markets": [mkt("pump_fun_amm", "LPMINT", 100, 30_000),
                                          mkt("meteoraDlmm", "11111111111111111111111111111111", 0, 50_000)]}
    s = evaluate_rugcheck(graduated, RULES)
    assert s.passed and s.lp_locked_pct == 100
    unlocked = {"mint": "M", "markets": [mkt("raydium", "LPMINT", 2, 30_000)]}
    assert not evaluate_rugcheck(unlocked, RULES).passed


def test_goplus_rules():
    info = {"is_honeypot": "0", "buy_tax": "0.1", "sell_tax": "0", "is_mintable": "0",
            "holders": [{"address": "0xpool", "percent": "0.6", "is_contract": 1},
                        {"address": "0xa", "percent": "0.1", "is_contract": 0}],
            "lp_holders": [{"address": "0x000000000000000000000000000000000000dead", "percent": "0.99"}]}
    s = evaluate_goplus(info, RULES)
    assert s.reasons == ["buy tax 10.0%"]
    assert round(s.lp_locked_pct) == 99


def test_reddit_extract():
    syms, addrs = reddit.extract("Aping $wif and $USDC, CA: 7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr lol")
    assert syms == {"WIF"}
    assert addrs == {"7GCihgDB8fe6KNjn2MYtkzZcRjQy3t9GHdC8uHYmW2hr"}


def test_social_velocity_rewards_acceleration():
    idx = scoring.MentionIndex()
    now = time.time()
    for i in range(6):
        idx.add(f"p{i}", f"user{i}", {"FROG"}, set(), ts=now - 60 * i)
    recent, base = idx.stats("FROG", "addr", now)
    assert recent == 3 and base == 0
    assert scoring.social_velocity(recent, base) > scoring.social_velocity(recent, recent)


def _hot_token() -> TokenState:
    t = TokenState(chain="solana", address="X", symbol="HOT", liquidity_usd=80_000,
                   vol_m5=40_000, vol_h1=150_000, buys_h1=900, sells_h1=300,
                   pair_created_at=time.time() - 3600)
    t.safety = Safety(passed=True, top10_pct=18, holders=1500)
    now = time.time()
    for i in range(60):
        t.trades.add((f"w{i}", "buy", 1.0), ts=now - i)
    t.smart_buys = {"s1", "s2"}
    return t


def test_composite_orders_hot_above_cold():
    idx = scoring.MentionIndex()
    for i in range(5):
        idx.add(f"p{i}", f"user{i}", {"HOT"}, {"X"})
    cold = TokenState(chain="solana", address="Y", liquidity_usd=6_000, vol_m5=0, vol_h1=11_000,
                      buys_h1=40, sells_h1=60)
    hot_f = scoring.compute_features(_hot_token(), idx)
    cold_f = scoring.compute_features(cold, idx)
    w = Weights()
    assert scoring.composite(hot_f, w) > 0.5 > scoring.composite(cold_f, w)
    assert all(0 <= v <= 1 for k, v in hot_f.items() if k != "mentions_recent")


def test_backtest_and_tune():
    snaps = []
    for tok in range(10):
        good = tok % 2 == 0
        f = {"social_velocity": 0.9 if good else 0.1, "volume_acceleration": 0.5,
             "wallet_diversity": 0.5, "liquidity_depth": 0.5, "smart_money": 0}
        for step in range(3):
            price = 1.0 * (2 ** step if good else 1)
            snaps.append({"chain": "solana", "address": str(tok), "ts": 1000 + step * 60,
                          "features": json.dumps(f), "price_usd": price})
    rows = backtest.build_rows(snaps)
    m = backtest.evaluate(rows, Weights().as_dict(), 0.45)
    assert m["alerts"] == 5 and m["hit_rate"] == 1.0
    w, best = backtest.tune(rows, 0.45, trials=200)
    assert best["hit_rate"] == 1.0


# ---------------------------------------------------------------- stage detector
from memeradar import stage as stg  # noqa: E402


def test_stage_early_when_buyers_accelerating_and_not_run_far():
    kw = dict(price=2.0, ref_price=1.0, peak_price=2.0,
              buyers={"m5": 40, "m15": 60}, sellers={"m5": 10, "m15": 20})
    r = stg.classify(age_minutes=30, **kw)
    assert r.stage == stg.EARLY and r.freshness == 1.0
    # same numbers on a 3-day-old coin: could be a second leg, but never "early"
    assert stg.classify(age_minutes=3 * 24 * 60, **kw).stage == stg.RUNNING


def test_stage_late_when_already_ripped():
    r = stg.classify(price=12.0, ref_price=1.0, peak_price=12.0,
                     buyers={"m5": 40, "m15": 60}, sellers={"m5": 10, "m15": 20})
    assert r.stage == stg.LATE


def test_stage_late_when_buyers_dry_up_after_a_run():
    r = stg.classify(price=3.0, ref_price=1.0, peak_price=3.1,
                     buyers={"m5": 5, "m15": 65}, sellers={"m5": 4, "m15": 30})
    assert r.stage == stg.LATE and "drying up" in r.reasons[0]


def test_stage_dumping_off_peak_or_sell_pressure():
    assert stg.classify(price=0.6, ref_price=0.5, peak_price=1.0, buyers=None, sellers=None).stage == stg.DUMPING
    r = stg.classify(price=1.0, ref_price=1.0, peak_price=1.0,
                     buyers={"m5": 6, "m15": 20}, sellers={"m5": 20, "m15": 30})
    assert r.stage == stg.DUMPING


def test_reference_price_counts_todays_rip_on_old_coins():
    t = TokenState(chain="solana", address="Q", price_usd=5.0, price_change={"h6": 20.0, "h24": 400.0},
                   pair_created_at=time.time() - 5 * 86400)
    t.first_price = 5.0
    assert t.reference_price() == 1.0  # up 5x today -> LATE territory even though we just found it


def test_reference_price_uses_24h_change_for_young_coins():
    t = TokenState(chain="solana", address="Z", price_usd=4.0, price_change={"h24": 300.0},
                   pair_created_at=time.time() - 600)
    t.first_price = 3.5
    assert t.reference_price() == 1.0


# ---------------------------------------------------------------- exit alerts
from memeradar import exits as ex  # noqa: E402


def _pos():
    return ex.Position(chain="solana", address="A", symbol="X", pair_address="P", entry_ts=time.time(),
                       entry_price=1.0, entry_liquidity=20_000, peak_price=1.0)


def test_exit_take_profit_levels_fire_once():
    p, r = _pos(), ex.ExitRules()
    assert [s.title for s in ex.evaluate(p, 1.6, 20_000, "RUNNING", r)] == ["TAKE PROFIT"]
    assert ex.evaluate(p, 1.7, 20_000, "RUNNING", r) == []
    assert [s.title for s in ex.evaluate(p, 2.1, 20_000, "RUNNING", r)] == ["TAKE PROFIT"]
    assert not p.closed


def test_exit_trailing_stop_after_peak():
    p, r = _pos(), ex.ExitRules()
    ex.evaluate(p, 1.86, 20_000, "RUNNING", r)            # the Learjet +86% peak
    sigs = ex.evaluate(p, 1.30, 20_000, "LATE", r)        # 30% off the high
    assert sigs[0].title == "GET OUT" and p.closed
    assert ex.evaluate(p, 0.02, 0, "DUMPING", r) == []    # watch already over


def test_exit_rug_and_stop_loss():
    p = _pos()
    assert ex.evaluate(p, 0.9, 5_000, "RUNNING", ex.ExitRules())[0].title == "RUG WARNING"
    p = _pos()
    assert ex.evaluate(p, 0.65, 19_000, "RUNNING", ex.ExitRules())[0].title == "STOP LOSS"


def test_exit_fading_warning_once():
    p, r = _pos(), ex.ExitRules()
    assert [s.title for s in ex.evaluate(p, 1.05, 20_000, "LATE", r)] == ["FADING"]
    assert ex.evaluate(p, 1.04, 20_000, "LATE", r) == []


def test_store_prune_keeps_recent(tmp_path):
    from memeradar.store import Store
    s = Store(str(tmp_path / "t.db"))
    s.db.execute("INSERT INTO snapshots (ts, chain, address, score) VALUES (?,?,?,?)", (time.time() - 30 * 86400, "c", "a", 0))
    s.db.execute("INSERT INTO snapshots (ts, chain, address, score) VALUES (?,?,?,?)", (time.time(), "c", "b", 0))
    assert s.prune(14) == 1
    assert s.db.execute("SELECT COUNT(*) FROM snapshots").fetchone()[0] == 1


def test_rugcheck_insider_network_rejected():
    from memeradar.config import SafetyRules
    from memeradar.safety import evaluate_rugcheck
    base = {"token": {"supply": 1_000_000}, "topHolders": [{"owner": "a", "pct": 5}], "markets": []}
    ok = evaluate_rugcheck({**base, "insiderNetworks": [{"size": 4, "currentHolding": 50_000}]}, SafetyRules())
    assert ok.passed
    bad = evaluate_rugcheck({**base, "insiderNetworks": [{"size": 265, "currentHolding": 807_000}]}, SafetyRules())
    assert not bad.passed and "insider group of 265 wallets holds 81%" in bad.reasons


def test_new_pool_on_old_coin_is_not_early():
    """BINGUS: ran ~$44k -> $260k on an old pool, then opened a new Raydium pool 9 min before
    the alert. The new pool alone said '1.7x, 9m old'; the coin was really 6x+ and ~1.5h old."""
    import time
    from memeradar.models import TokenState
    from memeradar.sources.dexscreener import token_history
    now = time.time() * 1000
    pairs = [
        {"dexId": "raydium", "marketCap": 296154, "pairCreatedAt": now - 9 * 60000,
         "priceChange": {"h6": 98, "h24": 98}},
        {"dexId": "meteora", "marketCap": 279496, "pairCreatedAt": now - 5 * 60000,
         "priceChange": {"h6": 279458, "h24": 279458}},             # silly seed price: ignored
        {"dexId": "meteora", "marketCap": 129593, "pairCreatedAt": now - 95 * 60000,
         "priceChange": {"h6": 193, "h24": 193}},
        {"dexId": "pumpfun", "marketCap": 38000, "pairCreatedAt": now - 100 * 60000,
         "priceChange": {"h24": 1300}},                              # launchpad curve: ignored
    ]
    h = token_history(pairs)
    assert abs(h["start_mcap"] - 129593 / 2.93) < 1
    t = TokenState(chain="solana", address="x", price_usd=1.0, market_cap=296154,
                   pair_created_at=now / 1000 - 540, price_change={"h6": 98, "h24": 98}, **h)
    assert 99 < t.age_minutes < 101 and t.pool_age_minutes < 10
    assert 296154 / (h["start_mcap"]) > 6 and t.reference_price() < 0.2


def test_paper_trading_round_trip(tmp_path):
    from memeradar.paper import PaperRules, PaperTrader
    from memeradar.store import Store
    trader = PaperTrader(Store(str(tmp_path / "p.db")).db, PaperRules(fee_pct=1.0, slippage_pct=0.0))
    assert "PAPER BUY $AAA" in trader.buy("solana", "a", "AAA", 1.0)
    assert trader.buy("solana", "a", "AAA", 1.0) is None          # already holding
    assert abs(trader.cash() - 95.0) < 1e-9
    msg = trader.sell("solana", "a", 2.0, "GET OUT")               # price doubled
    assert "✅" in msg and "1 wins / 0 losses" in msg
    # $5 -1% fee -> 4.95 worth of tokens, doubles to 9.90, -1% fee -> 9.801
    assert abs(trader.cash() - (95 + 9.801)) < 1e-6
    trader.buy("solana", "b", "BBB", 1.0)
    assert "❌" in trader.sell("solana", "b", 0.03, "RUG WARNING")
    assert trader.record() == (1, 1)
    assert trader.sell("solana", "zzz", 1.0, "x") is None
    rules = PaperRules(max_open=1)
    t2 = PaperTrader(Store(str(tmp_path / "q.db")).db, rules)
    t2.buy("solana", "c", "C", 1.0)
    assert "skipped" in t2.buy("solana", "d", "D", 1.0)


def test_wait_before_alerting(tmp_path):
    import asyncio
    from memeradar.config import Config
    from memeradar.models import TokenState
    from memeradar.pipeline import Radar
    from memeradar.stage import StageResult
    cfg = Config(db_path=str(tmp_path / "w.db"), enable_pumpportal=False)
    r = Radar(cfg)

    def tok(price, liq=20000, stage_name="EARLY", score=0.5):
        t = TokenState(chain="solana", address="x", symbol="X", price_usd=price, liquidity_usd=liq)
        t.stage = StageResult(stage_name, 1.5, 0.0, 1.5, [])
        t.score = score
        return t

    # healthy for 3 minutes -> alert
    t = tok(1.0)
    assert not r._confirmed(t, 1000)                    # starts watching
    t.price_usd = 1.1
    assert not r._confirmed(t, 1000 + 60)               # still waiting
    assert r._confirmed(t, 1000 + 181)
    assert "watched 3 min" in t.confirm_note and "+10%" in t.confirm_note

    # crashes while waiting -> no alert, not re-watched right away
    t = tok(1.0)
    r._confirmed(t, 5000)
    t.price_usd = 0.6
    assert not r._confirmed(t, 5030)
    assert t.candidate_at == 0 and not r._confirmed(t, 5060)
    assert not r._confirmed(t, 5300) and t.candidate_at == 0   # 15 min pause

    # liquidity pulled / turned DUMPING -> cancelled
    t = tok(1.0)
    r._confirmed(t, 9000)
    t.liquidity_usd = 9000
    assert not r._confirmed(t, 9200) and t.candidate_at == 0
    t = tok(1.0)
    r._confirmed(t, 20000)
    t.stage = StageResult("DUMPING", 1.5, 0.4, 0.5, [])
    assert not r._confirmed(t, 20200) and t.candidate_at == 0
    asyncio.run(r.http.close())
