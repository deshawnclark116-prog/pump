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
