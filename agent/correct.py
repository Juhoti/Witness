"""Corrections: when something Witness published or said turns out wrong, the correction is published too.

The record is append-only, so a wrong reading is never edited away. A correction is a new file that
names what it corrects, what was said, what is now known and how it was found out. The count of
corrections is part of the track record: a record with none is not more trustworthy, only less tested.

    python -m agent.correct --about <published file or topic> --was "..." --now "..." --how "..." [--cause "..."]
"""
from __future__ import annotations
import argparse
import json
import time
from . import ledger, settings

DIR = settings.LEDGER_DIR / "corrections"


def publish(about: str, was: str, now: str, how: str, cause: str | None = None) -> str:
    DIR.mkdir(parents=True, exist_ok=True)
    doc = {"kind": "correction", "ts": int(time.time()), "about": about, "was": was, "now": now, "found_by": how, "cause": cause}
    h = ledger.sha(doc)
    (DIR / f"{time.strftime('%Y-%m-%d', time.gmtime(doc['ts']))}_{h[:16]}.json").write_bytes(ledger.canonical(doc))
    return h


def count() -> int:
    return len(list(DIR.glob("*.json"))) if DIR.exists() else 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    for a in ("about", "was", "now", "how"):
        ap.add_argument(f"--{a}", required=True)
    ap.add_argument("--cause")
    a = ap.parse_args()
    print("correction", publish(a.about, a.was, a.now, a.how, a.cause)[:16], "| corrections on record:", count())
