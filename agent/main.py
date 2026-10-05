"""The loop. Wake, scan, score, judge rungs, append ledger, sleep. Gate 0 holds no keys."""
from __future__ import annotations
import json
import logging
import sys
import time
import traceback
from . import settings, ledger, gap, judge, notify, propose, build, memory, history
from .scan import stock_tokens, morpho, uniswap, reverts, perps, feeds, depth

settings.LOG_DIR.mkdir(exist_ok=True)
logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(settings.LOG_DIR / "agent.log")],
)
logging.getLogger("httpx").setLevel(logging.WARNING)  # its INFO line prints request URLs, and the Telegram URL carries the bot token
log = logging.getLogger("main")


def scan_once() -> dict:
    card: dict = {"chain_id": settings.CHAIN["chain"]["id"], "ts": int(time.time())}
    for name, fn in (("stock_tokens", stock_tokens.scan), ("morpho", morpho.scan),
                     ("uniswap", uniswap.scan), ("reverts", reverts.scan), ("perps", perps.scan)):
        try:
            card[name] = fn()
        except Exception as e:
            err = settings.redact(str(e))  # never let a keyed RPC URL reach the log, Telegram, or the ledger
            log.error("scanner %s failed: %s", name, err)
            card[name] = {"error": err}
            notify.send(f"scanner {name} failed: {err}")
    # Derived sections: each reads what the scanners above put in the card.
    for name, fn in (("feeds", feeds.scan), ("depth", depth.scan), ("pause_history", history.update)):
        try:
            card[name] = fn(card)
        except Exception as e:
            err = settings.redact(str(e))
            log.error("scanner %s failed: %s", name, err)
            card[name] = {"error": err}
            notify.send(f"scanner {name} failed: {err}")
    card["gap"] = gap.build(card)
    return card


def health_alerts(card: dict, prev: dict | None) -> None:
    toks = {t["symbol"]: t for t in card.get("stock_tokens", {}).get("tokens", []) if t.get("symbol")}
    if prev:
        ptoks = {t["symbol"]: t for t in prev.get("stock_tokens", {}).get("tokens", []) if t.get("symbol")}
        for sym, t in toks.items():
            p = ptoks.get(sym)
            if p and t.get("paused") and not p.get("paused"):
                notify.send(f"HALT: {sym} paused")
            if p and t.get("multiplier_raw") and p.get("multiplier_raw") and t["multiplier_raw"] != p["multiplier_raw"]:
                notify.send(f"MULTIPLIER STEP: {sym} {p['multiplier_raw']} -> {t['multiplier_raw']}")


def tick(prev: dict | None) -> dict:
    card = scan_once()
    h = ledger.write_scorecard(card)
    try:
        memory.remember_scorecard(card)
        card["vault_snapshot"] = memory.snapshot()
    except Exception as e:
        log.warning("vault update failed: %s", e)
    log.info("scorecard %s: tokens=%s morpho=%s reverts=%s feeds=%s depth=%s candidates=%s pass_rails=%s", h[:16],
             card.get("stock_tokens", {}).get("count"), card.get("morpho", {}).get("markets"),
             card.get("reverts", {}).get("clusters"), card.get("feeds", {}).get("count"),
             card.get("depth", {}).get("count"), card["gap"]["candidates"], card["gap"].get("pass_rails"))
    for r in judge.judge_rungs(card, h):
        ledger.append_entry({**r, "vault": card.get("vault_snapshot")})
        memory.append_daily(f"rung {r['rung']} {r['verdict']}: {r['why']}")
        log.info("rung %s %s: %s", r["rung"], r["verdict"], r["why"])
    for p in propose.propose(card["gap"]):  # empty in Gate 0/1
        ledger.append_entry({"kind": "proposal", "proposer": "model", "verdict": "pending", **p, "scorecard": h, "vault": card.get("vault_snapshot")})
        memory.append_daily(f"proposal: {p.get('title')}")
        notify.send(f"PROPOSAL: {p.get('title')} (needs your review)")
    # Builder: any rung in config/rungs.yaml with status "build" gets one attempt per tick.
    import yaml
    for r in (yaml.safe_load(settings.RUNGS_PATH.read_text()) or []):
        if r.get("status") == "build":
            cert = build.build(r, h)
            notify.send(f"BUILD {r['id']}: {cert['verdict']} — {cert['why']}")
    health_alerts(card, prev)
    return card


def main() -> None:
    once = "--once" in sys.argv
    prev = None
    if not once:  # agent.gate counts clean scans from here: a restart starts the run again
        settings.STATE_DIR.mkdir(exist_ok=True)
        (settings.STATE_DIR / "loop_started").write_text(str(int(time.time())))
    notify.send("witness agent started")
    while True:
        started = time.time()
        try:
            prev = tick(prev)
        except Exception:
            log.error("tick failed:\n%s", settings.redact(traceback.format_exc()))
            notify.send("tick failed; see logs/agent.log")
        if once:
            return
        # fixed cadence: the interval is start-to-start, not a pause after the scan
        time.sleep(max(30.0, settings.SCAN_EVERY_MINUTES * 60 - (time.time() - started)))


if __name__ == "__main__":
    main()
