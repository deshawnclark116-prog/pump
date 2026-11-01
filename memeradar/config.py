"""Configuration: defaults <- config.toml <- environment variables."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path


@dataclass
class Weights:
    social_velocity: float = 0.30
    volume_acceleration: float = 0.30
    wallet_diversity: float = 0.20
    liquidity_depth: float = 0.10
    smart_money: float = 0.10

    def as_dict(self) -> dict[str, float]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass
class SafetyRules:
    max_top10_pct: float = 50.0          # top-10 holders (excluding pools) may own at most this %
    max_tax_pct: float = 5.0             # buy/sell tax ceiling (EVM)
    min_lp_locked_pct: float = 50.0      # LP burned/locked share (ignored on bonding curves)
    reject_mint_authority: bool = True
    reject_freeze_authority: bool = True
    allow_unverified: bool = False       # chains without a free security API


@dataclass
class Config:
    # discovery
    chains: list[str] = field(default_factory=lambda: ["solana", "base", "ethereum"])
    enable_pumpportal: bool = True
    enable_dexscreener: bool = True
    enable_geckoterminal: bool = True
    enable_reddit: bool = True
    subreddits: list[str] = field(default_factory=lambda: [
        "memecoins", "solanamemecoins", "CryptoMoonShots", "SatoshiStreetBets"])

    # polling cadence (seconds) – tuned to stay inside free rate limits
    dexscreener_discovery_interval: int = 60
    geckoterminal_interval: int = 120
    reddit_interval: int = 120
    score_interval: int = 30

    # tracking
    max_tracked_tokens: int = 300
    track_ttl_minutes: int = 120          # drop tokens that have gone quiet this long
    pumpportal_trade_ttl_minutes: int = 30

    # Phase-1 pre-filter (applied before the safety APIs are called)
    min_volume_h1_usd: float = 10_000
    min_liquidity_usd: float = 3_000

    # alerting
    alert_threshold: float = 0.60
    alert_cooldown_minutes: int = 60
    discord_webhook_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # storage / UI
    db_path: str = "memeradar.db"
    smart_wallets_file: str = "smart_wallets.txt"
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8080

    weights: Weights = field(default_factory=Weights)
    safety: SafetyRules = field(default_factory=SafetyRules)

    smart_wallets: set[str] = field(default_factory=set)


ENV_MAP = {
    "DISCORD_WEBHOOK_URL": "discord_webhook_url",
    "TELEGRAM_BOT_TOKEN": "telegram_bot_token",
    "TELEGRAM_CHAT_ID": "telegram_chat_id",
    "MEMERADAR_DB": "db_path",
}


def _apply(obj, data: dict) -> None:
    for key, value in data.items():
        if not hasattr(obj, key):
            raise ValueError(f"unknown config key: {key}")
        current = getattr(obj, key)
        if isinstance(value, dict) and not isinstance(current, dict):
            _apply(current, value)
        else:
            setattr(obj, key, value)


def load_config(path: str | None = None) -> Config:
    cfg = Config()
    p = Path(path or os.environ.get("MEMERADAR_CONFIG", "config.toml"))
    if p.exists():
        with p.open("rb") as fh:
            _apply(cfg, tomllib.load(fh))
    for env, attr in ENV_MAP.items():
        if os.environ.get(env):
            setattr(cfg, attr, os.environ[env])
    wf = Path(cfg.smart_wallets_file)
    if wf.exists():
        cfg.smart_wallets = {
            line.split("#", 1)[0].strip()
            for line in wf.read_text().splitlines()
            if line.split("#", 1)[0].strip()
        }
    return cfg
