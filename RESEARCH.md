# Recovery entry investigation

Status: research only. No trading rules changed and no candidate promoted.
The module never sends alerts, submits orders, or writes to paper-trade tables.

## What the existing record establishes

Source: completed radar run 41836693670, report at 2026-11-04 19:03 UTC
(2:03 PM Eastern). This is an earlier snapshot than the handoff's final balances,
not a claim about the currently running job. The table sums rounded printed trade
profits; small differences from exact ledger totals are expected.

| Strategy | Closed trades | Wins | Net P&L | Average win | Average loss | Profit factor |
|---|---:|---:|---:|---:|---:|---:|
| Fast radar | 57 | 13 | -$40.37 | $5.19 | $2.45 | 0.63 |
| Graduation | 57 | 9 | -$60.40 | $13.01 | $3.70 | 0.66 |
| Scalper | 48 | 13 | -$18.12 | $2.06 | $1.28 | 0.60 |

These aggregate multiple rule versions. They do not isolate the performance of the
current rules or prove which losses faster data would have prevented. Existing
`backtest` and `tune` measure future maximum prices, not executable strategy P&L;
they must not be used as promotion evidence. This change preserves those commands
for compatibility and adds an independent chronological simulator.

The cache was subsequently exported by research audit run 41931829790. Its exact
ledger confirms fast -$40.347257, graduation -$60.436477 and scalp -$18.114629,
with no open positions at that saved checkpoint. It contains 116,889 snapshots.

An exploratory join to the last snapshot at or before entry (maximum age 120s)
found all 48 scalp entries matched: entries below 2x recorded run-up were 9 wins
from 13, +$15.78; entries at 2x or above were 4 wins from 35, -$33.90. This small,
retrospective, mixed-rule cohort is a hypothesis for a separate prospective test,
not grounds to promote a filter or to change the frozen recovery recipe.
Fast entries below 2x still lost $33.43 across 49 trades, so the apparent scalp
relationship does not generalize automatically to the other strategies.

Fast positions closed in under two minutes lost $51.86 across 24 trades;
graduation positions closed in under two minutes lost $93.93 across 22 trades.
Holding duration is a post-entry outcome: it cannot be used to select entries,
and excluding these trades would be hindsight selection, not a strategy.

## Fixed hypothesis v1

After initial buying interest, a pullback followed by recovery may distinguish a
resumable move from a coin that is still falling. These are deliberately explicit
research parameters, not optimized thresholds or claims of profitability:

- Solana open-market pool, coin age at least 30 minutes, market cap at least $100k,
  liquidity at least $50k, and five-minute volume at least $5k.
- Initial five-minute price change +3% to +25%; at least 30 unique buyers and
  1.2 to 3 buyers per seller; stage EARLY or RUNNING; passing safety check.
- Watch for an 8–20% pullback. Require at least a 4% rebound from the observed low,
  two upward observations and at least 30 seconds since that low. Cancel a watch
  after 15 minutes, excessive decline, missing data, or failed entry filters.
- Recheck eligibility on the later simulated entry quote. At most one entry per
  coin per evaluation window, $10 each, $100 starting capital, five open positions.
- Fixed net take-profit +25%, stop -15%, maximum hold 30 minutes. These are trigger
  levels, not guaranteed fill levels. Entry and exit require a later quote at least
  15 seconds after the signal. Both sides pay 1.25% fees and 2% slippage.
- A matched momentum comparator enters at initial qualification with identical
  universe, costs, sizing and exits. This is not the existing fast-radar strategy.

## Evidence and limitations

Snapshots now append `research_v1` evidence without changing the scoring features
in memory. It captures receipt timestamps, safety status/time, unique buyers and
sellers, age, pool type and recent price change. Missing old fields are never
reconstructed from today's token metadata. No historical candidate results are
claimed from the old incomplete snapshots.

The simulator processes observations chronologically, limits shared capital, and
does not choose future peaks as exit prices. A take-profit signal followed by a
crash can produce a losing fill. Gaps longer than 90 seconds or stale received
quotes make held positions unresolved and keep their capital reserved. Reports
show realized results, unresolved positions, and the scenario where all unresolved
positions have zero value. This is a stress scenario, not an estimated valuation.

The sampled universe contains scored coins only. Unscored/rejected launches are
not fully recorded, safety failures can end observation coverage, and graduation
has a separate discovery path. Provider event timestamps, executable swap quotes,
failed transactions, priority fees, MEV and true market impact are not available.
Receipt freshness cannot establish provider freshness. Passing this simulation
does not prove live profitability; unresolved trades cannot simply be excluded.

## Running and evaluating

```
python -m memeradar.research --db memeradar.db --audit
python -m memeradar.research --db memeradar.db \
  --since 2026-11-05T00:00:00Z --until 2026-11-06T00:00:00Z \
  --output replay.json
```

Dates above are syntax examples. Select the evaluation window before reviewing
its outcomes, and start after the new evidence recorder is deployed. Run a fixed
recipe forward, then compare it on disjoint later windows. Do not retune on the
same evaluation sample. Review net P&L, worst losses, unresolved exposure, number
of distinct coins/days, and dependence on a few outsized winners. Stress costs and
latency as predeclared scenarios, not opportunities to select the best result.

The radar exports a consistent SQLite backup, exact ledger audit, provenance and
a rolling 24-hour research replay after each run. Rolling reports overlap and
must not be pooled as independent trials. The research audit workflow also reads
the latest saved radar cache on research changes or manual dispatch. It does not
alter the cache or interrupt the current runner. Archives are retained 14 days;
the existing snapshot pruning policy also retains 14 days. Preserve selected
evaluation archives before expiry for longer studies.

Promotion is manual and requires prospective evidence plus execution validation.
No automatic promotion or real-money path is added. Free operation is retained.

## Forward paper candidates (enabled)

`paper_candidates_enabled = true` now starts two independent $100 fake-money
books, $10 per trade, up to five positions each. Existing balances are untouched.
Alerts identify `PAPER (recovery)` and `PAPER (scalp under 2x)`; both appear in
`paper` reports and ledger audits. No wallet or transaction path exists.

Recovery uses the fixed v1 entry thresholds above, including a later entry
observation after its bounce signal. It polls held positions through the existing
three-second paper exit loop; unlike the offline conservative simulator, it sells
at the observed exit quote without an additional 15-second simulated exit delay.
It therefore has its own forward ledger and must not be pooled with replay results.
Open positions and used mints survive restarts; unfilled watches reset on restart.

Under-2x uses the existing scalper settings and costs, with the additional run-up
filter at watch creation and actual entry. Its closed-trade cooldowns and open
positions reload across restarts. This isolates the proposed filter; inherited
scalper execution limitations remain. Neither candidate is declared profitable.

## Distinct forward paper hypotheses (November 2026)

Branch `paper-forward-models-20261105` adds three independent, explicitly
unvalidated experiments without modifying the live rules of the prior books:
pulse breakout (two increasing quotes), buyer-flow accumulation (measured
increase in five-minute unique buyer count), and controlled dip reclaim (a
less restrictive alternative to recovery-v1 with a later entry observation).

The fixed initial recipes are implemented in `memeradar/forward_models.py`.
Each starts with $250 fake cash, uses $10 entries, up to 8 positions, and
1.25% side fees plus 1% modeled side slippage. Each records actual simulated
entries and exits in its own persistent SQLite table and appears in both
`paper` output and `research --audit`. Paper books share the same scored
token universe and may select the same assets, so results are **correlated**.

Before activation, a diagnostic count over the prior 24-hour saved scored
snapshots suggested around 12 pulse, 22 flow and 25 reclaim qualifying
sequences. These are retrospective indications of possible trading frequency,
NOT executed trades, NOT a calibrated estimate of profitability, and NOT
grounds to retune the rules to that window. The forward paper run begins only
after deployment. Compare strategy P&L **per $10 entry**, win/loss payout,
profit factor, drawdown, number of opportunities per day, and sensitivity to
execution cost. Compare to the original under-2x scalp on identical dates.
Unresolved and open positions must remain visible in reports. Do not declare
a winner based on a single day or overlapping snapshot replay.


## Attention Lead v0.1 (research-only, locked initial hypotheses)

This new lane is separate from all existing scalper experiments.
Research registry: `memeradar/attention/{attention,chain,model,backtest,trader}.py`
and setup details in `ATTENTION_RESEARCH.md`.

- **Champion:** None. All old trade models remain independent comparators.
- **Information:** Real first-received timestamps for validated Solana mint
  mentions; 30s DexScreener pair/volume/transaction snapshots; RugCheck history.
- **Hypothesis:** A source-specific attention burst leads a larger *future
  volume-weighted buying proxy*, before the market fully reprices the coin.
  This is NOT verified wallet-level buy USD or proof of causality.
- **Frozen proposed rules:** project future 60m proxy; require prediction >=3x
  current pool liquidity, next-quote simulated buy, 75% forecast-arrival exit,
  -30% net stop, 60m time exit; independent $250/$10 fake balance, 1.25%
  per-side fee and 1% per-side slippage.
- **Stage:** Data collection only, no trades, no trained model. Initial
  dataset must accumulate 14–28 days, then a temporal/token-disjoint model
  holdout, then an independently timestamped replay, then optional 1–2 weeks
  of forward paper. No wallet swaps.
- **Promotion gate:** Outperform no-attention baselines after coverage and
  costs, reliable source access, stable calibration, drawdown and repeat-entry
  analysis; no holdout rescue tuning. If data access is absent, status remains
  disabled; zero observed mentions != zero actual attention.
- **Open hypotheses:** source lag effects, per-channel expertise, spam
  coordination / paid campaigns, real buy/sell USD from Helius on-chain
  evidence and quote/slippage realism.
- **Known limits:** X costs money per retrieved post; Reddit OAuth/approval
  is not automatically available. Post text and usernames are not persisted.
  Telegram requires the operator's own authorized session in GitHub Secrets.
