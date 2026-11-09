# Social Attention-only paper trading — retired strategy policy

Effective from commit activating `SOCIAL_ATTENTION_ONLY=1` on November 8, 2026.

**Only `attention_social_probe_v0` is authorized to place NEW simulated buys**.
This is a $10 paper-trade hypothesis based on real social contract mentions,
DexScreener market activity, RugCheck, delayed fills and net modeled costs.
Its historical results remain preliminary, not a validated edge.

The following strategies are **retired from new entries**:

- Main radar Fast, Steady, Steady v1
- Original scalper and Under-2x scalper
- Graduation, Recovery, Pulse Breakout, Buyer Flow, Dip Reclaim
- GeckoTerminal Trending attention experiment
- Stress-Survivor experiment
- Both OSECE Early Signal and Cross-Source Collision paper books
- Calibrated waiting-money model remains disabled pending real validation

**Do not delete history.** Every ledger and previously recorded score or
trade is preserved for honest retrospective auditing.

**Open legacy positions must not be invented as closed.** Existing open
fake positions are still monitored on fresh market quotes and may generate
sell/exit notifications only. Existing pending/queued buy signals are
retired without filling. In the main radar, original and candidate books
continue their exit manager loops, but their consider, scan and fast/steady
entry paths are disabled. Scalper's pending dip watches are cleared.

Source collection is intentionally retained:
- Existing social feed, attention snapshots, safety checks, and Gecko
  observations continue for Social Attention.
- OSECE cultural streams, PumpPortal launch census, Jupiter and Gecko
  new-pool research continue without OSECE paper entries.
- Stress-Survivor live entry heuristics are retired (its previous positions
  are still monitored for exit quotes).

The GitHub Actions workflow sets these flags explicitly:
```text
SOCIAL_ATTENTION_ONLY=1
ATTENTION_ENABLED=1
ATTENTION_EXPERIMENTAL_PAPER=1
ATTENTION_EXPERIMENTAL_SOCIAL_ONLY=1
ATTENTION_PAPER_ENABLED=0
STRESS_SURVIVOR_PAPER_ENABLED=0
STRESS_SURVIVOR_EXIT_ONLY=1
OSECE_ENABLED=1
OSECE_PAPER_ENABLED=0
OSECE_EXIT_ONLY=1
```

Disable of the others is not deletion or a retrospective rewrite. Compare
future Social Attention results to the prior cohorts by day and real
timestamps. Avoid treating one profitable partial day as proof of edge.
