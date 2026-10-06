"""The open data feed: the record, in a few stable files anyone can fetch.

Everything in the ledger is already public, but it is hash-named and spread across kinds. The feed
is the same data arranged for use: one file per question, overwritten each hour, served from the
public repository. Every file says when it was measured, which ledger file it came from (by hash,
so it can be checked), the method, and that it is a measurement and not an oracle.

  feed/index.json        what the files are, when each was built, and the ledger hash behind each
  feed/collateral.json   every stock token against the listing rails: feed, depth, pause-free days,
                         exchange halts, the loan-to-value tier its history survives, existing markets
  feed/prices.json       Witness's reference price per asset, with sources and the gap to Chainlink
  feed/oracles.json      how every Morpho market is priced, what was observed and how it is read
  feed/warnings.json     tokens whose holders cannot sell, by simulation
  feed/projects.json     projects on the chain measured beside their claims
  feed/opportunities.json  the ranked map of gaps, risks and the unexplained

    python -m agent.feed       # rebuild feed/ from the latest record
"""
from __future__ import annotations
import json
import time
from . import opportunity, settings

FEED = settings.ROOT / "feed"
DISCLAIMER = ("Measurements from Robinhood Chain (4663) by Witness, with method and age attached. Not an oracle: no manipulation "
              "resistance beyond what each method states, no availability promise. Check any figure against the ledger file it names.")


def _latest_with_hash(directory):
    best = None
    for p in directory.glob("*.json") if directory.exists() else []:
        if best is None or p.stat().st_mtime > best.stat().st_mtime:
            best = p
    if best is None:
        return None, None
    return json.loads(best.read_bytes()), best.name


def build() -> dict:
    FEED.mkdir(exist_ok=True)
    now = int(time.time())
    index = {"kind": "feed", "built": now, "disclaimer": DISCLAIMER, "repository": "github.com/Juhoti/Witness", "files": {}}

    def put(name: str, doc: dict, source: str | None, note: str):
        doc = {"as_of": doc.get("ts"), "ledger_file": source, "method": doc.get("method"), "disclaimer": DISCLAIMER, **doc}
        (FEED / f"{name}.json").write_text(json.dumps(doc, indent=1, sort_keys=True, default=str))
        index["files"][f"{name}.json"] = {"what": note, "as_of": doc.get("as_of"), "ledger_file": source}

    card = opportunity._latest(settings.SCORECARD_DIR, live_only=True) or {}
    sr, sr_src = _latest_with_hash(settings.LEDGER_DIR / "session_risk")
    feeds, depth, ph = (card.get("feeds") or {}).get("tokens", {}), (card.get("depth") or {}).get("tokens", {}), (card.get("pause_history") or {}).get("tokens", {})
    items = []
    for t in card.get("stock_tokens", {}).get("tokens", []):
        s = t["symbol"]; f = feeds.get(s) or {}; d = depth.get(s) or {}; r = (sr or {}).get("items", {}).get(s, {})
        items.append({"symbol": s, "address": t["address"], "multiplier_raw": t.get("multiplier_raw"), "paused": t.get("paused"),
                      "chainlink_feed": f.get("feed") if f.get("answers_on_chain") else None, "feed_age_s": f.get("age_s"), "feed_price_usd": f.get("price_usd"),
                      "depth_usd_5pct": d.get("depth_usd_5pct"), "max_cap_by_depth_usd": (d.get("depth_usd_5pct") or 0) / 5,
                      "pause_free_days": (ph.get(s) or {}).get("pause_free_days"), "exchange_halt_free_30d": r.get("exchange_halt_free_30d"),
                      "days_since_exchange_halt": r.get("days_since_exchange_halt"), "worst_closed_fall_5y": r.get("worst_closed_fall"),
                      "max_lltv_tier_surviving_worst": r.get("max_tier_surviving_worst"),
                      "passes_listing_rails": bool(f.get("answers_on_chain") and (d.get("depth_usd_5pct") or 0) > 0 and ((ph.get(s) or {}).get("pause_free_days") or 0) >= 30),
                      "markets": r.get("markets", [])})
    items.sort(key=lambda x: (not x["passes_listing_rails"], -(x["depth_usd_5pct"] or 0)))
    put("collateral", {"ts": card.get("ts"), "method": "scan loop: Chainlink directory + on-chain reads, Uniswap quoter sales at size, pause flags at scan times, "
                       "US exchange halt record, five years of share-market gaps (priors)", "rails": "Chainlink feed, 30 pause-free days, depth >= 5x cap, acceptable oracle",
                       "count": len(items), "passing": sum(1 for x in items if x["passes_listing_rails"]), "items": items},
        None, "every stock token against the listing rails")
    for name, folder, note in (("prices", "prices", "Witness's reference price per asset"), ("oracles", "oracle_audits", "how every Morpho market is priced"),
                               ("warnings", "warnings", "tokens whose holders cannot sell"), ("projects", "projects", "projects measured beside their claims"),
                               ("opportunities", "opportunities", "ranked gaps, risks and the unexplained")):
        doc, src = _latest_with_hash(settings.LEDGER_DIR / folder)
        if doc:
            put(name, doc, src, note)
    (FEED / "index.json").write_text(json.dumps(index, indent=1, sort_keys=True))
    return index


if __name__ == "__main__":
    ix = build()
    print(f"feed built {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(ix['built']))}:", ", ".join(ix["files"]))
