# De-peg and risky-market watch

**Kind:** data_feed (alerts)
**Built from:** oracle audits and session risk, 2026-10-13
**Demand:** $325M borrowed against USDe at a fixed 1.00 whose live backup takes over only after a
0.5% gap lasts 16 hours and someone calls it; $123M against feeds outside Chainlink's list; 9 markets
lending above the loan-to-value their collateral's history survives (6 a week earlier).
**Coverage today:** none published on this chain. The switch on the USDe oracle is permissionless;
nobody announces when it becomes callable.
**What it is:** every 10 minutes, the live gap between the fixed price and the backup feed, the
challenge state, divergence on other fragile oracles, and new over-leveraged markets. Alerts on a
change of level through the Witness bot (/watch), current levels in feed/watch.json.
**Will not do:** call challenge() or any other transaction (no keys, no certificate for it); tell
anyone to withdraw.
**Nearest rail:** no on-chain action without an adopted certificate; measurements, not advice.
