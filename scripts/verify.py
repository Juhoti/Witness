"""Gate 4 verifier stub. Today it walks the local ledger and checks that every entry chains
to its parent by hash. In Gate 4 it also fetches the CVM attestation and checks the image hash."""
import json, hashlib, sys
from pathlib import Path

root = Path(__file__).resolve().parent.parent
entries = sorted((root / "ledger" / "entries").glob("*.json"))
parent = None
for p in entries:
    e = json.loads(p.read_bytes())
    body = {k: v for k, v in e.items() if k != "hash"}
    h = hashlib.sha256(json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
    if h != e["hash"] or e.get("parent") != parent:
        print(f"BROKEN at {p.name}: hash or parent mismatch"); sys.exit(1)
    parent = h
print(f"ledger ok: {len(entries)} entries, head={parent[:16] if parent else None}")
print("attestation: not available on this host (Gate 4 moves signing to a confidential VM)")
