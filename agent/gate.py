"""Where Gate 0 stands: how many consecutive live scans have completed with every scanner healthy.

    python -m agent.gate

A scan counts as clean when no scorecard section carries an error. The run is counted back from the
latest live scorecard and never past the loop's last start (a restart begins the run again); a scan more than three intervals after the previous one also breaks the run,
since that is what a stalled or restarted loop looks like in the record.
"""
from __future__ import annotations
import json
import time
from . import settings

SECTIONS = ("stock_tokens", "morpho", "uniswap", "reverts", "feeds", "depth", "pause_history")
TARGET = 200


def errors(card: dict) -> list[str]:
    return [k for k in SECTIONS if isinstance(card.get(k), dict) and card[k].get("error")]


def status() -> dict:
    live = []
    for p in settings.SCORECARD_DIR.glob("*.json"):
        c = json.loads(p.read_bytes())
        if not c.get("backfilled"):
            live.append(c)
    live.sort(key=lambda c: c["ts"])
    run, max_gap = 0, 3 * settings.SCAN_EVERY_MINUTES * 60
    marker = settings.STATE_DIR / "loop_started"
    started = int(marker.read_text()) if marker.exists() else 0
    for i in range(len(live) - 1, -1, -1):
        if errors(live[i]) or live[i]["ts"] < started:
            break
        run += 1
        if i and live[i]["ts"] - live[i - 1]["ts"] > max_gap:
            break
    last = live[-1] if live else None
    return {"live_scans": len(live), "clean_run": run, "target": TARGET, "remaining": max(TARGET - run, 0),
            "last_scan": last and time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(last["ts"])),
            "last_scan_errors": errors(last) if last else None,
            "eta_hours": round(max(TARGET - run, 0) * settings.SCAN_EVERY_MINUTES / 60, 1)}


if __name__ == "__main__":
    s = status()
    print(f"clean consecutive scans: {s['clean_run']} / {s['target']}  (about {s['eta_hours']} h to go)")
    print(f"live scans in the record: {s['live_scans']}; last at {s['last_scan']}; errors in it: {s['last_scan_errors'] or 'none'}")
