# Witness

An agent that curates a lending vault on Robinhood Chain and keeps a public, replayable record of every change it makes to its policy and to itself. The record is in `ledger/`. Start there.

## Agent scaffold

Runs on a Mac mini (Apple Silicon, macOS 14+) 24/7 under launchd. Gate 0 scope: scan the chain,
write scorecards, append ledger entries, alert on failures. Read `PLAN.md` for what comes after.

## One-time setup on the mini

```bash
# from your laptop
scp -r witness mini.local:~/witness
ssh mini.local
cd ~/witness
./setup-mini.sh          # installs brew deps, python venv, launchd service
cp .env.example .env     # then edit: RPC URL, Telegram token, Anthropic key (Gate 2)
```

Edit `config/chain.toml` and replace every `UNVERIFIED` address after checking it on
https://robinhoodchain.blockscout.com. Do not run the scanners against addresses you have not checked.

## Run it

```bash
./scripts/run-once.sh                 # one scan, writes ledger/scorecards/<date>.json
launchctl load ~/Library/LaunchAgents/org.witness.agent.plist   # 24/7 loop (setup does this)
tail -f ~/witness/logs/agent.log
```

## Judge / ledger (Gate 1)

```bash
python -m agent.judge propose config/rungs.yaml      # evaluate human-written rungs
python -m agent.judge replay <certificate-hash>      # re-derive a verdict
python -m agent.ledger show                          # list entries
```

## Layout

```
PLAN.md                 the plan and the hard rails
config/chain.toml       chain + contract addresses (verify every UNVERIFIED line)
config/northstar.md     objective + rails the agent may not rewrite
config/rungs.yaml       the human-written ladder (first goals)
config/policy.json      current vault policy; changed only via judged entries
agent/main.py           scheduler loop
agent/scan/*.py         scanners (stock tokens, morpho, uniswap, reverts, perps)
agent/gap.py            demand - coverage scorecard
agent/propose.py        frontier-model proposer (Gate 2; off by default)
agent/judge.py          fixed judge: schema, witness, replay, budgets
agent/ledger.py         content-addressed certificates, append-only, git-pushed
agent/notify.py         Telegram alerts
ledger/                 scorecards + entries (commit this directory publicly)
vault/                  Obsidian vault: the agent's working notes (open this folder in Obsidian)
launchd/                macOS service definition
scripts/                run-once, verify (Gate 4 stub)
```

## Keep in mind

- Phase 0 holds no keys with signing power. `.env` only needs a read RPC and a Telegram token.
- The mini is not an attested runtime; it runs the loop. Signing moves to a confidential VM in Gate 4.
- Everything in `ledger/` is meant to be public. Nothing secret goes in there.

## Builder (Gate 2b)

Mark a rung `status: build` in `config/rungs.yaml` and the next tick has the model draft code for it
under `products/<slug>/` on a branch `build/<id>`, runs the witnesses, and records a certificate.
Merging is yours: `git log --oneline build/<id>`, read it, merge if the certificate says adoptable.
Needs `PROPOSER_ENABLED=true`, an Anthropic key, and a git repo initialised on the mini (`git init && git add -A && git commit -m init`).

## Obsidian vault (memory)

`vault/` is a plain Obsidian vault the agent writes to on every scan: a note per stock token, per
Morpho market, per product, and a daily log. Open it in Obsidian on your laptop — either sync the
folder (iCloud/Syncthing) or install the Obsidian Git plugin and point it at the same repo.
The vault is working memory; the ledger is the record. Certificates carry `vault:` with the vault's
content hash at decision time, so you can always see what the agent believed when it decided.
