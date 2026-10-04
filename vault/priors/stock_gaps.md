---
kind: priors
name: stock_gaps
updated: 2026-10-04T11:04:47Z
tags: [prior]
source: yahoo finance chart api
retrieved: 2026-10-04T11:02:41Z
years: 5
tickers: 202
---
# stock_gaps

**PRIOR, not a 4663 outcome.** Borrowed from other venues so the reasoning has a base rate; never cite it as something that happened on Robinhood Chain.

Source: Yahoo Finance chart API (`query1.finance.yahoo.com/v8/finance/chart/<symbol>`, daily bars, 5y), retrieved 2026-10-04T11:02:41Z. Tickers are the stock-token symbols from the latest live scorecard; 202 resolved, 1 did not (SATS).

Gap = next session's open / previous close - 1. "Weekend" = any gap spanning >= 2.5 calendar days (weekends and holidays), when the stock-token's Chainlink feed is frozen and the vault cannot liquidate.

| pooled | n | median | p95 | p99 | p99.9 | > 5% | > 10% |
|---|---|---|---|---|---|---|---|
| overnight | 181318 | 0.75% | 4.22% | 8.96% | 23.49% | 3.48% | 0.80% |
| weekend | 47434 | 0.80% | 4.43% | 8.61% | 21.86% | 3.87% | 0.69% |

Widest weekend tails (p99 of |gap|):

| ticker | weekend p99 | weekend max | worst down | n |
|---|---|---|---|---|
| XNDU | 63.37% | 63.37% | -63.37% | 27 |
| AMC | 25.64% | 37.13% | -37.13% | 260 |
| WULF | 21.90% | 45.45% | -45.45% | 260 |
| DJT | 21.05% | 49.56% | -21.05% | 260 |
| NNE | 19.16% | 20.27% | -20.27% | 125 |
| NBIS | 18.78% | 19.64% | -19.64% | 101 |
| USAR | 16.97% | 23.38% | -9.42% | 167 |
| QBTS | 16.72% | 24.53% | -19.24% | 260 |
| APLD | 16.56% | 21.53% | -21.53% | 233 |
| RGTI | 16.19% | 51.15% | -17.92% | 260 |
| TEM | 15.94% | 21.88% | -15.94% | 120 |
| QUBT | 15.81% | 22.46% | -15.81% | 260 |
| SMR | 14.78% | 18.84% | -17.23% | 239 |
| MSTR | 14.77% | 27.37% | -27.37% | 260 |
| SOUN | 14.50% | 87.69% | -14.50% | 231 |

Use for: sizing session rules (how far a collateral price can move while the market is closed, per ticker). Earnings dates drive the tail; a per-ticker rule should read the issuer calendar (Gate 5, item 4).

Machine-readable: `vault/priors/stock_gaps.json`

## revisions
- 2026-10-04T11:04:47Z sha:b71ab7b4350a

