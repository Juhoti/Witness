"""Reviews: a person records that they checked a finding, and how.

A finding above the review threshold stays "unreviewed" until the contract's verified name confirms
it or a person does. A review is published like everything else and names the market, the finding it
applies to, who looked and what they looked at. It applies to that finding only: if the audit later
reads the market differently, the new finding needs its own review.

    python -m agent.review --market <market id> --finding <finding> --by <who> --note "what was checked"
"""
from __future__ import annotations
import argparse
import json
import time
from . import ledger, settings

DIR = settings.LEDGER_DIR / "reviews"


def publish(market: str, finding: str, by: str, note: str) -> str:
    DIR.mkdir(parents=True, exist_ok=True)
    doc = {"kind": "review", "ts": int(time.time()), "market": market, "finding": finding, "reviewed_by": by, "note": note}
    h = ledger.sha(doc)
    (DIR / f"{time.strftime('%Y-%m-%d', time.gmtime(doc['ts']))}_{h[:16]}.json").write_bytes(ledger.canonical(doc))
    return h


def exists(market: str, finding: str) -> bool:
    for p in DIR.glob("*.json") if DIR.exists() else []:
        d = json.loads(p.read_bytes())
        if d.get("market") == market and d.get("finding") == finding:
            return True
    return False


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    for a in ("market", "finding", "by", "note"):
        ap.add_argument(f"--{a}", required=True)
    a = ap.parse_args()
    print("review", publish(a.market, a.finding, a.by, a.note)[:16])
