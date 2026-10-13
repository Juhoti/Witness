# Depth plan

**Kind:** depth_seed (costing; nothing deployed)
**Built from:** depth scanner, collateral feed and Uniswap v3 swap logs, 2026-10-13
**Demand:** the listing rail caps a market at a fifth of what can be sold within 5%. SPCX, GOOGL and
AAPL can absorb about $100k each and already have $518k, $261k and $198k borrowed against them at
29-57%; their caps for a Witness vault come out at $20k. Depth, not demand, is the constraint.
**Coverage today:** whatever liquidity providers happen to hold in each USDG pool; nobody places it
with liquidations in mind.
**What it is:** per stock token with a feed, the USDG needed in a band from the price to 10% below
it to reach $1M of depth (a $200k cap), the borrowing already exposed, and an upper bound on what that
liquidity could earn in pool fees (one-sided, so at most half the volume; real earnings depend on
where the price trades).
**Will not do:** place liquidity. That needs capital and a signature; it waits for the build budget's
working-capital line, which opens after Gate 4.
**Nearest rail:** no on-chain action without an adopted certificate.
