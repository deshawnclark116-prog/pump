# OSECE v1 — public cultural attention -> new Solana token -> forward paper trades

**Status:** Untrained, exploratory, paper-only. Frozen entry/exit thresholds on
November 8, 2026 BEFORE forward results. No live-money transactions or private
wallet. Existing meme radar and other independent paper strategies remain intact.

## Why this experiment is different

Rather than buy a coin because its price is rising or it is trending on
DexScreener, ask whether an unusual phrase appeared elsewhere on the internet
**before** a token with that phrase was first created. Does independent
corroboration improve profitability over a single source?

## Sources and cadence (all credential-free as verified on GitHub)

- **Bluesky Jetstream WebSocket** — live post creations; exact hashtags,
  case-sensitive phrase candidates, source identifier and unique DID hashes.
- **Wikimedia RecentChanges SSE** — new, non-bot English Wikipedia articles;
  edit/event and published timestamps.
- **Nostr public relay** — live public kind-1 notes; distinct public-key
  hashes; relay may fail and reconnect.
- **Google Trends US RSS** — newly trending search phrases; poll every 5m.
- **GDELT global news RSS** — new story titles, poll every 5m.
- **Hacker News** — recent story headlines, poll every 5m.
- **PumpPortal new-token events** — reuse the radar's EXISTING single free
  token-creation WebSocket; no duplicate connection and no paid trade stream.
- **Jupiter Tokens v2** — batch exact mint lookups for up to 80 watched mints
  every 45s. Store USD price, liquidity, five-minute buy USD, sell USD,
  Organic Score and audit fields. Missing values remain NULL.
- **Jupiter recent-first-pool** and **GeckoTerminal new Solana pools** —
  slower, timestamp-verified fallback token-launch detection every 3m.
  Ignore unknown creation timestamps; never backdate them into an earlier
  cultural observation.
- **RugCheck** — checked for tracked and Jupiter-indexed candidates before
  paper entry. All unknown/expired results fail closed.

Only fields directly observed from source responses are credited. No
ticker-to-contract guesses. Posting timestamps never replace the time our
system first received a source. Network outages never become zero attention.

## Frozen paper books (separate $250 fake balances, $10 positions)

1. **OSECE early, single-source baseline**: token name matches a phrase
   received before token creation from one independently qualifying source.
   Bluesky and Nostr each require at least THREE distinct authors within
   10 minutes, to avoid one promoter creating the entire "trend". Structured
   feeds qualify with one fresh event.
2. **OSECE cross-source collision**: same contract-name phrase was received
   from at least TWO separate source families before creation. A syndicated
   article copied multiple times inside a single feed is counted only as
   one source.

For both:
- Strict canonical token name match (or symbol only if at least six chars),
  prior sources received **15 seconds–30 minutes before creation**, maximum 45-minute
  wait for a viable Jupiter market.
- Two separate Jupiter market observations at least 30s apart must show
  >=$20k liquidity, measured five-minute buy USD >=$1,200, buy USD >=1.2x
  sell USD, Organic Score >=25, and explicit clean mint/freeze authority
  flags. Prices may not fall >6% or rise >12% between first two observations.
- A later third quote >=20s after confirmation is required for a simulated
  fill, with RugCheck passing and still-fresh market and buy/sell data.
  Reject >10% chase beyond confirmation; never fabricate an execution at
  signal time.
- Up to three simultaneous positions per book; two-hour same-coin cooldown;
  no position sizing beyond $10 fake.
- Exits: +20% net target, -12% net stop, >25% liquidity drain, or 20 minutes.
  Model fees 1.25% and slippage 1% EACH SIDE (from existing paper cost
  function); gaps past stop are possible.
- Stop/exit requires a later fresh valid quote. If no quote, position remains
  unresolved, not falsely realized.

The research registry includes all observed candidate terms, hours/number
of unpaired token launches, matched launches, every frozen signal and rejection,
Jupiter quotes, and every simulated paper fill/exit. Social authors and source
post IDs are HASHED; raw post texts are not stored. For high-volume social
streams, unconfirmed fragments stay in a bounded in-memory window; after
three distinct authors, no more than eight independent receipts per phrase
per ten-minute window enter SQLite. This suppresses generic chatter and
limits cache size.

## Research and promotion doctrine

The current hypotheses are not proven, calibrated, or globally novel.
A free data feed is not a predictive edge. Full replays must freeze first-seen
timestamps; historical source headlines known only in retrospect are
ineligible as entry signals.

The key null hypotheses are (a) no price edge from a cultural name match
versus ordinary new-token launches; (b) corroborating two sources adds
no incremental advantage after realistic transaction costs.

Forward evaluation will compare these two books against an independently
sampled, contemporaneous unmatched-launch baseline with the SAME
RugCheck, liquidity, Jupiter coverage, delay and fees, including zero-trade
days. Cluster by token/day; report unresolved positions and quote gaps,
not just winning closed trades. No real capital or claims of accuracy
until enough prospectively captured results beat controls.

## How to see trades

Run:

    python -m memeradar.osece report

Every six-hour GitHub radar run saves
research-output/osece-paper-report.json with source health, launches,
signal states, each coin traded, entry/exit and realized P&L.
Configured alert channels receive separate "OSECE PAPER" buy/sell
notifications. The code has no signer, wallet, or Jupiter swap endpoint.

The GitHub Actions input flags are OSECE_ENABLED=1 and
OSECE_PAPER_ENABLED=1. Turning the second OFF keeps data collection running.
