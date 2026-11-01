"""GeckoTerminal (free, no key, ~30 req/min): new + trending pools per network."""
from __future__ import annotations

from ..http import Http

BASE = "https://api.geckoterminal.com/api/v2"
NETWORK = {"solana": "solana", "ethereum": "eth", "base": "base", "bsc": "bsc",
           "arbitrum": "arbitrum", "polygon": "polygon_pos"}


async def discover(http: Http, chains: list[str]) -> list[dict]:
    out = []
    for chain in chains:
        net = NETWORK.get(chain)
        if not net:
            continue
        for kind in ("new_pools", "trending_pools"):
            data = await http.get(f"{BASE}/networks/{net}/{kind}?page=1")
            for pool in (data or {}).get("data", []) or []:
                rel = ((pool.get("relationships") or {}).get("base_token") or {}).get("data") or {}
                token_id = rel.get("id", "")
                if "_" not in token_id:
                    continue
                addr = token_id.split("_", 1)[1]
                out.append({"chain": chain, "address": addr, "source": f"geckoterminal/{kind}"})
    return out
