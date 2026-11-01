# memeradar: a free meme-coin momentum radar

memeradar spots meme coins that are speeding up while filtering out obvious rugs. It runs the
blueprint (ingestion → safety shield → scoring → alerts → backtesting) **at $0/month**. It needs no
paid APIs and no API keys, only one Python dependency (`aiohttp`), and runs as a single process.

> ⚠️ This tool is not financial advice. Meme coins are extremely high risk. The safety checks
> lower rug risk but cannot remove it, and a high score means *momentum*, not *quality*.

## How the paid blueprint maps to free parts

| Blueprint layer | Paid version | What this repo uses (free) |
|---|---|---|
| New pools / launches | Helius / QuickNode / Alchemy WebSockets | **PumpPortal** free WebSocket (every pump.fun launch + live trades with wallet addresses), **GeckoTerminal** new/trending pools, **DexScreener** latest profiles & boosts |
| Volume / liquidity | Birdeye API | **DexScreener** public API (300 req/min, no key) |
| Smart-money tracking | Nansen, paid indexers | Your own wallet list (`smart_wallets.txt`) matched against PumpPortal live trades |
| X / Twitter | X API ($200+/mo) | **Reddit public RSS** ($TICKER cashtags + contract addresses, counted by unique author). X is left out because it has no usable free tier |
| Contract security | n/a | **RugCheck** (Solana) and **GoPlus** (EVM), both free without keys |
| Kafka / Redpanda | streaming cluster | `asyncio` tasks in one process |
| Redis rolling windows | Redis | in-memory `RollingEvents` deques |
| Postgres / ClickHouse | managed DB | **SQLite** file |
| Grafana / Next.js | hosted UI | built-in dashboard at `http://127.0.0.1:8080` |
| Alerts | n/a | **Discord webhook** and/or **Telegram bot**, both free |

## Quick start

```bash
python3 -m venv .venv && . .venv/bin/activate     # Python 3.11+
pip install -r requirements.txt
python -m memeradar run --dashboard               # open http://127.0.0.1:8080
```

Alerts go to the console until you add a free alert channel:

```bash
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/..."   # Server settings → Integrations → Webhooks
export TELEGRAM_BOT_TOKEN="123:abc"  TELEGRAM_CHAT_ID="-100..."       # @BotFather, then add the bot to a channel
```

For tuning, copy `config.example.toml` to `config.toml`. To track smart money, copy
`smart_wallets.example.txt` to `smart_wallets.txt`.

## Pipeline

1. **Radar (ingestion).** Every new pump.fun token arrives over PumpPortal, and its trades are
   streamed for 30 minutes. DexScreener and GeckoTerminal add fresh and boosted tokens on Solana,
   Base and Ethereum. Contract addresses found in Reddit posts get resolved and tracked too.
2. **Phase-1 pre-filter.** A token is checked further only once it does at least $10k of volume
   per hour and has at least $3k of liquidity. This keeps the free safety APIs inside their rate
   limits. Tokens that never get there are dropped after 20 minutes.
3. **Shield (safety).** A token is rejected if any of these is true:
   - mint or freeze authority is still enabled
   - top-10 holders (pools and AMMs excluded) own more than 50%
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
   - `wallet_diversity`: unique buyers in the last 15 min, discounted by the **Gini coefficient**
     of buy sizes so a few whales can't fake breadth, plus holder count and top-10 spread.
     DexScreener buy/sell counts are the fallback for non-pump.fun tokens.
   - `liquidity_depth`: log scale, where $5k scores 0 and $500k scores 1.
   - `smart_money`: wallets from your `smart_wallets.txt` that bought in.
5. **Alerts and dashboard.** A token that scores at least `alert_threshold` (0.60) triggers a
   Discord/Telegram message, with a 60-minute cooldown per token. Every score is stored in SQLite.
6. **Backtest and tune (Phase 4).**
   ```bash
   python -m memeradar backtest            # hit rate (+50% within 1h) per threshold
   python -m memeradar tune                # random search for W1..W5, prints a [weights] block
   ```
   The backtest only uses data the radar has already collected. Let it run for a few days before
   you trust the tuned weights.

## Running it 24/7 for free

- **Your own PC, an old laptop or a Raspberry Pi.** This is the simplest option.
  `nohup python -m memeradar run --dashboard &`
- **Oracle Cloud "Always Free" VM** (ARM, 4 cores, 24 GB) or **Google Cloud e2-micro** (free
  tier). Both are always on and keep the PumpPortal stream connected. Run it under
  `systemd` or `tmux`.
- **GitHub Actions** (`.github/workflows/radar.yml`). Each run lasts about 13 minutes, and the
  database is kept in the Actions cache between runs. Public repos get unlimited minutes. Private
  repos get 2,000 minutes a month, which is roughly 150 runs. Add your webhook secrets, then
  uncomment the `schedule` block. Because this mode isn't continuous, it misses some very early
  pump.fun moves.

## Known limits of the free stack

- Without X/Twitter, social signal mostly comes from Reddit. With no Reddit chatter a token can
  score at most 0.70, so lower `social_velocity` or the threshold if you want more alerts.
- GeckoTerminal's free tier returns 429s when it's busy. The client backs off automatically.
- "Smart money" is only as good as your wallet list, and live trades are only available for
  pump.fun tokens.
- RugCheck and GoPlus limits are undocumented. The client spaces out calls (about 1 every
  1–2 seconds) and checks at most 8 tokens per cycle.

## Development

```bash
pip install pytest && pytest -q
python -m memeradar -v run --duration 120 --no-pumpportal   # short smoke run
```
