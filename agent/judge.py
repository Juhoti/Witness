"""The judge. Fixed, boring, replayable. The proposer never edits this file; changing it is
itself a judged entry with proposer=human.

A rung's witness is a tiny expression over the scorecard, e.g. "stock_tokens.count >= 150".
Grammar: <path> <op> <number> [and ...]. Nothing else parses, on purpose: a witness the judge
cannot evaluate is refused, never guessed at.

Gate 1 adds three things, all in this file so that changing them is visible:

  policy certificates   config/policy.json changes only through `judge_policy_change`, which checks
                        the rails, evaluates the witness against a scorecard, and appends a certificate
                        carrying the policy before and after in full. `adopt` is the human click: it
                        writes the policy only from an adoptable certificate whose "before" is the
                        policy on disk now, and records the adoption as its own entry.
  replay                `replay <hash>` re-derives any rung or policy certificate from the hashed
                        inputs it names and reports whether the verdict is the same.
  held-out budget       a test slice may be read HELD_OUT_BUDGET times; each read is a public ledger
                        entry. Past the budget, `figure()` refuses to report a number from that slice.
                        Peeking to go faster is the failure this exists to prevent.
"""
from __future__ import annotations
import json
import operator
import re
import sys
import time
import yaml
from . import ledger, settings

OPS = {">=": operator.ge, "<=": operator.le, ">": operator.gt, "<": operator.lt, "==": operator.eq}
CLAUSE = re.compile(r"^\s*([a-zA-Z_][a-zA-Z0-9_.]*)\s*(>=|<=|==|>|<)\s*(-?\d+(?:\.\d+)?)\s*$")

HELD_OUT_BUDGET = 20   # reads of any test slice before its figure is refused
BAD_ORACLES = ("no_oracle", "unrecognised_unchanged", "constant", "fixed_with_delayed_backup", "frozen", "vault_rate_only", "unlisted_feed")


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
        if not isinstance(val, (int, float)) or isinstance(val, bool):
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


# --- policy -------------------------------------------------------------------------------------

def check_policy_rails(before: dict, after: dict) -> list[str]:
    """Hard rails from northstar.md. Any violation refuses the change. A listing must carry the
    measurements the rails need; a missing measurement is a violation, never a pass."""
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
            if l.get("oracle_finding", "unknown") in BAD_ORACLES or l.get("oracle_finding") is None:
                problems.append(f"{l['symbol']}: oracle finding {l.get('oracle_finding')!r} is not acceptable")
        elif l.get("cap_usd", 0) > max_mult * prev.get("cap_usd", 0):
            problems.append(f"{l['symbol']}: cap raised > {max_mult}x in one change")
    if after.get("max_cap_increase_24h", max_mult) > max_mult:
        problems.append("max_cap_increase_24h may not be loosened by a policy change")
    if after.get("version") != before.get("version", 0) + 1 or after.get("parent") != ledger.sha(before):
        problems.append("policy version/parent do not chain to the current policy")
    return problems


def judge_policy_change(before: dict, after: dict, proposer: str, witness: str, card: dict, card_hash: str) -> dict:
    """The certificate for a proposed policy. Carries both policies in full so it can be replayed
    and adopted from the ledger alone."""
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
            "before": before, "after": after, "witness": witness, "scorecard": card_hash, "verdict": verdict, "why": reason}


def current_policy() -> dict:
    return json.loads(settings.POLICY_PATH.read_text())


def propose_policy(after: dict, proposer: str, witness: str, card: dict, card_hash: str) -> dict:
    """Judge `after` against the policy on disk and append the certificate. Returns the entry as written."""
    cert = judge_policy_change(current_policy(), after, proposer, witness, card, card_hash)
    cert["hash"] = ledger.append_entry(cert)
    return cert


def adopt(entry_hash_prefix: str, by: str = "human") -> dict:
    """The human click. Writes the certificate's policy to disk only if the certificate is adoptable
    and its "before" is the policy on disk right now, then records the adoption."""
    cert = next((e for e in ledger.entries() if e["hash"].startswith(entry_hash_prefix) and e.get("kind") == "policy"), None)
    if cert is None:
        raise ValueError("no policy certificate with that hash")
    if cert["verdict"] != "adoptable":
        raise ValueError(f"certificate verdict is {cert['verdict']}, not adoptable")
    if ledger.sha(current_policy()) != cert["policy_before"]:
        raise ValueError("the policy on disk is no longer the one this certificate was judged against")
    settings.POLICY_PATH.write_text(json.dumps(cert["after"], indent=2) + "\n")
    adoption = {"kind": "adoption", "proposer": by, "certificate": cert["hash"], "policy_after": cert["policy_after"],
                "verdict": "adopted", "why": f"adopted by {by}"}
    adoption["hash"] = ledger.append_entry(adoption)
    return adoption


# --- replay -------------------------------------------------------------------------------------

def replay_entry(e: dict) -> tuple[str | None, str]:
    """Re-derive one certificate's verdict from the inputs it names. Returns (verdict or None, why)."""
    if e.get("kind") == "rung":
        card = ledger.read_scorecard(e["scorecard"][:16]) if e.get("scorecard") else None
        if card is None:
            return None, "scorecard not found"
        ok, why = eval_witness(e["witness"], card)
        return ("met" if ok else "refused" if ok is False else "unjudgeable"), why
    if e.get("kind") == "policy":
        card = ledger.read_scorecard(e["scorecard"][:16]) if e.get("scorecard") else None
        if card is None or "before" not in e or "after" not in e:
            return None, "inputs not found (scorecard, before, after)"
        if ledger.sha(e["before"]) != e["policy_before"] or ledger.sha(e["after"]) != e["policy_after"]:
            return None, "the policies in the entry do not match the hashes it names"
        cert = judge_policy_change(e["before"], e["after"], e["proposer"], e["witness"], card, e["scorecard"])
        return cert["verdict"], cert["why"]
    return None, f"no replay for kind {e.get('kind')!r}"


def replay(entry_hash_prefix: str) -> bool:
    for e in ledger.entries():
        if e["hash"].startswith(entry_hash_prefix):
            verdict, why = replay_entry(e)
            same = verdict == e["verdict"]
            print(f'recorded={e["verdict"]} replayed={verdict} {"SAME" if same else "DIFFERS"}: {why}')
            return same
    print("no such entry")
    return False


# --- held-out budget ------------------------------------------------------------------------------

def slice_reads(name: str) -> int:
    return sum(1 for e in ledger.entries() if e.get("kind") == "slice_read" and e.get("slice") == name)


def read_slice(name: str, by: str, purpose: str) -> bool:
    """Record a read of a held-out slice. Returns False, and records nothing, once the budget is spent:
    the slice may not be read again."""
    n = slice_reads(name)
    if n >= HELD_OUT_BUDGET:
        return False
    ledger.append_entry({"kind": "slice_read", "slice": name, "proposer": by, "purpose": purpose[:200],
                         "read_number": n + 1, "budget": HELD_OUT_BUDGET, "verdict": "recorded", "why": f"read {n + 1} of {HELD_OUT_BUDGET}"})
    return True


def figure(name: str, value):
    """A number computed on a held-out slice, reported only while the slice is within budget."""
    n = slice_reads(name)
    if n > HELD_OUT_BUDGET:
        return {"slice": name, "value": None, "reads": n, "budget": HELD_OUT_BUDGET, "refused": True,
                "why": "held-out slice read more than its budget; figure withheld"}
    return {"slice": name, "value": value, "reads": n, "budget": HELD_OUT_BUDGET, "refused": False}


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "replay" and len(sys.argv) > 2:
        sys.exit(0 if replay(sys.argv[2]) else 1)
    elif cmd == "propose":
        cards = sorted(settings.SCORECARD_DIR.glob("*.json"))
        if not cards:
            print("no scorecards yet; run a scan first"); sys.exit(1)
        card = json.loads(cards[-1].read_bytes())
        for r in judge_rungs(card, ledger.sha(card)):
            print(f'{r["rung"]:4s} {r["verdict"]:12s} {r["why"]}')
    elif cmd == "policy" and len(sys.argv) > 4:
        # python -m agent.judge policy <proposed.json> <proposer> "<witness>"
        after = json.loads(open(sys.argv[2]).read())
        cards = sorted((p for p in settings.SCORECARD_DIR.glob("*.json")), key=lambda p: p.stat().st_mtime)
        card = json.loads(cards[-1].read_bytes())
        cert = propose_policy(after, sys.argv[3], sys.argv[4], card, ledger.sha(card))
        print(f'{cert["verdict"]} {cert["hash"][:16]}: {cert["why"]}')
    elif cmd == "adopt" and len(sys.argv) > 2:
        a = adopt(sys.argv[2])
        print(f'adopted; policy now {a["policy_after"][:16]}, adoption entry {a["hash"][:16]}')
    elif cmd == "slices":
        names = sorted({e["slice"] for e in ledger.entries() if e.get("kind") == "slice_read"})
        for n in names:
            print(f"{n}: {slice_reads(n)} of {HELD_OUT_BUDGET} reads used")
    else:
        print("usage: python -m agent.judge propose | replay <hash> | policy <file> <proposer> <witness> | adopt <hash> | slices")
