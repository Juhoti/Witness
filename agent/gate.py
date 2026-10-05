"""Where Gate 0 stands: how many consecutive scans the loop has completed without being restarted.

    python -m agent.gate

The gate is about the loop running unattended, so the count is every scan completed since the loop
last started: a restart or a crash begins the run again, and so does a gap of more than three
intervals, which is what a stalled loop looks like in the record. A scan in which one scanner failed
(a rate limit, an API outage) still counts, because the loop carried on and recorded the failure.
Quality is reported beside the count, not folded into it: the share of scans with every section
clean, and whether failures are isolated. The gate is not ready while more than 5% of the run's scans
carry an error or any two in a row do.
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
    marker = settings.STATE_DIR / "loop_started"
    started = int(marker.read_text()) if marker.exists() else 0
    max_gap = 3 * settings.SCAN_EVERY_MINUTES * 60
    run: list[dict] = []
    for i in range(len(live) - 1, -1, -1):
        if live[i]["ts"] < started:
            break
        run.append(live[i])
        if i and live[i]["ts"] - live[i - 1]["ts"] > max_gap:
            break
    run.reverse()
    flawed = [bool(errors(c)) for c in run]
    by_section: dict[str, int] = {}
    for c in run:
        for k in errors(c):
            by_section[k] = by_section.get(k, 0) + 1
    back_to_back = any(a and b for a, b in zip(flawed, flawed[1:]))
    rate = sum(flawed) / len(run) if run else 0.0
    last = live[-1] if live else None
    return {"live_scans": len(live), "run": len(run), "target": TARGET, "remaining": max(TARGET - len(run), 0),
            "scans_with_an_error": sum(flawed), "error_rate": round(rate, 4), "errors_by_section": by_section,
            "two_in_a_row": back_to_back, "quality_ok": rate <= 0.05 and not back_to_back,
            "ready": len(run) >= TARGET and rate <= 0.05 and not back_to_back,
            "last_scan": last and time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(last["ts"])),
            "eta_hours": round(max(TARGET - len(run), 0) * settings.SCAN_EVERY_MINUTES / 60, 1)}


if __name__ == "__main__":
    s = status()
    print(f"consecutive scans since the loop last started: {s['run']} / {s['target']}  (about {s['eta_hours']} h to go)")
    print(f"scans with a scanner error: {s['scans_with_an_error']} ({s['error_rate']:.1%}) {s['errors_by_section'] or ''}"
          f"; two in a row: {'yes' if s['two_in_a_row'] else 'no'}; quality {'ok' if s['quality_ok'] else 'NOT ok'}")
    print(f"gate 0 scan criterion: {'MET' if s['ready'] else 'not yet'}; last scan {s['last_scan']}")
