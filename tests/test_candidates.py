import asyncio
import sqlite3

from memeradar.candidates import RecoveryPaper, UnderTwoScalper
from memeradar.models import Safety, TokenState
from memeradar.paper import PaperRules
from memeradar.scalp import ScalpRules, Scalper
from memeradar.stage import StageResult


async def send(*args):
    pass


def db():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


def test_separate_ledgers_and_restart():
    c = db()
    a = RecoveryPaper(c, send)
    b = UnderTwoScalper(ScalpRules(), PaperRules(), c, send)
    original = Scalper(ScalpRules(), PaperRules(), c, send)
    a.book.buy("solana", "mint", "X", 1)
    assert b.book.cash() == original.book.cash() == 100
    restored = RecoveryPaper(c, send)
    assert "mint" in restored.held and "mint" in restored.used
    assert restored.book.cash() == 90


def test_under_two_checks_actual_entry_quote():
    b = UnderTwoScalper(ScalpRules(), PaperRules(), db(), send)
    t = TokenState("solana", "mint", price_usd=1.9, first_price=1)
    asyncio.run(b._buy(t, 2.1, "test"))
    assert not b.held
    asyncio.run(b._buy(t, 1.9, "test"))
    assert "mint" in b.held


def test_recovery_requires_bounce_and_later_entry(monkeypatch):
    import time
    c = RecoveryPaper(db(), send)
    t = TokenState("solana", "mint", dex_id="pumpswap", market_cap=200000,
                   liquidity_usd=60000, vol_m5=10000, buyers={"m5":45}, sellers={"m5":30},
                   price_change={"m5":10}, token_created_at=1, safety=Safety(True))
    from types import SimpleNamespace
    t.stage = SimpleNamespace(stage="RUNNING")
    for offset, px in [(0,100),(30,90),(60,92),(90,95)]:
        now=10000+offset
        monkeypatch.setattr(time,"time",lambda n=now:n)
        t.price_usd=px
        t.market_updated_at=t.buyers_updated_at=t.safety.checked_at=now
        asyncio.run(c.consider(t))
        assert not c.held
    monkeypatch.setattr(time,"time",lambda:10120)
    t.price_usd=96
    t.market_updated_at=t.buyers_updated_at=t.safety.checked_at=10120
    asyncio.run(c.consider(t))
    assert "mint" in c.held and c.book.cash()==90


def test_candidate_exit_after_restore():
    c=db(); p=RecoveryPaper(c,send)
    p.book.buy("solana","mint","X",1)
    p=RecoveryPaper(c,send)
    class Http:
        async def get(self,url):
            return [{"baseToken":{"address":"mint"},"priceUsd":"0.5","liquidity":{"usd":60000}}]
    asyncio.run(p.manage(Http()))
    assert not p.held
    assert p.book.closed_trades()[0]["usd_out"]<5
