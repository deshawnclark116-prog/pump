# November 10, 2026 — Social Attention guarded arm and OSECE collision research

## Grounded failure diagnosis before changes

Data: actual persisted paper receipts and source/market snapshots from completed
GitHub radar #83, archived November 10, 2026, at 9:03 AM Eastern.

- $1 Social Attention: 27 completed trades, 10 wins, 17 losses, net
  -$4.6991 from $25. Five trades lost more than 45% of a $1 stake.
- $5 Social Attention: 25 completed trades, 9 wins, 16 losses, net
  -$19.87; $5.13 cash remains. **No reset or recapitalization.**
- Severe social losses can occur with high buy transaction counts when
  sell transactions are almost as numerous (e.g., 1345/1261,
  1042/1032 and 2206/2129 observed before entry). Mere total volume
  and market liquidity did not stop near-total quote-to-quote crashes.
- All previously reported source windows remain *consumed in-sample
  research*. No claim of validated predictive improvement.
- OSECE had 325 matched launches and 582 signals across books,
  with one previous v1b $10 simulated loss and no new $25 fills.
  Of 13 OSECE queued signals previously marked 'no timely fill',
  **12 had a recorded FAILED RugCheck** and one had UNKNOWN.
  Those failures reflect proper safety abstentions, not quote
  unavailability. This PR labels a known failed safety check explicitly.
- The original OSECE collision had 39 matched two-source signals,
  of which 30 received a quote during the signal lifetime, but zero
  passed its strict organic score/liquidity market gate. An alternative
  **$5k** liquidity flow screen saw about seven two-source candidate
  tokens, only one of which had a contemporaneous passed safety check.
  As such, zero or very few simulated buys is a valid result.

## Independent $25 prospective test arms

Both arms begin **fresh**, with independent $25 cash accounts at $1
and $5 position stakes, and cannot spend or borrow from the four
incumbent strategies or resurrect old entries.

### Social Attention Guarded 2026-11-10

This arm receives the *same real social signal* as the existing
Social Attention, but buys only after a later independent market quote
and all of these test-only filters:

- Fresh RugCheck passed and market quote available; never assume
  missing history means safe.
- Two separately observed recent quotes at least 20 seconds apart.
- >= $25k liquidity in both quotes, >= $1k observed five-minute traded
  volume and >= $500 buy-flow proxy in both.
- >=15 buy transactions and >=2 sell transactions in each; buys must
  exceed sells by **25%** in both consecutive snapshots.
- Entry quote at most 5% above the original source signal quote;
  no sudden >6% drop between the two observed confirmation quotes.
- Maximum six simultaneous $1 positions or two $5 positions. Preserves
  $25 total cash budget, cooldown and existing take-profit/stop/hold rules.

These thresholds are deliberately simple *unvalidated prospective
hypotheses*, not calibrated causal discovery or retrospectively
optimized winners. The original Social Attention controls remain
unchanged and are **not topped up**.

### OSECE Cross-Source Early-Liquidity 2026-11-10

This is a **new model identity**, not a modification of frozen OSECE
single-source or collision v1 models. It requires:

- Two independent cultural source families *observed before token
  creation*, with exact normalized full-token name matching;
- A minimum of $5,000 measured market liquidity, >=$500 real five-minute
  buy dollars, >=1.25x contemporaneous sell dollars, >=8 market traders;
- Verified mint and freeze authority audit, fresh passing RugCheck;
- Two successive confirmation quotes and a distinct later quote for
  paper entry, net model costs, cooldowns, and 20-minute exit ceiling.

Lower-liquidity pools can have severe slippage and sudden liquidity
withdrawals. Indicative prices are not on-chain executable estimates.
No relaxation of RugCheck or authority checks is allowed.

## Observability

Existing GitHub Issue #12 now displays all **twelve** $25 accounts
(six strategies at $1 and $5), including *zero* trade models, cash,
net realized P&L, unpriced open exposure, and signal rejection reasons.
The OSECE funnel distinguishes a failed RugCheck from unavailable
quotes, and the Social guarded arm shows why a candidate was declined.

Both modifications preserve old research/source receipts and previously
recorded simulated losses. Next promotion decision requires untouched
forward data: compare severe loss incidence, average net return
per opportunity, decision coverage, and the total no-trade/missed-win
cost; **do not select only the winning examples**.

There are no live wallet orders, swaps, token transfers, or real trades.
