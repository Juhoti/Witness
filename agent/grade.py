"""Outcome grading: every claim the agent records carries a check, and the check is run later.

A finding is a claim, not a fact. When one is written it must say what would show it right or wrong,
as a check in the judge's own grammar over the published measurements, and when that check falls due.
This module runs the checks that have fallen due and publishes the grade. Nothing is graded by a
model: the check is fixed when the claim is made and evaluated by the same code that judges rungs.

What a check can refer to (the "world"), all from published or reproducible measurements:
  the latest live scorecard's sections      stock_tokens.count, morpho.markets, feeds.count, ...
  census.coverage_by_contract_kind, census.coverage_by_named_event, census.logs
  contract.<address>.logs, contract.<address>.event_types, contract.<address>.known   (0 or 1)
  oracle.<finding>.markets, oracle.<finding>.borrow_usd
  opportunity.totals.gaps, opportunity.totals.risk_usd, opportunity.totals.unexplained

    python -m agent.grade
"""
from __future__ import annotations
import json
import time
from . import census, judge, ledger, settings

FINDINGS = settings.LEDGER_DIR / "findings"
GRADES = settings.LEDGER_DIR / "grades"
STATE = settings.STATE_DIR / "graded.json"


def _latest(directory, skip_backfilled: bool = False) -> dict:
    best = None
    for p in sorted(directory.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
        d = json.loads(p.read_bytes())
        if not (skip_backfilled and d.get("backfilled")):
            best = d
            break
    return best or {}


def world() -> dict:
    w = dict(_latest(settings.SCORECARD_DIR, skip_backfilled=True))
    try:
        con = census.db()
        r = census.report()
        w["census"] = {k: r[k] for k in ("coverage_by_contract_kind", "coverage_by_named_event", "logs", "contracts")}
        w["contract"] = {a: {"logs": n, "event_types": t, "known": int(k not in (None, "unknown"))}
                         for a, n, t, k in con.execute("""SELECT c.address, c.logs, (SELECT count(DISTINCT topic0) FROM activity a WHERE a.address = c.address), c.kind
                                                           FROM contracts c ORDER BY c.logs DESC LIMIT 5000""")}
    except Exception:
        w["census"], w["contract"] = {}, {}
    oa = settings.STATE_DIR / "oracle_audit.json"
    w["oracle"] = json.loads(oa.read_text()).get("by_finding", {}) if oa.exists() else {}
    opp = settings.LEDGER_DIR / "opportunities"
    w["opportunity"] = {"totals": _latest(opp).get("totals", {})} if opp.exists() else {}
    return w


def run(now: int | None = None) -> list[dict]:
    now = now or int(time.time())
    done = set(json.loads(STATE.read_text())) if STATE.exists() else set()
    due = []
    for p in sorted(FINDINGS.glob("*.json")) if FINDINGS.exists() else []:
        f = json.loads(p.read_bytes())
        h = p.stem.split("_", 1)[1]
        pred = f.get("prediction") or {}
        if h not in done and pred.get("check") and pred.get("due_ts", 0) <= now:
            due.append((h, f))
    if not due:
        return []
    w = world()
    out = []
    GRADES.mkdir(parents=True, exist_ok=True)
    for h, f in due:
        ok, why = judge.eval_witness(f["prediction"]["check"], w)
        grade = {"kind": "grade", "ts": now, "finding": h, "subject": f.get("subject"), "check": f["prediction"]["check"],
                 "result": "held" if ok is True else "failed" if ok is False else "ungradeable", "why": why,
                 "graded_against": {"scorecard_ts": w.get("ts"), "census_logs": (w.get("census") or {}).get("logs")}}
        gh = ledger.sha(grade)
        (GRADES / f"{time.strftime('%Y-%m-%d', time.gmtime(now))}_{gh[:16]}.json").write_bytes(ledger.canonical(grade))
        done.add(h)
        out.append(grade)
        if grade["result"] == "held" and f.get("demand_hypothesis") and (f.get("confidence") or 0) >= 0.6:
            request(h, f, gh)
    STATE.parent.mkdir(exist_ok=True)
    STATE.write_text(json.dumps(sorted(done)))
    return out


def request(finding_hash: str, finding: dict, grade_hash: str) -> str:
    """A claim that named unmet demand and then held up becomes an open request: something worth
    measuring properly or building for. Requests are the builder's queue at Gate 2; until then they
    are a list for a person to read. Writing one changes nothing else."""
    d = settings.LEDGER_DIR / "requests"
    d.mkdir(parents=True, exist_ok=True)
    doc = {"kind": "request", "status": "open", "ts": int(time.time()), "from_finding": finding_hash, "graded_by": grade_hash,
           "subject": finding.get("subject"), "category": finding.get("category"), "what_it_is": finding.get("what_it_is"),
           "demand_hypothesis": finding.get("demand_hypothesis"), "confidence": finding.get("confidence")}
    h = ledger.sha(doc)
    (d / f"{time.strftime('%Y-%m-%d', time.gmtime(doc['ts']))}_{h[:16]}.json").write_bytes(ledger.canonical(doc))
    return h


def record() -> dict:
    """How the agent's past claims have turned out: the number it reads before making new ones."""
    tally = {"held": 0, "failed": 0, "ungradeable": 0}
    for p in GRADES.glob("*.json") if GRADES.exists() else []:
        tally[json.loads(p.read_bytes()).get("result", "ungradeable")] += 1
    graded = tally["held"] + tally["failed"]
    return {**tally, "hit_rate": round(tally["held"] / graded, 3) if graded else None}


if __name__ == "__main__":
    gs = run()
    print(f"graded {len(gs)} claims now due;", "record so far:", record())
    for g in gs:
        print(f"  {g['result']:11} {g['check']}  ({g['why']})")
