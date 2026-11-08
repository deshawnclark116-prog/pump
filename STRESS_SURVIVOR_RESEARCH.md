# Stress-Survivor v0 — a new falsifiable Solana paper-trading hypothesis

**Status:** Unvalidated forward paper experiment only. Not an established edge.
Nothing below buys real tokens, requires a wallet, or changes existing models.

## Idea: buy the coin that *refuses* to fall under sell pressure

Previous models bought attractive-looking token conditions: posts, trending
rank, buyer-count growth, breakouts, and dips reclaiming. Most Saturday November
7, 2026 paper trades lost money. We now test a different question:

> **When a group of other tracked Solana tokens is selling off, which token
> retains its price and liquidity EVEN THOUGH its own last-five-minute
> transaction counts favor SELL transactions? Does that unusual resistance
> precede a profitable rebound AFTER the wider selloff stops worsening?**

This is a **cross-sectional negative-control stress test** rather than a
one-token momentum or "buy the dip" rule. We cannot claim nobody has ever
used relative strength or price absorption; the combination is our new
specific experiment and still needs to earn statistical credibility.

## Exact v0 frozen selection rule

1. Poll free DexScreener snapshots (currently every 30 seconds) for all
   tracked attention-discovered Solana mints. Exclude zero/invalid prices,
   mcap below $25k, liquidity below $20k, stale quotes, and pool switches.
   Require at least 10 *other* tracked tokens with valid current and ~5m
   earlier prices.
2. **Market stress:** the median ~5m return of the *other* peers is
   <= -0.6%, and at least 60% of peers are down. Avoid using the
   candidate's own return to manufacture the peer benchmark.
3. **Resilient coin:** its ~5m price change is between -0.8% and +1.5%,
   outperforming the peer median by at least 1.2 percentage points.
   Its liquidity retains at least 96% of its earlier level.
4. **Adverse own trade pressure:** five-minute DexScreener *SELL
   transaction count* is at least 1.10x BUY count and >=10. At least
   $2,000 total five-minute volume. **This does not prove net sell
   dollar volume:** trades can vary widely in size.
5. **Safety:** a RugCheck passing result must have been received at or
   before the signal, and cannot be older than one hour.
6. **Second confirmation:** 60-210s after first watch, the coin's
   price cannot fall >1% or spike >3.5%; liquidity cannot fall >3%;
   the peers' median drawdown cannot worsen by >0.2 percentage points.
   Then queue an entry — never fill on this same quote.
7. **Entry:** next fresh quote >=20 seconds later, before a 180s expiry.
   Fail closed if RugCheck expires, the pool loses depth, price drops
   >3% or rallies >4% versus original watched quote.
8. **Exit:** +12% net take profit, -10% net stop, liquidity loss
   >20% from entry, or a 15-minute time exit. Quotes arriving too late
   to simulate an actual fill do not become fabricated trades.
   Stop gaps can exceed -10%.

## Capital and testing

- $250 **simulated** starting cash, $10 per paper position, three open
  positions max, minimum four hours between watches/trades on same mint.
- Charge modeled **1.25% fees and 1% slippage on each side** via the
  existing attention backtest cost function; verify realized returns net.
- Separate SQLite tables: `stress_survivor_meta`,
  `stress_survivor_signals`, `stress_survivor_trades`. Preserve watches,
  confirmations, reasons for rejection, actual delayed fills, and full
  trade log across GitHub workflow restarts.
- `python -m memeradar.attention stress-report` shows each entry,
  exit, source peer statistics and realized P&L. Each completed workflow
  archives `research-output/stress-survivor-paper.json`.
- `STRESS_SURVIVOR_PAPER_ENABLED=1` starts the forward-only watch
  timestamps. Historical data may furnish a past price baseline but
  cannot create retroactive paper entries. Failures and zero-trade days
  must be reported honestly.

## How this idea is evaluated

**First**, accumulate raw, untouched forward signals and all failed/rejected
watches for at least 2 weeks. Grade every trade and worst excursions,
closing and unresolved positions separately.

**Then**, compare observed performance against contemporaneous peers with
similar liquidity, age/market cap and trading activity, a random
trending-token paper control, and a no-trade cash baseline. Use the same
entry lag, fill assumptions, fees, and liquidity rules for all arms.
Group correlated positions/coins; report returns by day and by token,
not only trade-by-trade win rates.

**Before considering promotion**, require an independent future period to
beat the controls after costs and realistic DEX executable-quote studies;
test with stricter slippage/MEV and a liquidity-withdrawal stress
scenario. No threshold rescue tuning on the same holdout. Paper-only
performance, especially at $10 stake, does not establish that real
market fills exist at those prices.

## Known limitations

- DexScreener five-minute BUY/SELL transaction counts are rolling,
  overlapping windows, and do not report exact buy/sell dollar notional.
- The observed tracked cohort is selected by attention feeds; this is not
  necessarily a representative measure of all Solana meme coins.
- We do not observe swaps/route quotes or guarantee contract exits.
- Price resilience can result from manipulation, stale pricing, or
  temporary liquidity support. RugCheck and pair-consistency filters
  reduce, but do not remove, these threats.

All rules fixed before forward test; the experimental book never feeds
its trades into the original trading models.
