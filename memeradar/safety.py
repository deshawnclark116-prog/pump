"""The Shield: free contract-security checks (RugCheck for Solana, GoPlus for EVM)."""
from __future__ import annotations

from .config import SafetyRules
from .http import Http
from .models import Safety

GOPLUS_CHAIN_IDS = {"ethereum": "1", "bsc": "56", "base": "8453", "arbitrum": "42161",
                    "polygon": "137", "avalanche": "43114", "optimism": "10"}
SYSTEM_PROGRAM = "11111111111111111111111111111111"
BURN_ADDRESSES = {"0x0000000000000000000000000000000000000000",
                  "0x000000000000000000000000000000000000dead"}


def _num(x, default: float = 0.0) -> float:
    try:
        return float(x)
    except (TypeError, ValueError):
        return default


def evaluate_rugcheck(report: dict, rules: SafetyRules) -> Safety:
    reasons: list[str] = []
    if report.get("rugged"):
        reasons.append("flagged as rugged")
    if rules.reject_mint_authority and report.get("mintAuthority"):
        reasons.append("mint authority still enabled")
    if rules.reject_freeze_authority and report.get("freezeAuthority"):
        reasons.append("freeze authority enabled")
    for risk in report.get("risks") or []:
        if str(risk.get("level", "")).lower() == "danger":
            reasons.append(f"rugcheck: {risk.get('name')}")
    fee = _num((report.get("transferFee") or {}).get("pct"))
    if fee > rules.max_tax_pct:
        reasons.append(f"transfer fee {fee:.1f}%")

    # top-10 concentration, excluding pools/AMMs
    markets = report.get("markets") or []
    pool_accounts = {report.get("mint")}
    for m in markets:
        for k in ("pubkey", "liquidityA", "liquidityB"):
            if isinstance(m.get(k), str):
                pool_accounts.add(m[k])
    for addr, info in (report.get("knownAccounts") or {}).items():
        if (info or {}).get("type") == "AMM":
            pool_accounts.add(addr)
    pool_accounts.discard(None)
    holders = [h for h in report.get("topHolders") or []
               if h.get("owner") not in pool_accounts and h.get("address") not in pool_accounts]
    top10 = sum(_num(h.get("pct")) for h in holders[:10])
    if top10 > rules.max_top10_pct:
        reasons.append(f"top-10 holders own {top10:.1f}%")

    # hidden insider groups: many wallets funded/linked together, so top-10 looks spread out
    # while one group really holds most of the supply (the classic "bundled" pump.fun dump)
    supply = _num((report.get("token") or {}).get("supply"))
    if supply > 0:
        for net in report.get("insiderNetworks") or []:
            held = _num(net.get("currentHolding") or net.get("tokenAmount")) / supply * 100
            if held > rules.max_insider_pct:
                reasons.append(f"insider group of {net.get('size')} wallets holds {held:.0f}%")
                break

    # LP lock, judged on the deepest pool that actually has a fungible LP token. Exempt:
    #  - the pump.fun bonding curve ("pump_fun"; nothing to withdraw)
    #  - position-based pools (Meteora DLMM/DAMM v2, Orca whirlpools…) whose lpMint is the
    #    system program: there is no LP token to burn/lock, RugCheck always reports 0% there
    def depth(m: dict) -> float:
        lp = m.get("lp") or {}
        return _num(lp.get("baseUSD")) + _num(lp.get("quoteUSD"))

    lp_markets = [m for m in markets
                  if m.get("marketType") != "pump_fun"
                  and (m.get("lp") or {}).get("lpMint") not in (None, SYSTEM_PROGRAM)]
    lp_locked = None
    if lp_markets:
        lp_locked = _num((max(lp_markets, key=depth).get("lp") or {}).get("lpLockedPct"))
        if lp_locked < rules.min_lp_locked_pct:
            reasons.append(f"only {lp_locked:.0f}% of LP locked/burned")

    return Safety(passed=not reasons, reasons=reasons, top10_pct=round(top10, 2),
                  holders=report.get("totalHolders"), lp_locked_pct=lp_locked,
                  liquidity_usd=report.get("totalMarketLiquidity"))


def evaluate_goplus(info: dict, rules: SafetyRules) -> Safety:
    reasons: list[str] = []
    flag = lambda k: str(info.get(k, "0")) == "1"  # noqa: E731
    if flag("is_honeypot") or flag("cannot_buy") or flag("cannot_sell_all"):
        reasons.append("honeypot / cannot sell")
    if rules.reject_mint_authority and flag("is_mintable"):
        reasons.append("mint function enabled")
    for k in ("owner_change_balance", "hidden_owner", "selfdestruct", "transfer_pausable",
              "can_take_back_ownership"):
        if flag(k):
            reasons.append(k.replace("_", " "))
    for side in ("buy_tax", "sell_tax"):
        tax = _num(info.get(side)) * 100
        if tax > rules.max_tax_pct:
            reasons.append(f"{side.replace('_', ' ')} {tax:.1f}%")

    holders = [h for h in info.get("holders") or []
               if str(h.get("is_contract")) != "1" and str(h.get("is_locked")) != "1"
               and str(h.get("address", "")).lower() not in BURN_ADDRESSES]
    top10 = sum(_num(h.get("percent")) for h in holders[:10]) * 100
    if top10 > rules.max_top10_pct:
        reasons.append(f"top-10 holders own {top10:.1f}%")

    lp_holders = info.get("lp_holders") or []
    lp_locked = None
    if lp_holders:
        lp_locked = 100 * sum(_num(h.get("percent")) for h in lp_holders
                              if str(h.get("is_locked")) == "1"
                              or str(h.get("address", "")).lower() in BURN_ADDRESSES)
        if lp_locked < rules.min_lp_locked_pct:
            reasons.append(f"only {lp_locked:.0f}% of LP locked/burned")

    return Safety(passed=not reasons, reasons=reasons, top10_pct=round(top10, 2),
                  holders=int(_num(info.get("holder_count"))) or None, lp_locked_pct=lp_locked)


async def check(http: Http, chain: str, address: str, rules: SafetyRules) -> Safety | None:
    """Returns None when the API gave no answer (retry later)."""
    if chain == "solana":
        report = await http.get(f"https://api.rugcheck.xyz/v1/tokens/{address}/report")
        return evaluate_rugcheck(report, rules) if isinstance(report, dict) and report.get("mint") else None
    chain_id = GOPLUS_CHAIN_IDS.get(chain)
    if chain_id:
        data = await http.get(f"https://api.gopluslabs.io/api/v1/token_security/{chain_id}"
                              f"?contract_addresses={address}")
        info = ((data or {}).get("result") or {}).get(address.lower())
        return evaluate_goplus(info, rules) if info else None
    return Safety(passed=rules.allow_unverified, reasons=[] if rules.allow_unverified
                  else [f"no free security API for {chain}"])
