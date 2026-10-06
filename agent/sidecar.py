"""The sidecar: runs everything that is not the scan loop, each on its own cadence.

The scan loop stays small and proven. This process keeps the wider picture current beside it: the
chain census, the intent sampler, the oracle audit, Witness's prices, the opportunity map, the
grading of past claims and, when switched on, the investigator. A task that fails is logged and tried
again at its next turn; one task failing never stops the others, and nothing here can stop the scans.

    python -m agent.sidecar            # run forever
    python -m agent.sidecar --once     # run every task that is due, once, then exit
"""
from __future__ import annotations
import json
import logging
import sys
import time
import traceback
from . import census, grade, intent, investigate, opportunity, oracle_audit, prices, projects, scoreboard, sellcheck, session_risk, settings, vaults

log = logging.getLogger("sidecar")
STATE = settings.STATE_DIR / "sidecar.json"


def _prices():
    card = opportunity._latest(settings.SCORECARD_DIR, live_only=True) or {}
    return prices.publish(prices.measure(card))[:16]


def _opportunity():
    return opportunity.publish(opportunity.build())[:16]


# name, every N minutes, function. Order matters within one pass: measure, then map, then act on the map.
TASKS = [
    ("census_ingest", 10, lambda: census.ingest(minutes=1.5)),
    ("intent_sample", 10, lambda: {k: v for k, v in intent.sample(pages=90).items() if k != "blocks"}),
    ("census_classify", 60, lambda: {"classified": census.classify(800), "events_named": census.name_events()}),
    ("prices", 60, _prices),
    ("oracle_audit", 360, lambda: oracle_audit.publish(oracle_audit.audit())[:16]),
    ("vaults", 360, lambda: vaults.publish(vaults.build())[:16]),
    ("session_risk", 1440, lambda: session_risk.publish(session_risk.build())[:16]),
    ("sellcheck", 360, lambda: sellcheck.publish(sellcheck.build())[:16]),
    ("projects", 1440, lambda: projects.publish(projects.build())[:16]),
    ("opportunity", 60, _opportunity),
    ("grade", 60, lambda: len(grade.run())),
    ("scoreboard", 60, lambda: scoreboard.OUT.write_text(scoreboard.build())),
    ("investigate", 30, lambda: investigate.run_once() if settings.INVESTIGATOR_ENABLED else "off"),
]


def due(state: dict, now: float) -> list:
    return [(n, every, fn) for n, every, fn in TASKS if now - state.get(n, 0) >= every * 60]


def run_pass(state: dict) -> dict:
    for name, _every, fn in due(state, time.time()):
        started = time.time()
        try:
            result = fn()
            log.info("%s ok in %.0fs: %s", name, time.time() - started, settings.redact(str(result))[:160])
        except Exception:
            log.error("%s failed:\n%s", name, settings.redact(traceback.format_exc())[-1500:])
        state[name] = time.time()   # a failed task waits for its next turn rather than spinning
        STATE.parent.mkdir(exist_ok=True)
        STATE.write_text(json.dumps(state))
    return state


def main() -> None:
    settings.LOG_DIR.mkdir(exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(settings.LOG_DIR / "sidecar.log")])
    for noisy in ("httpx", "urllib3", "web3"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    state = json.loads(STATE.read_text()) if STATE.exists() else {}
    if "--once" in sys.argv:
        run_pass(state)
        return
    log.info("sidecar started; investigator %s", "on" if settings.INVESTIGATOR_ENABLED else "off")
    while True:
        run_pass(state)
        time.sleep(30)


if __name__ == "__main__":
    main()
