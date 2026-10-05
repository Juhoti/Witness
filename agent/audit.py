"""Check the record against itself. Anyone with a clone can run this; it needs no network.

    python -m agent.audit

Three checks:
  scorecards  every file's name carries the sha256 of its canonical content
  entries     sequence numbers are contiguous, each entry's hash is the sha256 of its body, and each
              entry names the hash of the one before it
  references  every scorecard an entry cites is present in ledger/scorecards/
Exit status is 0 only if all three hold.
"""
from __future__ import annotations
import json
import sys
from . import ledger, settings


def scorecards() -> tuple[int, list[str]]:
    bad, n = [], 0
    for p in sorted(settings.SCORECARD_DIR.glob("*.json")):
        n += 1
        try:
            h = ledger.sha(json.loads(p.read_bytes()))
        except ValueError:
            bad.append(f"{p.name}: not valid JSON")
            continue
        if p.stem.split("_", 1)[1] != h[:16]:
            bad.append(f"{p.name}: content hashes to {h[:16]}")
    return n, bad


def entries() -> tuple[int, list[str], set[str]]:
    bad, cited, parent, n = [], set(), None, 0
    for i, p in enumerate(sorted(settings.ENTRY_DIR.glob("*.json")), start=1):
        n += 1
        e = json.loads(p.read_bytes())
        body = {k: v for k, v in e.items() if k != "hash"}
        h = ledger.sha(body)
        if e.get("seq") != i:
            bad.append(f"{p.name}: seq {e.get('seq')} where {i} was expected")
        if e.get("hash") != h or not p.stem.endswith(h[:16]):
            bad.append(f"{p.name}: body hashes to {h[:16]}")
        if e.get("parent") != parent:
            bad.append(f"{p.name}: parent {str(e.get('parent'))[:16]} does not match the previous entry {str(parent)[:16]}")
        parent = e.get("hash")
        for k in ("scorecard", "scorecard_hash"):
            if isinstance(e.get(k), str):
                cited.add(e[k])
    return n, bad, cited


def main() -> int:
    n_cards, bad_cards = scorecards()
    n_entries, bad_entries, cited = entries()
    have = {p.stem.split("_", 1)[1] for p in settings.SCORECARD_DIR.glob("*.json")}
    missing = sorted(c for c in cited if c[:16] not in have)
    print(f"scorecards: {n_cards} checked, {len(bad_cards)} do not match their name")
    print(f"entries:    {n_entries} checked, {len(bad_entries)} problems in the chain")
    print(f"references: {len(cited)} scorecards cited, {len(missing)} missing")
    for line in (bad_cards + bad_entries + [f"missing scorecard {m[:16]}" for m in missing])[:20]:
        print("  " + line)
    return 0 if not (bad_cards or bad_entries or missing) else 1


if __name__ == "__main__":
    sys.exit(main())
