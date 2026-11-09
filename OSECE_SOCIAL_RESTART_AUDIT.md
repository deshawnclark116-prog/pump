# November 9, 2026: Social loss audit + OSECE forward paper restart

## Social Attention diagnosis — historical, not a new validated trade rule

Source: immutable research artifacts from GitHub runs #77 and #78.
Their six-hour datasets diverged when run #77's cache-save conditional
timed out hashing an oversized SQLite DB. Compare trade identity using
(variant, mint, entry_ts), never the autoincrement trade ID alone.

As-of 7:54 AM Eastern Nov 9, across the deduplicated combined ledger:
- **73** Social Attention entries, **72** closed and **1** open.
- **27 winners**, **45 losers**, combined realized modeled P&L **-$54.60**.
- Winners grossed +$62.34; losers lost -$116.94.
- Among 18 closed overnight: 7 wins, 11 losses, net **-$20.13**
  (exact original sums: 77 run -19.882383; 78 run -0.252226).
- Over the 72 closed trades, 39 of 45 losses exited on the -20% net
  STOP_LOSS condition; the rest were negative time exits.
- Five losses were worse than -$4 each; aggregate -$33.34 across those
  five *selected after seeing outcomes*. This is a severity diagnostic,
  not a risk-model threshold.
- Most damaging overnight coins: Gemini (-$9.96 fake USD on a $10
  position) and MrBeast (-$7.04). Their recent buy/sell transaction
  counts were 1,634/1,555 and 1,368/1,353 respectively, with roughly
  $50k and $45k reported liquidity. Relative buy-count dominance was
  extremely small and does not demonstrate real dollar buyer pressure.
- All seven closed trades that entered more than 5% ABOVE their own
  previously observed signal price lost (-$17.58 total). Treat a
  maximum entry-chase cutoff as a **prospective shadow hypothesis**.
  It is *forbidden* to claim profitability using this same consumed
  window as a validation holdout.
- No simple static liquidity floor clearly separated winners from
  losers. The median liquidity for all historical 27 winners was
  ~$49.0k versus ~$45.0k for 45 losers.

**Important:** Some market quotes skip past a configured stop. A -20%
stop does NOT guarantee a -20% loss in this quote-based simulator.
The social entry logic itself is NOT modified by this PR.

A reproducible audit is generated after each six-hour scan in
\`research-output/social-loss-audit.json\`, with complete trade receipts,
daily P&L, reason tallies, large-loss analysis, repeated mints and
predeclared shadow chase-guard comparison.

## OSECE live fake-money experiments

User explicitly reactivated OSECE paper models while keeping Social
Attention as the only active *old-school attention* bot.

- **Original single-source OSECE**: unchanged v1 requirements. $250
  separate cash, $10 fake stakes, exact cultural phrase received prior
  to token launch, >=$20k liquidity, actual five-minute buy USD >=1200,
  buy/sell >=1.20, organic >=25, verified audit, RugCheck, delayed
  confirmation/entry.
- **Original two-source OSECE Collision**: same frozen requirements,
  requires two separate cultural source families before token creation.
  Independent $250 fake bankroll.
- **NEW OSECE v1b early-liquidity research arm**: $250 third independent
  fake bankroll; one qualifying prelaunch source, >=$5k measured
  liquidity, buy USD >=500 over five minutes and >=1.25x sell dollars,
  >=8 active traders, verified mint/freeze authorities and fresh RugCheck
  passing history. No Organic Score gate because actual very young
  token snapshots mostly reported 0. The 5k liquidity threshold is
  deliberately more aggressive and carries greater risk of
  non-executable quotes and exit slippage; do not confuse indicative
  paper prices with executable swap quotes.

All arms require three independent NEW Jupiter quotes after token launch
and a delay between confirmation and entry. Paper trades have a
20% net take-profit, -12% net stop, 20-minute maximum hold and simulated
two-sided costs (1.25% fee and 1% slippage on EACH side). Price gaps
can exceed the stop. The OSECE research model has no real swap path.

Original OSECE experiments are NOT changed retroactively, no old
retired watches reopened, no backdated entries. New markets and incoming
culture after deployment only. OSECE remains a separately labeled
experimental lane alongside Social Attention, not a proven edge.

## Research integrity and GitHub continuity

The previous workflow used a multi-hundred-MB \`hashFiles('memeradar.db')\`
expression in the cache-save condition. GitHub timed it out after 120s,
so run #77 exported an artifact but DID NOT write the cache consumed by #78.

This PR:
1. Replaces hash-based conditions with a lightweight shell file-presence
   check in \`steps.saved_db.outputs.present\`.
2. Adds a startup reconciliation of the latest two immutable
   \`research-<run ID>\` artifacts and specifically the known run #77
   archive (while it remains within GitHub's retention period).
3. Never uses raw SQLite autoincrement IDs across divergent run databases;
   matches already-observed social or OSECE trades on variant + mint +
   exact entry timestamp and remaps signal IDs, avoiding silent loss.
4. Does NOT create inferred trades, fills or price exits. Closed archived
   outcomes can repair previously open records; open archived positions
   remain open unless later quotes justify a real simulated exit.
5. Marks recovered alert notifications as already delivered to avoid
   sending historical fake buys again.
6. Runs the independent paper-only accounts through ordinary GitHub
   Actions and retains the original report artifacts for review.

**Promotion tests:** Assess Social shadow filters and OSECE strategy
performance strictly in future independent forward windows. Grade all
coin launches, unfilled signals, and gaps, not only successful entries.
No real capital or claims of calibrated edge.
