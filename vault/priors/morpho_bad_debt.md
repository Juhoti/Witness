---
kind: priors
name: morpho_bad_debt
updated: 2026-10-04T11:02:27Z
tags: [prior]
source: https://blue-api.morpho.org/graphql
retrieved: 2026-10-04T11:02:27Z
markets: 6380
---
# morpho_bad_debt

**PRIOR, not a 4663 outcome.** Borrowed from other venues so the reasoning has a base rate; never cite it as something that happened on Robinhood Chain.

Source: Morpho API `https://blue-api.morpho.org/graphql` (`markets.realizedBadDebt`), retrieved 2026-10-04T11:02:27Z. 6380 markets.

| chain | markets | supply now | markets with realized bad debt | realized bad debt | share of supply |
|---|---|---|---|---|---|
| Ethereum | 1805 | $29,402,539,164 | 119 | $4,514,799 | 1.54 bps |
| Base | 4317 | $4,175,809,000 | 29 | $114,022 | 0.27 bps |
| Arbitrum One | 258 | $13,188,920,289 | 7 | $31 | 0.00 bps |

Reading: realized bad debt on Morpho Blue is concentrated in long-tail collateral at 0.77-0.92 LLTV that was later abandoned (supply now near zero), not in blue-chip markets. Unrealized `badDebt` is not used: its USD conversion is unreliable on dead markets.

Use for: a base rate for "what fraction of markets ever realize bad debt" and "at what LLTV". Not for: anything about stock-token collateral, which has no history here.

Machine-readable: `vault/priors/morpho_bad_debt.json`

## revisions
- 2026-10-04T11:02:27Z sha:415578549b17

