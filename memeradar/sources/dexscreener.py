"""DexScreener (free, no key): discovery of fresh/boosted tokens + market snapshots."""
from __future__ import annotations

import logging
from collections import defaultdict

from ..http import Http

log = logging.getLogger(__name__)
BASE = "https://api.dexscreener.com"

DISCOVERY_ENDPOINTS = (
    "/token-profiles/latest/v1",   # tokens that just got a DexScreener profile
    "/token-boosts/latest/v1",     # tokens that just bought boosts (= someone is spending on hype)
    "/token-boosts/top/v1",
)


async def discover(http: Http, chains: list[str]) -> list[dict]:
    """Return [{chain, address, source}] for recently promoted tokens on the given chains."""
    out: dict[tuple[str, str], dict] = {}
    for path in DISCOVERY_ENDPOINTS:
        data = await http.get(BASE + path)
        if not isinstance(data, list):
            continue
        for item in data:
            chain, addr = item.get("chainId"), item.get("tokenAddress")
            if chain in chains and addr:
                out.setdefault((chain, addr), {"chain": chain, "address": addr,
                                               "source": "dexscreener" + path.split("/v1")[0]})
    return list(out.values())


def _f(x) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def parse_pair(pair: dict) -> dict:
    txns = pair.get("txns") or {}
    vol = pair.get("volume") or {}
    liq = (pair.get("liquidity") or {}).get("usd")
    created = pair.get("pairCreatedAt")
    return {
        "pair_address": pair.get("pairAddress", ""),
        "dex_id": pair.get("dexId", ""),
        "symbol": (pair.get("baseToken") or {}).get("symbol", ""),
        "name": (pair.get("baseToken") or {}).get("name", ""),
        "price_usd": _f(pair.get("priceUsd")) or None,
        "liquidity_usd": _f(liq) if liq is not None else None,
        "market_cap": _f(pair.get("marketCap") or pair.get("fdv")) or None,
        "vol_m5": _f(vol.get("m5")),
        "vol_h1": _f(vol.get("h1")),
        "buys_m5": int(_f((txns.get("m5") or {}).get("buys"))),
        "sells_m5": int(_f((txns.get("m5") or {}).get("sells"))),
        "buys_h1": int(_f((txns.get("h1") or {}).get("buys"))),
        "sells_h1": int(_f((txns.get("h1") or {}).get("sells"))),
        "pair_created_at": created / 1000 if created else None,
        "price_native": _f(pair.get("priceNative")) or None,
        "quote_symbol": ((pair.get("quoteToken") or {}).get("symbol") or "").upper(),
        "price_change": {k: _f(v) for k, v in (pair.get("priceChange") or {}).items()},
    }


# launchpad bonding-curve pools: measuring from their start would make every graduated coin look
# "late" (graduation alone is ~14x), so run-up is measured from the open-market pools instead
LAUNCHPAD_DEX_HINTS = ("pumpfun", "launchlab", "dbc", "bonding", "moonshot", "moonit", "boop", "bonk", "believe")
MAX_SANE_RUN = 50.0   # ignore pools that imply >50x (seeded at a silly price, e.g. a fresh DLMM bin)


def token_history(pairs: list[dict]) -> dict:
    """Facts about the coin across ALL its pools, so a brand-new pool on an old coin
    (a coin moving to Raydium or a new pairing) isn't mistaken for a fresh launch."""
    created = [_f(p.get("pairCreatedAt")) / 1000 for p in pairs if p.get("pairCreatedAt")]
    starts = []
    for p in pairs:
        dex = (p.get("dexId") or "").lower()
        if any(h in dex for h in LAUNCHPAD_DEX_HINTS):
            continue
        mcap = _f(p.get("marketCap") or p.get("fdv"))
        changes = [_f(v) for k, v in (p.get("priceChange") or {}).items() if k in ("h6", "h24")]
        for pc in changes:
            if mcap and -100 < pc < (MAX_SANE_RUN - 1) * 100:
                starts.append(mcap / (1 + pc / 100))
    return {"token_created_at": min(created) if created else None,
            "start_mcap": min(starts) if starts else None}


async def snapshots(http: Http, chain: str, addresses: list[str]) -> dict[str, dict]:
    """Best (most liquid / most traded) pair per token address, 30 tokens per request."""
    result: dict[str, dict] = {}
    for i in range(0, len(addresses), 30):
        batch = addresses[i:i + 30]
        data = await http.get(f"{BASE}/tokens/v1/{chain}/{','.join(batch)}")
        if not isinstance(data, list):
            continue
        by_token: dict[str, list[dict]] = defaultdict(list)
        for pair in data:
            base = (pair.get("baseToken") or {}).get("address")
            if base:
                by_token[base].append(pair)
        for addr, pairs in by_token.items():
            best = max(pairs, key=lambda p: (_f((p.get("liquidity") or {}).get("usd")),
                                             _f((p.get("volume") or {}).get("h1"))))
            result[addr] = parse_pair(best)   # the batch endpoint only returns the top pool
    return result


async def pools_history(http: Http, chain: str, address: str) -> dict | None:
    """token_history() over every pool of one coin (one request; the batch endpoint omits pools)."""
    data = await http.get(f"{BASE}/token-pairs/v1/{chain}/{address}")
    if not isinstance(data, list) or not data:
        return None
    pairs = [p for p in data if (p.get("baseToken") or {}).get("address") == address]
    return token_history(pairs or data)


async def lookup_any_chain(http: Http, address: str) -> tuple[str, dict] | None:
    """Resolve a bare contract address (e.g. scraped from Reddit) to (chain, snapshot)."""
    data = await http.get(f"{BASE}/latest/dex/tokens/{address}")
    pairs = (data or {}).get("pairs") or []
    pairs = [p for p in pairs if (p.get("baseToken") or {}).get("address", "").lower() == address.lower()]
    if not pairs:
        return None
    best = max(pairs, key=lambda p: _f((p.get("volume") or {}).get("h1")))
    return best.get("chainId"), {**parse_pair(best), **token_history(pairs)}
