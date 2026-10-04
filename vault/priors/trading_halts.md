---
kind: priors
name: trading_halts
updated: 2026-10-04T11:02:41Z
tags: [prior]
source: https://www.nyse.com/api/trade-halts/historical/download
retrieved: 2026-10-04T11:02:40Z
halts: 73991
from: 2019-02-22
to: 2026-10-02
---
# trading_halts

**PRIOR, not a 4663 outcome.** Borrowed from other venues so the reasoning has a base rate; never cite it as something that happened on Robinhood Chain.

Source: NYSE historical trading-halts download `https://www.nyse.com/api/trade-halts/historical/download` (covers all US listing venues: Nasdaq 57267, NYSE 9661, NYSE American 4294, NYSE Arca 1920, Cboe BZX 849), retrieved 2026-10-04T11:02:40Z. 73991 halts, 2019-02-22 to 2026-10-02.

| year | halts | LULD pauses | news pending | other |
|---|---|---|---|---|
| 2019 | 1214 | 838 | 351 | 25 |
| 2020 | 14928 | 14076 | 796 | 56 |
| 2021 | 5633 | 4682 | 891 | 60 |
| 2022 | 6892 | 5751 | 1059 | 82 |
| 2023 | 9384 | 7772 | 1456 | 156 |
| 2024 | 11218 | 8882 | 1881 | 455 |
| 2025 | 13095 | 10749 | 2021 | 325 |
| 2026 | 11627 | 9444 | 1844 | 339 |

For the 203 stock-token tickers: 11 had at least one halt in the trailing 12 months (5%); 15 halts in total.

| ticker | halts 12m | LULD | news | all-time |
|---|---|---|---|---|
| WDAY | 3 | 3 | 0 | 3 |
| XNDU | 3 | 3 | 0 | 3 |
| CIEN | 1 | 0 | 1 | 2 |
| P | 1 | 1 | 0 | 1 |
| RVI | 1 | 1 | 0 | 1 |
| VICR | 1 | 1 | 0 | 1 |
| COHR | 1 | 1 | 0 | 4 |
| NOK | 1 | 0 | 1 | 7 |
| LUNR | 1 | 1 | 0 | 31 |
| CBRS | 1 | 1 | 0 | 1 |
| TE | 1 | 1 | 0 | 1 |

Reading: LULD pauses are minutes long and clear on their own; news-pending halts can last hours and are the ones that matter for a frozen feed. Use for: the "30 halt-free days" rail and the halt-coverage fund (Gate 5, item 8). A Robinhood-side pause of the token (beacon `paused()`) is a different event and is read live by the scanner.

Machine-readable: `vault/priors/trading_halts.json`

## revisions
- 2026-10-04T11:02:41Z sha:c5e1c05ef3bb

