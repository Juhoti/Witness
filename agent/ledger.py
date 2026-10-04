"""Content-addressed, append-only ledger.

A scorecard is a JSON file named by its sha256. A certificate names the scorecard(s), the policy
before and after, the witness, the verdict, and the proposer. Replaying a certificate means
re-running the judge on the same hashed inputs and getting the same verdict bit for bit.

Gate 1 pushes `ledger/` to a public git remote after every entry. Nothing secret goes here.
"""
from __future__ import annotations
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path
from . import settings


def canonical(obj) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha(obj) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


def write_scorecard(card: dict) -> str:
    settings.SCORECARD_DIR.mkdir(parents=True, exist_ok=True)
    h = sha(card)
    day = time.strftime("%Y-%m-%d", time.gmtime())
    path = settings.SCORECARD_DIR / f"{day}_{h[:16]}.json"
    if not path.exists():
        path.write_bytes(canonical(card))
    return h


def read_scorecard(h_prefix: str) -> dict | None:
    for p in sorted(settings.SCORECARD_DIR.glob("*.json")):
        if h_prefix in p.name:
            return json.loads(p.read_bytes())
    return None


def append_entry(entry: dict) -> str:
    settings.ENTRY_DIR.mkdir(parents=True, exist_ok=True)
    entries = sorted(settings.ENTRY_DIR.glob("*.json"))
    seq = len(entries) + 1
    parent = json.loads(entries[-1].read_bytes())["hash"] if entries else None
    body = {**entry, "seq": seq, "parent": parent, "ts": int(time.time())}
    h = sha(body)
    (settings.ENTRY_DIR / f"{seq:06d}_{h[:16]}.json").write_bytes(canonical({**body, "hash": h}))
    _push()
    return h


def entries() -> list[dict]:
    return [json.loads(p.read_bytes()) for p in sorted(settings.ENTRY_DIR.glob("*.json"))]


def _push() -> None:
    if not settings.LEDGER_GIT_REMOTE:
        return
    try:
        subprocess.run(["git", "-C", str(settings.ROOT), "add", "ledger"], check=True, capture_output=True)
        subprocess.run(["git", "-C", str(settings.ROOT), "commit", "-qm", "ledger: entry"], capture_output=True)
        subprocess.run(["git", "-C", str(settings.ROOT), "push", "-q", settings.LEDGER_GIT_REMOTE, "HEAD"], capture_output=True)
    except subprocess.CalledProcessError:
        pass  # a failed push is reported by the next scan's health check, not hidden


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "show":
        for e in entries():
            print(f'{e["seq"]:6d} {e["hash"][:16]} {e.get("kind","?"):18s} {e.get("verdict","-"):8s} {e.get("why","")}')
