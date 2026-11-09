# $25 beginner-mode PAPER trading — 2026-11-09

This is a prospective research experiment, not live-money trading. It
replaces **new** $250 experimental-paper entries with eight independently
funded $25 accounting books; historical $250 trades remain in their original
ledgers for grading and exits. No trade receipts are backfilled or resized.

## Eight totally independent accounts

| Raw-signal model | Paper account A | Paper account B |
|---|---|---|
| Social Attention | $25 cash, $1 stakes | $25 cash, $5 stakes |
| OSECE Early Signal | $25 cash, $1 stakes | $25 cash, $5 stakes |
| OSECE Cross-Source | $25 cash, $1 stakes | $25 cash, $5 stakes |
| OSECE Early Liquidity | $25 cash, $1 stakes | $25 cash, $5 stakes |

Each model relies solely on its own qualifying source/signal gate.
The existing model must first pass the same safety checks and quotes as
before. A further observed quote at least 20 seconds after the original
model's eligible entry observation is required to allocate micro cash.
A signal never uses the same price observation to fill its micro entry.
Each micro account has a stable independent starting cash balance and
is never refilled; separate $1 and $5 accounts never borrow from one
another. On eligible exits, received simulated proceeds replenish
only that same account.

The $1 account permits at most 15 simultaneous positions, the $5
account at most four. A coin-specific cooldown applies after any filled
entry. A cash-insufficient account cannot buy. There are NO leverage,
fractional stakes above balance, automated capital injections or
live-wallet connections.

## Costs, P&L and realism limits

The simulator applies 1.25% fee and 1% slippage per side plus a
conservative illustrative $0.02 fixed per-side transaction overhead.
That overhead is a hypothesis, not a verified Jupiter/Raydium quote;
real SOL-network fees, priority fees, DEX pool fees and price impact can
differ and some tiny $1 swaps may be impractical or not executable.

A same-price roundtrip therefore loses money even at a $1 nominal size,
and fixed overhead takes a larger percentage of $1 than $5.
All P&L displayed is **net of these modeled costs**, not actual exchange
execution. On quote gaps, keep positions OPEN and mark estimated
equity unavailable, never pretend an exit occurred at the stop price.
Use latest real snapshots for modeled exits. Exit thresholds follow the
existing model family: Social Attention +20% net target / -20% net
stop / 30min hold; OSECE +20% net target / -12% net stop /
20min hold. Quotes can gap beyond stop levels.

## Live results without six-hour waits

Issue #12 is the public GitHub dashboard:

https://github.com/deshawnclark116-prog/pump/issues/12

The active radar updates one issue comment about every 10 minutes,
with update timestamp, run ID, eight balances, number open, number
closed, wins/losses, net realized P&L, estimated equity only when
positions are priced, and recent named-by-contract trades. No raw
GitHub secrets, on-chain wallet keys, real trade execution, or
private social post content is uploaded.

This GitHub Actions status is near-real-time only while that runner is
healthy. A stale issue timestamp means the writer or runner has not
updated. It must not be interpreted as proof the strategy is idle.

At the end of each six-hour radar segment, the normal research artifact
also exports `research-output/micro-paper-report.json` along with the
full SQLite ledger. Archive reconciliation remaps micro account receipts
by model + stake + mint + original signal timestamp instead of trusting
forked SQLite row IDs. Archive recovery never invents entry/exit fills.

The original unmodified social and OSECE signal components now hand
qualifying intent to the micro accounts. All other previously retired
bots remain disabled. New micro accounts begin only from information
observed after activation.

Do not conclude that a model has predictive edge from a high hit rate
or a single positive day. Compare each model/size combination's net
profit, risk of ruin, unpriced positions and slippage assumptions
forward in time.
