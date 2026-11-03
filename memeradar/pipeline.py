"""The Radar: wires sources -> pre-filter -> safety -> scoring -> storage/alerts.

Everything runs in one asyncio process (no Kafka/Redis needed), so it fits on a laptop,
a Raspberry Pi, a free cloud VM, or a GitHub Actions runner.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import OrderedDict

from . import alerts, exits, safety, scoring, stage
from .config import Config
from .http import Http
from .models import TokenState
from .sources import dexscreener, geckoterminal, reddit
from .sources.pumpportal import PumpPortal
from .store import Store

log = logging.getLogger(__name__)

SAFETY_RECHECK = 15 * 60
SAFETY_CHECKS_PER_CYCLE = 8
PREFILTER_GRACE = 20 * 60      # drop tokens that never reach the volume bar within 20 min
REDDIT_LOOKUPS_PER_CYCLE = 5


class Radar:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.http = Http()
        self.store = Store(cfg.db_path)
        self.mentions = scoring.MentionIndex()
        self.tokens: dict[tuple[str, str], TokenState] = {}
        self.rejected: OrderedDict[tuple[str, str], float] = OrderedDict()
        self.pending_addresses: OrderedDict[str, None] = OrderedDict()
        self.positions: dict[tuple[str, str], exits.Position] = {}
        for raw in self.store.load_positions():
            pos = exits.Position(**raw)
            self.positions[(pos.chain, pos.address)] = pos
        self.pump = (PumpPortal(self._on_new_pump_token, self._on_pump_trade, cfg.pumpportal_api_key)
                     if cfg.enable_pumpportal else None)

    # ------------------------------------------------------------------ intake
    def add_candidate(self, chain: str, address: str, source: str, symbol: str = "",
                      name: str = "") -> TokenState | None:
        key = (chain, address)
        if key in self.tokens:
            self.tokens[key].last_activity = time.time()
            return self.tokens[key]
        if key in self.rejected:
            return None
        if len(self.tokens) >= self.cfg.max_tracked_tokens:
            candidates = [t for t in self.tokens.values() if t.key not in self.positions]
            if candidates:
                victim = min(candidates, key=lambda t: (t.score, t.last_activity))
                self._drop(victim.key)
        t = TokenState(chain=chain, address=address, source=source, symbol=symbol, name=name)
        self.tokens[key] = t
        log.debug("tracking %s %s (%s)", chain, symbol or address, source)
        return t

    def _drop(self, key: tuple[str, str], reject: bool = False) -> None:
        if key in self.positions and not reject:
            return  # still watching it for an exit
        t = self.tokens.pop(key, None)
        if reject:
            self.rejected[key] = time.time()
            while len(self.rejected) > 20_000:
                self.rejected.popitem(last=False)
        if t and self.pump and t.chain == "solana":
            asyncio.ensure_future(self.pump.unsubscribe_trades([t.address]))

    async def _on_new_pump_token(self, data: dict) -> None:
        t = self.add_candidate("solana", data["mint"], "pumpportal", data.get("symbol", ""), data.get("name", ""))
        if t:
            try:
                t.launch_mcap_sol = float(data.get("marketCapSol") or 0) or None
            except (TypeError, ValueError):
                pass
        if t and self.pump:
            await self.pump.subscribe_trades([t.address])

    async def _on_pump_trade(self, data: dict) -> None:
        t = self.tokens.get(("solana", data.get("mint")))
        if not t:
            return
        side = "sell" if data.get("txType") == "sell" else "buy"
        wallet = data.get("traderPublicKey", "")
        try:
            sol = float(data.get("solAmount") or 0)
        except (TypeError, ValueError):
            sol = 0.0
        t.trades.add((wallet, side, sol))
        t.last_activity = time.time()
        if side == "buy" and wallet in self.cfg.smart_wallets:
            t.smart_buys.add(wallet)

    # ------------------------------------------------------------------ pollers
    async def _every(self, seconds: int, fn) -> None:
        while True:
            try:
                await fn()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("%s failed", fn.__name__)
            await asyncio.sleep(seconds)

    async def poll_dexscreener(self) -> None:
        for c in await dexscreener.discover(self.http, self.cfg.chains):
            self.add_candidate(c["chain"], c["address"], c["source"])

    async def poll_geckoterminal(self) -> None:
        for c in await geckoterminal.discover(self.http, self.cfg.chains):
            self.add_candidate(c["chain"], c["address"], c["source"])

    async def poll_reddit(self) -> None:
        for sub in self.cfg.subreddits:
            for m in await reddit.fetch(self.http, sub):
                if self.mentions.add(m.post_id, m.author, m.symbols, m.addresses):
                    for addr in m.addresses:
                        if not any(k[1] == addr for k in self.tokens):
                            self.pending_addresses[addr] = None
        while len(self.pending_addresses) > 200:
            self.pending_addresses.popitem(last=False)

    # ------------------------------------------------------------------ scoring
    async def _resolve_reddit_addresses(self) -> None:
        for _ in range(min(REDDIT_LOOKUPS_PER_CYCLE, len(self.pending_addresses))):
            addr, _ = self.pending_addresses.popitem(last=False)
            found = await dexscreener.lookup_any_chain(self.http, addr)
            if found and found[0] in self.cfg.chains:
                t = self.add_candidate(found[0], addr, "reddit")
                if t:
                    self._apply_market(t, found[1])

    @staticmethod
    def _apply_market(t: TokenState, snap: dict) -> None:
        for k, v in snap.items():
            if k in ("symbol", "name") and not v:
                continue
            setattr(t, k, v)
        t.market_updated_at = time.time()
        if t.price_usd:
            t.first_price = t.first_price or t.price_usd
            t.peak_price = max(t.peak_price or 0, t.price_usd)
        if snap.get("vol_m5"):
            t.last_activity = time.time()

    async def _refresh_markets(self) -> None:
        by_chain: dict[str, list[str]] = {}
        for t in self.tokens.values():
            by_chain.setdefault(t.chain, []).append(t.address)
        for chain, addrs in by_chain.items():
            snaps = await dexscreener.snapshots(self.http, chain, addrs)
            for addr, snap in snaps.items():
                t = self.tokens.get((chain, addr))
                if t:
                    self._apply_market(t, snap)

    def _passes_prefilter(self, t: TokenState) -> bool:
        busy = t.vol_h1 >= self.cfg.min_volume_h1_usd or t.vol_m5 >= self.cfg.early_min_volume_m5_usd
        if not busy:
            return False
        return t.liquidity_usd is None or t.liquidity_usd >= self.cfg.min_liquidity_usd

    async def score_cycle(self) -> list[TokenState]:
        now = time.time()
        await self._resolve_reddit_addresses()
        await self._refresh_markets()

        alerted: list[TokenState] = []
        ready: list[TokenState] = []
        safety_budget = SAFETY_CHECKS_PER_CYCLE
        for t in list(self.tokens.values()):
            if now - t.last_activity > self.cfg.track_ttl_minutes * 60:
                self._drop(t.key)
                continue
            if self.pump and t.chain == "solana" and now - t.first_seen > self.cfg.pumpportal_trade_ttl_minutes * 60 \
                    and t.score < self.cfg.alert_threshold:
                await self.pump.unsubscribe_trades([t.address])
            if not self._passes_prefilter(t):
                if now - t.first_seen > PREFILTER_GRACE:
                    self._drop(t.key)
                continue

            if t.safety is None or now - t.safety.checked_at > SAFETY_RECHECK:
                if safety_budget <= 0:
                    continue
                safety_budget -= 1
                try:
                    result = await safety.check(self.http, t.chain, t.address, self.cfg.safety)
                except Exception:
                    log.exception("safety check crashed for %s %s", t.chain, t.address)
                    result = None
                if result is None:
                    continue
                t.safety = result
                self.store.upsert_token(t)
                if not result.passed:
                    log.info("rejected %s %s: %s", t.symbol, t.address, "; ".join(result.reasons))
                    self._drop(t.key, reject=True)
                    continue

            ready.append(t)

        # unique buyers/sellers for everything that made it through (30 pools per request)
        by_chain: dict[str, list[TokenState]] = {}
        for t in ready:
            if t.pair_address:
                by_chain.setdefault(t.chain, []).append(t)
        for chain, toks in by_chain.items():
            stats = await geckoterminal.pool_stats(self.http, chain, [t.pair_address for t in toks])
            for t in toks:
                st = stats.get(t.pair_address)
                if st:
                    t.buyers, t.sellers = st["buyers"], st["sellers"]

        for t in ready:
            t.stage = stage.classify(price=t.price_usd, ref_price=t.reference_price(), peak_price=t.peak_price,
                                     buyers=t.buyers, sellers=t.sellers, age_minutes=t.age_minutes,
                                     rules=self.cfg.stage)
            t.features = scoring.compute_features(t, self.mentions)
            t.momentum = scoring.composite(t.features, self.cfg.weights)
            t.score = round(t.momentum * t.stage.freshness, 4)
            t.features.update(stage=t.stage.stage, freshness=t.stage.freshness, momentum=t.momentum,
                              runup=t.stage.runup, drawdown=round(t.stage.drawdown, 4))
            self.store.upsert_token(t)
            self.store.snapshot(t)

            if t.score >= self.cfg.alert_threshold and t.stage.stage in self.cfg.alert_stages:
                last = t.last_alert or self.store.last_alert_ts(t.chain, t.address)
                if now - last > self.cfg.alert_cooldown_minutes * 60:
                    t.last_alert = now
                    self.store.alert(t)
                    await alerts.send(self.http, self.cfg, t)
                    alerted.append(t)
                    if t.key not in self.positions and t.price_usd:
                        pos = exits.Position(chain=t.chain, address=t.address, symbol=t.symbol,
                                             pair_address=t.pair_address, entry_ts=now,
                                             entry_price=t.price_usd, entry_liquidity=t.liquidity_usd,
                                             peak_price=t.price_usd)
                        self.positions[t.key] = pos
                        self.store.save_position(pos)
        self.store.commit()
        log.info("cycle: tracking %d tokens, %d scored, %d alerts", len(self.tokens),
                 sum(1 for t in self.tokens.values() if t.features), len(alerted))
        return alerted

    # ------------------------------------------------------------------ exits
    async def exit_cycle(self) -> None:
        """Fast loop (every ~10s) over alerted coins: take-profit / get-out signals."""
        open_pos = [p for p in self.positions.values() if not p.closed]
        if not open_pos:
            return
        by_chain: dict[str, list[str]] = {}
        for p in open_pos:
            by_chain.setdefault(p.chain, []).append(p.address)
        snaps: dict[tuple[str, str], dict] = {}
        for chain, addrs in by_chain.items():
            for addr, snap in (await dexscreener.snapshots(self.http, chain, addrs)).items():
                snaps[(chain, addr)] = snap
        for pos in open_pos:
            key = (pos.chain, pos.address)
            snap = snaps.get(key)
            if not snap:
                continue
            t = self.tokens.get(key)
            if t:
                self._apply_market(t, snap)
            st = t.stage.stage if t and t.stage else None
            for sig in exits.evaluate(pos, snap.get("price_usd"), snap.get("liquidity_usd"), st,
                                      self.cfg.exits):
                log.info("EXIT %s %s: %s", pos.symbol, sig.title, sig.detail)
                self.store.exit_event(pos, sig)
                await alerts.send_text(
                    self.http, self.cfg, exits.format_exit(pos, sig), symbol=pos.symbol,
                    address=pos.address, kind=sig.title,
                    link=alerts.chart_link(pos.chain, pos.pair_address or pos.address))
            self.store.save_position(pos)
            if pos.closed:
                self.positions.pop(key, None)
        self.store.commit()

    async def housekeeping(self) -> None:
        removed = self.store.prune(self.cfg.keep_days)
        if removed:
            log.info("pruned %d old snapshots", removed)

    # ------------------------------------------------------------------ run
    async def run(self, duration: float | None = None, dashboard: bool = False) -> None:
        cfg = self.cfg
        tasks = []
        if cfg.enable_dexscreener:
            tasks.append(self._every(cfg.dexscreener_discovery_interval, self.poll_dexscreener))
        if cfg.enable_geckoterminal:
            tasks.append(self._every(cfg.geckoterminal_interval, self.poll_geckoterminal))
        if cfg.enable_reddit:
            tasks.append(self._every(cfg.reddit_interval, self.poll_reddit))
        if self.pump:
            tasks.append(self.pump.run())
        tasks.append(self._every(cfg.score_interval, self.score_cycle))
        tasks.append(self._every(cfg.exits.check_interval, self.exit_cycle))
        tasks.append(self._every(3600, self.housekeeping))
        if not duration:  # long-running server: tell the phone it's alive
            watching = len(self.positions)
            tasks.append(alerts.send_text(self.http, cfg, f"✅ Meme radar started"
                                          + (f" · still watching {watching} alerted coin(s) for exits" if watching else "")))
        if dashboard:
            from .dashboard import serve
            tasks.append(serve(self.store, cfg))

        running = [asyncio.ensure_future(c) for c in tasks]
        try:
            if duration:
                await asyncio.wait(running, timeout=duration)
            else:
                await asyncio.gather(*running)
        finally:
            for task in running:
                task.cancel()
            await asyncio.gather(*running, return_exceptions=True)
            self.store.commit()
            await self.http.close()
