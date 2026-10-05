"""Intent from failures: what people tried to do on the chain and could not.

The reverts scanner groups failed transactions by target and reason. This goes one step further:
each sampled transaction, failed or not, is counted by the contract it called and the function it
called there, so a failure can be read as a rate ("31% of calls to this function fail") rather than a
bare count, and the target is labelled with its kind from the census. The output is a ranked list of
intents that are failing unusually often, which is the raw material for demand nothing serves yet.

It samples: Blockscout lists recent transactions newest first, and the chain is busy enough that a
few hundred pages cover under a minute. Counts accumulate across runs in state/chain.db, so the
picture sharpens the more often this runs. Function names come from Blockscout when the contract is
verified, otherwise from the public signature database; both are data, never instructions.

    python -m agent.intent sample [--pages 90]
    python -m agent.intent report [--min-calls 30]
"""
from __future__ import annotations
import argparse
import json
import logging
import time
from collections import Counter
import httpx
from . import census, settings

log = logging.getLogger("intent")
SIG_API = "https://api.openchain.xyz/signature-database/v1/lookup"
RESERVE = 55  # requests left in the rate-limit window for the scan loop
SCHEMA = """
CREATE TABLE IF NOT EXISTS calls (address TEXT, selector TEXT, ok INTEGER DEFAULT 0, failed INTEGER DEFAULT 0,
  example_failed TEXT, last_reason TEXT, PRIMARY KEY (address, selector)) WITHOUT ROWID;
CREATE TABLE IF NOT EXISTS functions (selector TEXT PRIMARY KEY, name TEXT, source TEXT);
CREATE TABLE IF NOT EXISTS targets (address TEXT PRIMARY KEY, name TEXT, verified INTEGER);
"""


def _db():
    con = census.db()
    con.executescript(SCHEMA)
    return con


def _get(client: httpx.Client, url: str, params: dict, tries: int = 4) -> httpx.Response:
    for i in range(tries):
        try:
            r = client.get(url, params=params)
            if r.status_code == 429:
                raise httpx.TransportError("rate limited")
            return r
        except httpx.TransportError:
            if i == tries - 1:
                raise
            time.sleep(2 ** i)


def sample(pages: int) -> dict:
    """Read `pages` pages of recent transactions and fold them into the per-function counts."""
    con = _db()
    last_seen = int(census._meta(con, "intent_last_block", 0))
    base = settings.BLOCKSCOUT_API.rstrip("/")
    headers = {"x-api-key": settings.BLOCKSCOUT_API_KEY} if settings.BLOCKSCOUT_API_KEY else {}
    calls: dict = {}
    names, targets = {}, {}
    params: dict = {"filter": "validated"}
    seen = failed = 0
    newest = oldest = None
    with httpx.Client(timeout=30, headers=headers) as client:
        for _ in range(pages):
            try:
                r = _get(client, f"{base}/transactions", params)
                r.raise_for_status()
            except httpx.HTTPError as e:  # rate limit or outage: keep what was read so far
                log.warning("stopping sample early: %s", str(e)[:80])
                break
            j = r.json()
            # Blockscout allows 150 requests per five minutes on this key and the scan loop needs 40
            # of them; stop while there is still room for it.
            stop = int(r.headers.get("x-ratelimit-remaining", RESERVE + 1)) <= RESERVE
            for tx in j.get("items", []):
                bn = tx.get("block_number") or 0
                if bn <= last_seen:  # already counted in an earlier run
                    stop = True
                    break
                newest = max(newest or bn, bn)
                oldest = bn
                to = (tx.get("to") or {})
                raw = tx.get("raw_input") or "0x"
                if not to.get("hash") or not to.get("is_contract") or len(raw) < 10:
                    continue  # plain transfers and contract creations carry no function call
                sel = raw[:10].lower()
                seen += 1
                c = calls.setdefault((to["hash"], sel), [0, 0, None, None])
                if tx.get("status") == "error":
                    failed += 1
                    c[1] += 1
                    c[2] = c[2] or tx.get("hash")
                    rr = tx.get("revert_reason")
                    rr = (rr.get("decoded") or rr.get("raw")) if isinstance(rr, dict) else rr
                    c[3] = str(rr)[:120] if rr else c[3]
                else:
                    c[0] += 1
                m = tx.get("method")
                if m and not m.startswith("0x"):
                    names[sel] = m[:80]
                targets[to["hash"]] = (to.get("name"), 1 if to.get("is_verified") else 0)
            nxt = j.get("next_page_params")
            if stop or not nxt:
                break
            params = {"filter": "validated", **nxt}
    con.executemany("""INSERT INTO calls VALUES (?,?,?,?,?,?) ON CONFLICT DO UPDATE SET ok = ok + excluded.ok, failed = failed + excluded.failed,
                       example_failed = coalesce(excluded.example_failed, example_failed), last_reason = coalesce(excluded.last_reason, last_reason)""",
                    [(a, s, ok, bad, ex, rs) for (a, s), (ok, bad, ex, rs) in calls.items()])
    con.executemany("INSERT INTO functions VALUES (?,?,'blockscout') ON CONFLICT DO UPDATE SET name = excluded.name, source = excluded.source", list(names.items()))
    con.executemany("INSERT OR REPLACE INTO targets VALUES (?,?,?)", [(a, n, v) for a, (n, v) in targets.items()])
    if newest:
        con.execute("INSERT INTO meta VALUES ('intent_last_block', ?) ON CONFLICT(k) DO UPDATE SET v = excluded.v", (str(newest),))
    con.commit()
    return {"calls": seen, "failed": failed, "functions": len(calls), "blocks": [oldest, newest], "named_now": _name_functions(con)}


def _name_functions(con, limit: int = 100) -> int:
    rows = [r[0] for r in con.execute("""SELECT selector FROM calls WHERE selector NOT IN (SELECT selector FROM functions)
                                         GROUP BY selector ORDER BY sum(failed) DESC, sum(ok) DESC LIMIT ?""", (limit,))]
    named = 0
    for i in range(0, len(rows), 25):
        part = rows[i:i + 25]
        try:
            r = httpx.get(SIG_API, params={"function": ",".join(part), "filter": "true"}, timeout=30)
            r.raise_for_status()
            found = (r.json().get("result") or {}).get("function") or {}
        except Exception as e:
            log.warning("signature lookup failed: %s", e)
            continue
        for s in part:
            name = (found.get(s) or [{}])[0].get("name") if found.get(s) else None
            named += bool(name)
            con.execute("INSERT OR REPLACE INTO functions VALUES (?,?,'openchain')", (s, name and name[:120]))
    con.commit()
    return named


def report(min_calls: int = 30, top: int = 25) -> dict:
    con = _db()
    tot_ok, tot_bad = con.execute("SELECT coalesce(sum(ok),0), coalesce(sum(failed),0) FROM calls").fetchone()
    base = tot_bad / max(tot_ok + tot_bad, 1)
    rows = con.execute("""SELECT c.address, c.selector, c.ok, c.failed, c.example_failed, c.last_reason, f.name, k.kind, k.symbol, t.name, t.verified
                          FROM calls c LEFT JOIN functions f ON f.selector = c.selector LEFT JOIN contracts k ON k.address = c.address
                          LEFT JOIN targets t ON t.address = c.address WHERE c.ok + c.failed >= ? AND c.failed > 0""", (min_calls,)).fetchall()
    items = []
    for a, s, ok, bad, ex, reason, fn, kind, sym, tname, ver in rows:
        n = ok + bad
        items.append({"contract": a, "contract_kind": kind or "not in census", "contract_label": tname or sym, "verified": bool(ver),
                      "function": (fn or s).split("(")[0], "selector": s, "calls": n, "failed": bad, "failure_rate": round(bad / n, 4),
                      "excess_failures": round(bad - base * n, 1), "last_reason": reason, "example": ex})
    items.sort(key=lambda x: -x["excess_failures"])
    by_kind = Counter()
    for a, s, ok, bad, *_rest, kind, _sym, _tn, _v in rows:
        by_kind[kind or "not in census"] += bad
    return {"calls_sampled": tot_ok + tot_bad, "failed": tot_bad, "base_failure_rate": round(base, 4),
            "distinct_functions": con.execute("SELECT count(*) FROM calls").fetchone()[0],
            "failures_by_contract_kind": dict(by_kind.most_common()), "items": items[:top]}


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["sample", "report"])
    ap.add_argument("--pages", type=int, default=90)
    ap.add_argument("--min-calls", type=int, default=30)
    a = ap.parse_args()
    print(json.dumps(sample(a.pages) if a.cmd == "sample" else report(a.min_calls), indent=1))


if __name__ == "__main__":
    main()
