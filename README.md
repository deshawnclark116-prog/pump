# memeradar: a free meme-coin momentum radar

memeradar spots meme coins that are speeding up while filtering out obvious rugs. It runs the
blueprint (ingestion → safety shield → scoring → alerts → backtesting) **at $0/month**. It needs no
paid APIs and no API keys, only one Python dependency (`aiohttp`), and runs as a single process.

> ⚠️ This tool is not financial advice. Meme coins are extremely high risk. The safety checks
> lower rug risk but cannot remove it, and a high score means *momentum*, not *quality*.

## How the paid blueprint maps to free parts

| Blueprint layer | Paid version | What this repo uses (free) |
|---|---|---|
| New pools / launches | Helius / QuickNode / Alchemy WebSockets | **PumpPortal** free WebSocket (every pump.fun launch), **GeckoTerminal** new/trending pools, **DexScreener** latest profiles & boosts |
| Buyer activity | Paid trade streams | **GeckoTerminal** unique buyers/sellers per 5/15/30/60 min, 30 coins per request |
| Volume / liquidity | Birdeye API | **DexScreener** public API (300 req/min, no key) |
| Smart-money tracking | Nansen, paid indexers | Off by default. Optional wallet list, only works with a funded PumpPortal key |
| X / Twitter | X API ($200+/mo) | **Reddit public RSS** ($TICKER cashtags + contract addresses, counted by unique author). X is left out because it has no usable free tier |
| Contract security | n/a | **RugCheck** (Solana) and **GoPlus** (EVM), both free without keys |
| Kafka / Redpanda | streaming cluster | `asyncio` tasks in one process |
| Redis rolling windows | Redis | in-memory `RollingEvents` deques |
| Postgres / ClickHouse | managed DB | **SQLite** file |
| Grafana / Next.js | hosted UI | built-in dashboard at `http://127.0.0.1:8080` |
| Alerts | n/a | **ntfy** phone app (no account), **Discord webhook** and/or **Telegram bot**, all free |

## Quick start

```bash
python3 -m venv .venv && . .venv/bin/activate     # Python 3.11+
pip install -r requirements.txt
python -m memeradar run --dashboard               # open http://127.0.0.1:8080
```

Alerts go to the console until you add a free alert channel:

```bash
export NTFY_TOPIC="my-secret-topic"                                  # ntfy app → + → subscribe to that name
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."   # Server settings → Integrations → Webhooks
export TELEGRAM_BOT_TOKEN="123:abc"  TELEGRAM_CHAT_ID="-100..."       # @BotFather, then add the bot to a channel
```

For tuning, copy `config.example.toml` to `config.toml`. To track smart money, copy
`smart_wallets.example.txt` to `smart_wallets.txt`.

## Early or late? The stage detector

A coin with high volume might be just starting, or it might be the top. Every coin gets a label
built only from its own data:

| Stage | Meaning | Alerts? |
|---|---|---|
| 🟢 **EARLY** | Under 4h old, under 3x from launch, new buyers arriving at least 1.3x faster than the 10 min before, more buyers than sellers | yes |
| 🟡 **RUNNING** | Moving and still bought, but already up, or an older coin | dashboard only (add `"RUNNING"` to `alert_stages` to change that) |
| 🔴 **LATE** | 8x or more already, 20%+ off its peak, or new buyers drying up after a 2x+ run | no |
| 🔴 **DUMPING** | 35%+ off its peak, or sellers clearly outnumber buyers | no |

The final score is momentum × stage (EARLY ×1.0, RUNNING ×0.75, LATE ×0.3, DUMPING ×0), so a
coin that has already ripped can't top the list. "Run-up" is measured from launch for pump.fun
coins, from the 24h price change for coins under a day old, and from today's gain for older
coins. "Peak" means the highest price seen since the radar started watching. All of the
thresholds are in the `[stage]` section of `config.example.toml`.

## Exit alerts

Every alerted coin keeps being watched every 10 seconds, for up to 6 hours:

| Alert | When |
|---|---|
| 🟠 TAKE PROFIT | up +50%, +100%, +200% since the alert (each once) |
| 🟠 FADING | stage turned LATE: buyers drying up |
| 🔴 GET OUT | 25% off its high since the alert (once it has been up 10%+) |
| 🔴 STOP LOSS | 30% below the alert price |
| 🔴 DUMPING | sellers swamping buyers |
| 🔴 RUG WARNING | half the pool's liquidity pulled since the alert |

A red alert ends the watch. To see every alert and what it did afterwards:

```bash
python -m memeradar report
```

## Fake-money trading (paper mode)

Every entry alert also makes a pretend $5 buy, and the first red exit alert (or the end of the 6h
watch) sells it. Fees (1.25% each way) and slippage (2% each way) are charged like a real trade. Your
phone gets a 🤖 PAPER BUY / PAPER SELL message with the fake balance (starts at $100). No wallet,
no real money. To see every fake trade:

```bash
python -m memeradar paper
```

## Pipeline

1. **Radar (ingestion).** Every new pump.fun token arrives over PumpPortal. DexScreener and GeckoTerminal add fresh and boosted tokens on Solana,
   Base and Ethereum. Contract addresses found in Reddit posts get resolved and tracked too.
2. **Phase-1 pre-filter.** A token is checked further only once it does at least $10k of volume
   per hour, or $3k in the last 5 minutes (the fast lane for new launches), and has at least $3k
   of liquidity. This keeps the free safety APIs inside their rate
   limits. Tokens that never get there are dropped after 20 minutes.
3. **Shield (safety).** A token is rejected if any of these is true:
   - mint or freeze authority is still enabled
   - top-10 holders (pools and AMMs excluded) own more than 50%
   - one hidden group of linked wallets (RugCheck "insider network") holds more than 40%, the
     "bundled" trick where hundreds of wallets make the top-10 look spread out
   - tax or transfer fee is above 5%
   - it is a honeypot or has owner-controlled balances
   - less than 50% of the LP is locked or burned (bonding curves and position-based pools such
     as DLMM/CLMM are exempt because they have no LP token)
   - RugCheck flags a "danger" risk

   Rejected tokens are remembered so they are never checked again.
4. **Scoring.** Each feature is scored from 0 to 1, and the composite is a weighted mean:
   - `social_velocity`: unique Reddit authors in the last 30 min compared with the 2 h baseline.
     A contract-address hit counts fully. A bare `$TICKER` counts half, because tickers collide.
   - `volume_acceleration`: 1 h volume ÷ liquidity, plus how much faster the last 5 min traded
     than the hourly pace (adjusted for token age).
   - `wallet_diversity`: unique buyers vs. sellers in the last 15 min (GeckoTerminal), plus holder
     count and top-10 spread.
   - `liquidity_depth`: log scale, where $5k scores 0 and $500k scores 1.
   - `smart_money`: off by default (weight 0).

   Then the stage multiplier is applied (see above).
5. **Alerts and dashboard.** An **EARLY** token that scores at least `alert_threshold` (0.40) triggers a
   Discord/Telegram message, with a 60-minute cooldown per token. Every score is stored in SQLite.
6. **Backtest and tune (Phase 4).**
   ```bash
   python -m memeradar backtest            # hit rate (+50% within 1h) per threshold
   python -m memeradar tune                # random search for W1..W5, prints a [weights] block
   ```
   The backtest only uses data the radar has already collected. Let it run for a few days before
   you trust the tuned weights.

## Running it 24/7 for free

**Step-by-step guide: [DEPLOY.md](DEPLOY.md)**, one command on a free Oracle Cloud server.


- **Your own PC, an old laptop or a Raspberry Pi.** This is the simplest option.
  `nohup python -m memeradar run --dashboard &`
- **Oracle Cloud "Always Free" VM** (ARM, 4 cores, 24 GB) or **Google Cloud e2-micro** (free
  tier). Both are always on and keep the PumpPortal stream connected. Run it under
  `systemd` or `tmux`.
- **GitHub Actions** (`.github/workflows/radar.yml`). Add the `NTFY_TOPIC` secret and it runs by
  itself: ~6h runs back to back, database kept in the Actions cache. Free and unlimited for public
  repos (private repos only get 2,000 min/month, not enough for 24/7).

## Known limits of the free stack

- Without X/Twitter, social signal mostly comes from Reddit, and most coins have none.
- PumpPortal's per-trade feed (individual wallets) now needs an API key funded with 0.02 SOL.
  It isn't needed, because buyer counts come from GeckoTerminal for free. If you get a key anyway,
  set `PUMPPORTAL_API_KEY` and per-wallet data turns on.
- Buyer counts refresh about every 20 seconds, so "early" means minutes 2 to 15, not the launch
  second. Bots will always be faster.
- The radar can't see a peak from before it started watching a coin. Run it continuously.
- The 0.40 threshold is a starting guess. Use `backtest` after a few days to set it properly.
- GeckoTerminal's free tier returns 429s when it's busy. The client backs off automatically.
- RugCheck and GoPlus limits are undocumented. The client spaces out calls (about 1 every
  1–2 seconds) and checks at most 8 tokens per cycle.

## Development

```bash
pip install pytest && pytest -q
python -m memeradar -v run --duration 120 --no-pumpportal   # short smoke run
```
