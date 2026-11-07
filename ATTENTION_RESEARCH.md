# Attention Lead: independent Solana social-attention research lane

**Status: data collection only. No live swaps, wallet integration, or paper trades enabled.**
This is an *experiment* alongside Fast Radar, Graduation, Scalper, Recovery,
Under-2x, Pulse, Buyer Flow and Dip Reclaim. None of those strategies' decision
rules or ledgers are changed by this lane.

## What is implemented

| Script | Responsibility |
|---|---|
| `memeradar/attention/attention.py` | Authenticate to X, approved Reddit OAuth, and user-authorized Telegram channels. Validate Solana 32-byte mint; capture first-seen event, source, posted time, observed time, known engagement/audience. No raw post text stored. |
| `memeradar/attention/chain.py` | Poll DexScreener token pools every 30 seconds (max 150 active mints, batches of <=30), select the deepest REAL Solana base-token pool, save quotes, 5-minute buy/sell transaction counts, liquidity and five-minute volume. Run independently recorded RugCheck security checks. |
| `memeradar/attention/model.py` | Sample receipt-time social features, label FULLY observed next-60-minute *buy-volume proxy*, train a regularized log-volume regression with chronological, token-disjoint validation and baseline comparison. Require at least 14 days of evidence. |
| `memeradar/attention/backtest.py` | Replay only timestamps and evidence available at the time of each signal. Fill on the NEXT quote, apply safety/freshness checks, use 3x liquidity signal, exit on 75% arrived proxy, -30% net or 60 minutes. Count missing quotes as unresolved. |
| `memeradar/attention/trader.py` | Later optional **forward paper-only** simulator, independently gated by training validation and ATTENTION_PAPER_ENABLED. No Jupiter calls or signatures. |
| `memeradar/attention/db.py` | Own tables within the same existing SQLite file `memeradar.db`. Source-cursor state, de-duplication, quote history, RugCheck history, frozen model metrics and separate paper ledger. |
| `memeradar/attention/runner.py` | Run alongside the radar in the SAME process/SQLite database, so the existing GitHub Actions cache and research exports preserve it. |

## What is *not* established

A predicted buyer count or dollar inflow is not measurable from DexScreener's
5-minute volume and trade counts. The implementation computes a **volume-weighted
buy-transaction fraction** as a *proxy*:

`buy_volume_proxy_m5 = vol_m5 * buys_m5 / (buys_m5 + sells_m5)`

This is **NOT** wallet-level observed USD buys and does not establish social
causality. The next-hour label integrates the overlapping five-minute values
with the elapsed quote time, rather than repeatedly summing overlapping
five-minute windows. Gaps greater than 120 seconds fail the label, not become
zeros. An eventual Helius-based transaction feed could measure true buy/sell
USD and replace this proxy; that is NOT part of this version.

The 3x-liquidity rule is a fixed research hypothesis, not an optimized or
validated threshold; actual trade frequency can be **zero**. Models are not
allowed to learn from post publication timestamps earlier than when we
received the posts. `$TICKER`-only messages are not mapped automatically
because identical symbols appear on many different Solana mints.

## Required source setup (do not paste credentials into chat or commits)

**Telegram (best free starting source):**

1. Create your Telegram API ID/hash at `https://my.telegram.org` and
   join the public channels you are authorized to monitor.
2. On YOUR OWN computer (not in GitHub Actions), install `pip install -r
   requirements-attention.txt`, then run
   `python -m memeradar.attention.telegram_login`. Finish the Telegram login.
3. In GitHub repository **Settings > Secrets and variables > Actions > Secrets**,
   save `ATTENTION_TELEGRAM_API_ID`, `ATTENTION_TELEGRAM_API_HASH`, and
   `ATTENTION_TELEGRAM_SESSION`. **The StringSession is account access; never
   commit it, paste it into chat, or print it in GitHub Actions logs.**
4. Under **Variables**, set `ATTENTION_TELEGRAM_CHANNELS` to a
   comma-separated list of Telegram channel handles you have joined.
   The script only captures NEW channel messages, not backfilled old messages.

**X:** An approved X developer token is needed. Search is not generically
free: X bills by retrieved post. By default the collector makes **zero** X
searches. Set encrypted secret `ATTENTION_X_BEARER_TOKEN` and nonzero
variable `ATTENTION_X_POST_BUDGET_PER_DAY` ONLY if willing to pay and monitor
usage. The budget is a local approximation; the X developer billing console
is authoritative. Do not assume this is a free source.

**Reddit:** Current Reddit Data API access requires valid OAuth and an
approved use case. Avoid scraping an unauthenticated feed. The collector is
disabled by default. Only if approved for this research use case, set
`ATTENTION_REDDIT_CLIENT_ID` and `ATTENTION_REDDIT_CLIENT_SECRET` as encrypted
secrets and `ATTENTION_REDDIT_APPROVED=1` as a repository variable.
See the Reddit Data API Terms, including restrictions on algorithm/model
training, allowed uses and content deletion. Source text/usernames are not
persisted; `delete-post` removes a known event if a source requires deletion.

**Chain data:** No Helius key is required for this research prototype.
DexScreener and RugCheck are called only on validated mints. Unknown
RugCheck outcomes fail closed for trading. Quotes for failed-security mints
may still be stored to understand negative outcomes, but cannot be bought
by the candidate model.

## How to use it

The main `radar.yml` runner starts the collector automatically. When the
above social credentials are absent, sources report **disabled** instead of
generating false data. GitHub Actions archives
`research-output/attention-status.json` with its usual research SQLite
backup. To run locally without a simultaneous radar process:

```bash
pip install -r requirements-attention.txt
python -m memeradar.attention status
python -m memeradar.attention collect
```

On the **same host** never run a second collector against an already-running
radar database: attach it to the radar's event loop or run the collector
alone. Use `--db path/to/memeradar.db` to inspect a backed-up DB.
The status command reports event counts, source health, quote count, trained
models, and paper trade records. Do **not** infer no trade from an inaccessible
live GitHub log; use completed artifacts for authoritative saved ledger state.

After collecting 2–4 weeks of sufficiently complete real observations:

```bash
python -m memeradar.attention lag-report
python -m memeradar.attention train
# Only after a model has 'retrospective_pass' and after model creation:
python -m memeradar.attention backtest --model attention_ridge_YYYYMMDD_HHMMSS \
    --since 2026-12-01T00:00:00Z --until 2026-12-08T00:00:00Z
```

Training returns `insufficient_data` until minimum independent evidence
exists: >=14 days, >=300 train examples, >=100 holdout examples, >=50
train mints and at least one authorized social source. Validation uses
a time-sorted holdout with a 2-hour embargo and no overlapping mints, and
requires >5% error improvement over simple baselines. A validation pass is
NOT proof of a tradable edge. Offline replay is allowed only **after**
the model creation timestamp, so the same holdout isn't re-used as an
independent trading backtest. No profits or entry predictions are claimed
until actual out-of-sample evidence exists.

## Risk and promotion gates

- Run **data collection only** for 2–4 weeks before testing.
- Freeze model version and rules before a fresh, independent out-of-sample
  replay. Preserve all predictions and unresolved trades.
- Only then consider changing `ATTENTION_PAPER_ENABLED` from the hard-coded
  `0` to an explicit opt-in. Forward paper trade for at least 1–2 weeks,
  independently of all existing scalper ledgers.
- Measure true execution feasibility. Simulated 1% side slippage and 1.25%
  side fee may understate losses in highly volatile or low-liquidity mints.
  The spread, liquidity depletion, priority fees, MEV and failed swaps are
  NOT modeled; a hypothetical profitable backtest isn't sufficient.
- **Never automatically deploy real Jupiter swaps.** A separate security,
  risk and data validation stage is required. There is deliberately no
  wallet/transaction path in this code.

### Source documentation

- DexScreener: https://docs.dexscreener.com/api/reference
- RugCheck full reports: https://api.rugcheck.xyz
- X paid read pricing: https://developer.x.com/
- Reddit Data API policy: https://redditinc.com/policies/data-api-terms
