# Witness

An autonomous agent that curates a USDG lending vault on Robinhood Chain (chain id 4663) and keeps
a public, replayable record of everything it measures and every change it makes to its own policy.
The record is the product. It lives in [`ledger/`](ledger/); start there.

The agent holds no keys. It reads the chain, writes scorecards, and will only ever act on-chain from
a certificate a human adopted. Anyone can fork the code; nobody can fork the record.

## The record

- `ledger/scorecards/` — one JSON per scan, named by date and the sha256 of its canonical form.
  Every stock token on the chain with its multiplier and pause state, every Morpho market with
  supply, borrow and utilisation, Uniswap v3 and v4 swaps with price impact and slippage paid per
  pool, reverted transactions clustered by target and reason, and the gap table derived from them.
  Scorecards marked `backfilled: true` were reconstructed from chain history with the same code
  and the same sample window as a live scan.
- `ledger/entries/` — an append-only chain of certificates. Each names the scorecard it used by
  hash, the rung it judged, the verdict, and the content hash of the agent's working notes at the
  time. Entries are never edited or deleted; failed scans stay in the record.
- `vault/` — the agent's working memory as an Obsidian vault: a note per token, per market, a daily
  log, and `vault/priors/`, borrowed base rates from other venues, each labelled as a prior with
  its source and retrieval time. Memory, not record: the ledger cites it by hash.

## What it measures

| signal | source | file |
|---|---|---|
| stock tokens, multiplier, pause state | beacon-proxy enumeration + `uiMultiplier()` via Multicall3 | `agent/scan/stock_tokens.py` |
| Morpho markets, caps, utilisation, collateral | Morpho Blue API, cross-checked on chain | `agent/scan/morpho.py` |
| slippage paid per pool, token prices | Uniswap v3 and v4 `Swap` events | `agent/scan/uniswap.py` |
| reverted intent | Blockscout failed transactions | `agent/scan/reverts.py` |
| perp funding and open interest | not yet wired | `agent/scan/perps.py` |

`agent/gap.py` turns a scorecard into the gap table: demand minus coverage per candidate, with the
feasibility and risk flags from `config/northstar.md` applied as data checks. In the current gate it
produces candidates and zero proposals.

## How it decides

`config/northstar.md` is the objective and the rails; the agent may propose toward it and may not
rewrite it. `config/rungs.yaml` is a human-written ladder of goals, each with a witness the judge can
run against a scorecard. `agent/judge.py` is fixed code: it evaluates witnesses, writes certificates,
and can replay any certificate from its hashed inputs to the same verdict. Changing the judge is
itself a judged, visible event.

Work is gated, not scheduled. The scan loop runs unattended (Gate 0); every policy or code change
goes through the judge and leaves a certificate (Gate 1); a model proposes and a human adopts (Gate
2); a small real vault (Gate 3); an attested signing runtime (Gate 4); products built from measured
demand, repeatedly (Gate 5). A gate opens only when the previous one's checks hold.

## Running it

Requirements: Python 3.11+, a read-only JSON-RPC endpoint for chain 4663, and nothing that can sign.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
python -m pytest -q
python -m agent.main --once          # one scan -> ledger/scorecards/<date>_<hash>.json
python -m agent.main                 # loop every SCAN_EVERY_MINUTES
python -m agent.backfill             # one scorecard per UTC day since the chain's first block
python -m agent.priors               # refresh vault/priors/
python -m agent.ledger show          # list certificates
python -m agent.census ingest        # read every log on the chain into per-contract counts
python -m agent.census report        # share of activity the agent can name, and what it cannot
python -m agent.audit                # check the record against itself: hashes, chain, references
python -m agent.gate                 # consecutive clean scans toward the current gate
```

Configuration is read from environment variables (a `.env` file in the repo root is loaded if
present; it is gitignored):

| variable | purpose |
|---|---|
| `RPC_URL` | primary endpoint for `eth_call`; a keyed provider is strongly recommended |
| `RPC_URL_LOGS` | endpoint for bulk `eth_getLogs`; defaults to the public RPC, which allows wide ranges |
| `RPC_URL_FALLBACK` | used when the others are unset |
| `BLOCKSCOUT_API_KEY` | Blockscout account key, sent as `x-api-key`; unkeyed requests are refused |
| `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` | optional push alerts on scan failure, token halt or multiplier step |
| `LEDGER_GIT_REMOTE` | optional git remote; every ledger entry is committed and pushed when set |
| `SCAN_EVERY_MINUTES`, `LOG_LEVEL` | loop cadence and verbosity |

Secrets never reach the tree: keyed URLs and tokens are redacted from every log line, alert and
ledger write. `config/chain.toml` holds every contract address the scanners use, each with the
evidence it was checked against.

Run `python -m agent.main` under any supervisor that restarts it; how and where is the operator's business.

## Layout

```
agent/main.py            the loop: scan, score, judge rungs, append entries, alert
agent/scan/              the scanners
agent/gap.py             demand - coverage per candidate
agent/judge.py           fixed judge: schema, witness, replay, held-out budgets
agent/ledger.py          content-addressed, append-only scorecards and certificates
agent/memory.py          the Obsidian vault writer and its content hash
agent/backfill.py        replay the scanners over chain history
agent/priors.py          borrowed base rates for vault/priors/
agent/propose.py         model proposer (Gate 2; off by default)
agent/build.py           product builder (Gate 2b; confined to products/)
config/                  chain addresses, north star, rungs, policy
ledger/                  the record
vault/                   working memory
products/                what the builder ships, one directory per product
tests/                   pytest
```

## Status

Gate 0. The scanners produce a clean scorecard every 30 minutes: ~200 stock tokens, ~300 Morpho
markets, ~1,300 Uniswap pairs, revert clusters. The gate closes after 200 consecutive unattended
scans. Backfill from the chain's first block and the borrowed priors are in place.

Apache-2.0.
