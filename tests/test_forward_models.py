import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from memeradar.forward_models import DipReclaim, FlowAccumulation, PulseBreakout
from memeradar.models import Safety, TokenState


async def send(*args):
    return None


def database():
    c = sqlite3.connect(":memory:")
    c.row_factory = sqlite3.Row
    return c


def token(now=10000, *, address="mint", buyers=20, sellers=12, m5=10):
    t = TokenState("solana", address, symbol="TEST", dex_id="pumpswap",
                   price_usd=100.0, liquidity_usd=22_000, market_cap=100_000,
                   vol_m5=6000, buyers={"m5":buyers}, sellers={"m5":sellers},
                   price_change={"m5":m5}, safety=Safety(True, checked_at=now))
    t.stage = SimpleNamespace(stage="RUNNING")
    t.market_updated_at = t.buyers_updated_at = now
    return t


def tick(monkeypatch, model, t, now, px, *, buyers=None, sellers=None, m5=None):
    monkeypatch.setattr("memeradar.forward_models.time.time", lambda: now)
    t.price_usd, t.market_updated_at, t.buyers_updated_at = px, now, now
    t.safety.checked_at = now
    if buyers is not None:
        t.buyers["m5"] = buyers
    if sellers is not None:
        t.sellers["m5"] = sellers
    if m5 is not None:
        t.price_change["m5"] = m5
    asyncio.run(model.consider(t))


def test_three_independent_books_and_restart():
    c = database()
    models = [PulseBreakout(c, send), FlowAccumulation(c, send), DipReclaim(c, send)]
    assert [m.book.cash() for m in models] == [250, 250, 250]
    models[0].book.buy("solana", "mint", "TEST", 1, now=100)
    assert [m.book.cash() for m in models] == [240, 250, 250]
    new = PulseBreakout(c, send)
    assert "mint" in new.held and new.book.cash() == 240


def test_pulse_requires_two_fresh_higher_observations(monkeypatch):
    c = database()
    m = PulseBreakout(c, send)
    t = token()
    tick(monkeypatch, m, t, 10000, 100, buyers=20)
    tick(monkeypatch, m, t, 10020, 102, buyers=24)
    assert not m.held
    tick(monkeypatch, m, t, 10045, 105, buyers=29)
    assert "mint" in m.held
    assert len(m.book.open_trades()) == 1
    assert m.book.cash() == 240


def test_flow_requires_buyer_growth_not_just_positive_ratio(monkeypatch):
    c = database()
    m = FlowAccumulation(c, send)
    t = token(buyers=20, sellers=12, m5=5)
    tick(monkeypatch, m, t, 10000, 100, buyers=20)
    tick(monkeypatch, m, t, 10020, 100.5, buyers=20)
    assert not m.held
    tick(monkeypatch, m, t, 10045, 101.0, buyers=26, sellers=13)
    assert "mint" in m.held


def test_reclaim_keeps_watch_during_negative_momentum(monkeypatch):
    c = database()
    m = DipReclaim(c, send)
    t = token(buyers=24, sellers=15)
    tick(monkeypatch, m, t, 10000, 100)
    tick(monkeypatch, m, t, 10020, 92, m5=-6)
    tick(monkeypatch, m, t, 10040, 94, m5=-4)
    tick(monkeypatch, m, t, 10065, 97, m5=-2)
    assert not m.held
    tick(monkeypatch, m, t, 10090, 98, m5=-1)
    assert "mint" in m.held and m.book.cash() == 240


@pytest.mark.parametrize("model_cls", [PulseBreakout, FlowAccumulation, DipReclaim])
def test_rejects_stale_market_and_unchecked_safety(monkeypatch, model_cls):
    m = model_cls(database(), send)
    t = token()
    t.market_updated_at = 1
    monkeypatch.setattr("memeradar.forward_models.time.time", lambda: 10000)
    asyncio.run(m.consider(t))
    assert not m.held and not m.watches
    t.market_updated_at = 10000
    t.safety.passed = False
    asyncio.run(m.consider(t))
    assert not m.watches


def test_pulse_exit_still_uses_paper_costs_and_no_orders(monkeypatch):
    m = PulseBreakout(database(), send)
    t = token()
    tick(monkeypatch, m, t, 10000, 100)
    tick(monkeypatch, m, t, 10020, 102)
    tick(monkeypatch, m, t, 10045, 105)
    class Http:
        async def get(self, url):
            assert "/tokens/v1/solana/" in url
            return [{"baseToken": {"address": "mint"}, "priceUsd": "145",
                     "liquidity": {"usd": 22000}}]
    tick_time = 10060
    monkeypatch.setattr("memeradar.forward_models.time.time", lambda: tick_time)
    asyncio.run(m.manage(Http()))
    assert not m.held
    closed = m.book.closed_trades()
    assert len(closed) == 1 and closed[0]["usd_out"] > closed[0]["usd_in"]
    assert m.book.cash() > 250
