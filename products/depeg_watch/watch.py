"""De-peg and risky-market watch: warns before fragile prices bite, not after.

Every run (every 10 minutes from the sidecar) it checks three things and alerts only when a level
changes, so a quiet market sends nothing:

  meta-oracles     markets priced by a primary/backup wrapper with over $1M borrowed (the USDe
                   market: a constant 1.00 with a live backup feed that takes over only after a
                   0.5% gap lasts 16 hours and someone calls it). Read live on chain: both prices,
                   the gap, and whether a challenge is running. Levels: ok, watch (gap at half the
                   threshold), challengeable, challenged (with the time it can switch), on backup.
  fragile prices   other flagged markets with over $1M borrowed: the oracle's price against the
                   collateral's Uniswap price. Level "diverging" past 2%.
  new risk         markets lending above the loan-to-value their collateral's history survives,
                   and markets newly flagged by the oracle audit with over $10k borrowed.

Current levels go to feed/watch.json; each change is appended to ledger/watch/ and sent through the
bot to everyone who asked for alerts and to the owner.

    python -m products.depeg_watch.watch
"""
from __future__ import annotations
import json
import time
from web3 import Web3
from agent import chain, ledger, settings

STATE = settings.STATE_DIR / "watch.json"
FEED = settings.ROOT / "feed" / "watch.json"
OUT = settings.LEDGER_DIR / "watch"
META_FNS = ("primaryPrice()", "backupPrice()", "getDeviation()", "deviationThreshold()", "isChallenged()",
            "challengeExpiresAt()", "isBackup()")


def _sel(s: str) -> bytes:
    return Web3.keccak(text=s)[:4]


def meta_level(dev: float, threshold: float, challenged: bool, expires: int, on_backup: bool) -> str:
    if on_backup:
        return "on_backup"
    if challenged:
        return "challenged"
    if dev >= threshold:
        return "challengeable"
    if dev >= threshold / 2:
        return "watch"
    return "ok"


def _latest(folder: str) -> dict:
    d = settings.LEDGER_DIR / folder
    fs = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime) if d.exists() else []
    return json.loads(fs[-1].read_bytes()) if fs else {}


def run(send=None) -> dict:
    audit = json.loads((settings.STATE_DIR / "oracle_audit.json").read_text())
    levels: dict[str, dict] = {}
    metas = [m for m in audit["items"] if m.get("meta_oracle") and m.get("borrow_usd", 0) >= 1_000_000]
    raws = chain.multicall([(m["oracle"], _sel(f)) for m in metas for f in META_FNS])
    for i, m in enumerate(metas):
        r = raws[i * len(META_FNS):(i + 1) * len(META_FNS)]
        prim, back, dev, thr, chal, exp, onb = (chain.decode_uint(x) for x in r)
        if dev is None or thr is None:
            continue
        lvl = meta_level(dev / 1e18, thr / 1e18, bool(chal), exp or 0, bool(onb))
        levels[f"meta:{m['market']}"] = {"level": lvl, "market": m["market"], "pair": f"{m['collateral']}/{m['loan']}", "borrow_usd": m["borrow_usd"],
                                         "gap": round(dev / 1e18, 6), "threshold": thr / 1e18, "challenge_expires": exp or None,
                                         "primary": prim, "backup": back,
                                         "text": f"{m['collateral']}/{m['loan']} market (${m['borrow_usd'] / 1e6:,.0f}M borrowed): live feed is "
                                                 f"{dev / 1e16:.2f}% from the fixed price; switch threshold {thr / 1e16:.2f}%."}
    for m in audit["items"]:
        if m["finding"] in ("unlisted_feed", "frozen", "vault_rate_only") and m.get("borrow_usd", 0) >= 1_000_000 and m.get("gap_to_uniswap") is not None:
            g = m["gap_to_uniswap"]
            levels[f"price:{m['market']}"] = {"level": "diverging" if abs(g) > 0.02 else "ok", "market": m["market"], "pair": f"{m['collateral']}/{m['loan']}",
                                              "borrow_usd": m["borrow_usd"], "gap": g,
                                              "text": f"{m['collateral']}/{m['loan']} market (${m['borrow_usd'] / 1e6:,.1f}M borrowed): oracle is {g:+.2%} from the Uniswap price."}
    sr = _latest("session_risk")
    for x in sr.get("exposed_markets", []):
        levels[f"tier:{x['market']}"] = {"level": "above_safe_tier", "market": x["market"], "pair": f"{x['collateral']}/USDG", "borrow_usd": x["borrow_usd"],
                                         "text": f"New {x['collateral']} market lends at {x['lltv']:.1%}; {x['collateral']}'s worst closed-market fall in 5 years "
                                                 f"({x['worst_closed_fall']:.1%}) is only survived up to {x['max_tier_surviving_worst'] or 0:.1%}."}
    for m in audit["items"]:
        if m["finding"] in ("constant", "frozen", "vault_rate_only", "fixed_with_delayed_backup", "no_oracle") and m.get("borrow_usd", 0) >= 10_000:
            levels.setdefault(f"oracle:{m['market']}", {"level": m["finding"], "market": m["market"], "pair": f"{m['collateral']}/{m['loan']}",
                                                        "borrow_usd": m["borrow_usd"], "text": f"{m['collateral']}/{m['loan']} market (${m['borrow_usd']:,.0f} borrowed): {m['reading']}."})
    before = json.loads(STATE.read_text()) if STATE.exists() else {}
    first_run = not before
    changes = [(k, before.get(k, {}).get("level"), v) for k, v in levels.items() if before.get(k, {}).get("level") != v["level"]]
    now = int(time.time())
    if changes:
        OUT.mkdir(parents=True, exist_ok=True)
        doc = {"kind": "watch", "ts": now, "chain_id": settings.CHAIN["chain"]["id"], "first_run": first_run,
               "changes": [{"subject": k, "from": a, "to": v["level"], **{kk: vv for kk, vv in v.items() if kk != "level"}} for k, a, v in changes]}
        h = ledger.sha(doc)
        (OUT / f"{time.strftime('%Y-%m-%d', time.gmtime(now))}_{h[:16]}.json").write_bytes(ledger.canonical(doc))
        alertable = [(k, a, v) for k, a, v in changes if v["level"] not in ("ok",) and not (first_run and v["level"] in ("watch",))]
        if send and alertable and not first_run:
            for k, a, v in alertable[:10]:
                send(f"Witness watch: {v['level'].replace('_', ' ')}\n{v['text']}\nmarket {v['market'][:18]}…\n(measurement, not advice)")
    STATE.write_text(json.dumps(levels))
    FEED.parent.mkdir(exist_ok=True)
    FEED.write_text(json.dumps({"as_of": now, "method": "live on-chain reads of meta-oracles; oracle audit; session risk", "levels": levels}, indent=1, sort_keys=True))
    return {"subjects": len(levels), "changes": len(changes), "first_run": first_run,
            "not_ok": sorted({v["level"] for v in levels.values() if v["level"] != "ok"})}


if __name__ == "__main__":
    print(json.dumps(run(), indent=1))
