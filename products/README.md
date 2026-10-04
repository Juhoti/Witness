# products/

Every product the agent builds lives here as its own directory. A product is admitted to main
only through a build certificate in `ledger/entries/` whose verdict is `adoptable`, and deployed
only through a deploy certificate naming the exact transactions sent.

Required in each product:
- `PRODUCT.md` — what it is, the demand number and coverage number it was built from (with the
  scorecard hash), what it will not do, and which rail it is most likely to brush against.
- code + `tests/`
- `simulate.py` if it touches the chain: `simulate(rpc_url) -> dict` run against a fork.
- `contracts/` (Foundry) if it deploys anything; `deploy.py` that only *prepares* calldata.

Kinds of product the gap table produces, roughly in the order they tend to appear:
1. listing            — a new collateral market admitted to the vault (policy + market params)
2. session_policy     — rules that change caps around market close / halts / ex-dates
3. second_vault       — a vault with a different objective
4. basis_supply       — spot + short-perp where funding is persistently positive
5. depth_seed         — market-making on pairs where slippage is consistently paid
6. data_feed          — the multiplier/halt/blocklist feed, sold via x402
7. halt_cover         — a small fund paying out on halts
8. frontend           — a borrow/lend page for one product (geofenced)
