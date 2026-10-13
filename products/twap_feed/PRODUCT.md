# Witness TWAP feed

**Kind:** data_feed (on-chain contract; written and fork-tested, NOT deployed, NOT audited)
**Built from:** feed coverage and price sets, 2026-10-13
**Demand:** 168 of 203 stock tokens have no Chainlink feed, so no careful lender can list them; GLD,
HIMS, RDDT, AMC and others have real depth but no price a lending market can read.
**Coverage today:** Chainlink covers 35 stock tokens. Markets on the others read feeds outside
Chainlink's directory or nothing at all (see the oracle audit).
**What it is:** `contracts/WitnessTwapFeed.sol`, one instance per token: a Chainlink
AggregatorV3-shaped price (8 decimals, `latestRoundData`) computed from a Uniswap v3 pool's own
30-minute time-weighted average tick. No owner, no keeper, nothing written after construction. A pool
whose oracle cannot cover the window makes it revert, so a consumer gets no price rather than a wrong
one. It plugs into Morpho's standard oracle as a base feed.
**Fork check (simulate.py):** run against the 12 deepest v3 USDG pools for stock tokens on
2026-10-13: largest difference from the same TWAP computed off chain 7e-10; agrees with Chainlink
within 0.9% where both exist (GME 0.85%, the rest under 0.4%); a pool without enough history reverts
with "OLD".
**Before anyone relies on it:** an independent audit (formal verification of `price()` preferred);
raising each pool's observation cardinality so it can cover the window; a cost-to-manipulate figure
per pool published beside each feed; and a judged product certificate, as the rail on published
prices requires.
**Will not do:** be called an oracle by Witness until those steps are done.
**Nearest rail:** a published price becomes something others rely on only as a judged product.
