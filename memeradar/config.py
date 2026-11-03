"""Configuration: defaults <- config.toml <- environment variables."""
from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field, fields
from pathlib import Path

from .exits import ExitRules
from .paper import PaperRules
from .stage import StageRules


@dataclass
class Weights:
    social_velocity: float = 0.25
    volume_acceleration: float = 0.35
    wallet_diversity: float = 0.25     # unique buyers vs sellers
    liquidity_depth: float = 0.15
    smart_money: float = 0.0           # off by default; only matters with smart_wallets.txt

    def as_dict(self) -> dict[str, float]:
        return {f.name: getattr(self, f.name) for f in fields(self)}


@dataclass
class SafetyRules:
    max_top10_pct: float = 50.0          # top-10 holders (excluding pools) may own at most this %
    max_tax_pct: float = 5.0             # buy/sell tax ceiling (EVM)
    max_insider_pct: float = 40.0        # one linked wallet group ("insider network") may hold at most this %
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
    score_interval: int = 20

    # tracking
    max_tracked_tokens: int = 300
    track_ttl_minutes: int = 120          # drop tokens that have gone quiet this long
    pumpportal_trade_ttl_minutes: int = 30
    pumpportal_api_key: str = ""          # optional: PumpPortal's trade feed needs a key funded with 0.02 SOL

    # Phase-1 pre-filter (applied before the safety APIs are called)
    min_volume_h1_usd: float = 10_000
    min_liquidity_usd: float = 3_000
    # early lane: a young coin with a burst of volume in the last 5 min gets checked right away
    early_min_volume_m5_usd: float = 3_000

    # alerting
    alert_threshold: float = 0.40
    alert_cooldown_minutes: int = 60
    # wait before alerting: a coin that qualifies is watched this long first, and only alerted if
    # it is still healthy (filters the "alert, then crash 1 minute later" coins)
    confirm_minutes: float = 3.0
    confirm_max_drop_pct: float = 15.0   # cancel if price falls this much below where it qualified
    confirm_max_liq_drop_pct: float = 20.0
    alert_stages: list[str] = field(default_factory=lambda: ["EARLY"])   # add "RUNNING" for more, riskier alerts
    discord_webhook_url: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    ntfy_topic: str = ""                  # ntfy app topic name (or full https://server/topic URL)

    # storage / UI
    db_path: str = "memeradar.db"
    keep_days: int = 14                   # delete score history older than this (alerts are kept)
    smart_wallets_file: str = "smart_wallets.txt"
    dashboard_host: str = "127.0.0.1"
    dashboard_port: int = 8080

    weights: Weights = field(default_factory=Weights)
    safety: SafetyRules = field(default_factory=SafetyRules)
    stage: StageRules = field(default_factory=StageRules)
    exits: ExitRules = field(default_factory=ExitRules)
    paper: PaperRules = field(default_factory=PaperRules)

    smart_wallets: set[str] = field(default_factory=set)


ENV_MAP = {
    "DISCORD_WEBHOOK_URL": "discord_webhook_url",
    "TELEGRAM_BOT_TOKEN": "telegram_bot_token",
    "TELEGRAM_CHAT_ID": "telegram_chat_id",
    "NTFY_TOPIC": "ntfy_topic",
    "MEMERADAR_DB": "db_path",
    "PUMPPORTAL_API_KEY": "pumpportal_api_key",
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
