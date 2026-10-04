"""The judge. Fixed, boring, replayable. The proposer never edits this file; changing it is
itself a judged entry with proposer=human.

A rung's witness is a tiny expression over the scorecard, e.g. "stock_tokens.count >= 150".
Grammar: <path> <op> <number> [and ...]. Nothing else parses, on purpose: a witness the judge
cannot evaluate is refused, never guessed at.

Policy changes (Gate 1+) are judged with rails from config/northstar.md encoded here as checks.
"""
from __future__ import annotations
import json
import operator
import re
import sys
import yaml
from . import ledger, settings

OPS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt, "==": operator.eq}
CLAUSE = re.compile(r"^\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*(>=|<=|==|>|<)\s*(-?\d+(?:\.\d+)?)\s*$")

HELD_OUT_BUDGET = 20   # reads of any test slice before its figure is marked stale


def _lookup(card: dict, path: str):
    cur = card
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def eval_witness(witness: str, card: dict) -> tuple[bool | None, str]:
    for clause in witness.split(" and "):
        m = CLAUSE.match(clause)
        if not m:
            return None, f"unparseable witness clause: {clause!r}"
        path, op, num = m.groups()
        val = _lookup(card, path)
        if not isinstance(val, (int, float)):
            return None, f"{path} is not a number in the scorecard ({val!r})"
        if not OPS[op](val, float(num)):
            return False, f"{path}={val} fails {op} {num}"
    return True, "all clauses hold"


def judge_rungs(card: dict, card_hash: str) -> list[dict]:
    rungs = yaml.safe_load(settings.RUNGS_PATH.read_text()) or []
    results = []
    for r in rungs:
        if r.get("status") != "open":
            continue
        ok, why = eval_witness(r["witness"], card)
        verdict = "met" if ok else ("refused" if ok is False else "unjudgeable")
        results.append({"kind": "rung", "rung": r["id"], "title": r["title"], "witness": r["witness"],
                        "scorecard": card_hash, "verdict": verdict, "why": why, "proposer": "human"})
    return results


def check_policy_rails(before: dict, after: dict) -> list[str]:
    """Hard rails from northstar.md. Any violation refuses the change."""
    problems = []
    max_mult = before.get("max_cap_increase_24h", 2.0)
    caps_before = {l["symbol"]: l for l in before.get("listings", [])}
    for l in after.get("listings", []):
        prev = caps_before.get(l["symbol"])
        if prev is None:
            if not l.get("chainlink_feed"):
                problems.append(f"{l['symbol']}: listing without a Chainlink feed")
            if not l.get("halt_free_30d"):
                problems.append(f"{l['symbol']}: listing without 30 halt-free days")
            if l.get("liquidation_depth_usd", 0) < 5 * l.get("cap_usd", 0):
                problems.append(f"{l['symbol']}: liquidation depth < 5x cap")
        elif l.get("cap_usd", 0) > max_mult * prev.get("cap_usd", 0):
            problems.append(f"{l['symbol']}: cap raised > {max_mult}x in one change")
    if after.get("version") != before.get("version", 0) + 1 or after.get("parent") != ledger.sha(before):
        problems.append("policy version/parent do not chain to the current policy")
    return problems


def judge_policy_change(before: dict, after: dict, proposer: str, witness: str, card: dict, card_hash: str) -> dict:
    problems = check_policy_rails(before, after)
    ok, why = eval_witness(witness, card)
    if problems:
        verdict, reason = "refused", "; ".join(problems)
    elif ok is None:
        verdict, reason = "unjudgeable", why
    elif not ok:
        verdict, reason = "refused", why
    else:
        verdict, reason = "adoptable", why  # adoption is a human click until Gate 4
    return {"kind": "policy", "proposer": proposer, "policy_before": ledger.sha(before), "policy_after": ledger.sha(after),
            "witness": witness, "scorecard": card_hash, "verdict": verdict, "why": reason, "after": after}


def replay(entry_hash_prefix: str) -> None:
    for e in ledger.entries():
        if e["hash"].startswith(entry_hash_prefix):
            card = ledger.read_scorecard(e["scorecard"][:16]) if e.get("scorecard") else None
            if card is None:
                print("scorecard not found; cannot replay"); return
            ok, why = eval_witness(e["witness"], card)
            verdict = "met" if ok else ("refused" if ok is False else "unjudgeable")
            same = verdict == e["verdict"] or (e["verdict"] == "adoptable" and verdict == "met")
            print(f'recorded={e["verdict"]} replayed={verdict} {"SAME" if same else "DIFFERS"}: {why}')
            return
    print("no such entry")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "replay" and len(sys.argv) > 2:
        replay(sys.argv[2])
    elif cmd == "propose":
        cards = sorted(settings.SCORECARD_DIR.glob("*.json"))
        if not cards:
            print("no scorecards yet; run a scan first"); sys.exit(1)
        card = json.loads(cards[-1].read_bytes())
        for r in judge_rungs(card, ledger.sha(card)):
            print(f'{r["rung"]:4s} {r["verdict"]:12s} {r["why"]}')
    else:
        print("usage: python -m agent.judge propose | replay <hash>")
