"""The opportunity map: every measured gap, risk and unexplained thing on the chain, in one ranked list.

Each scanner answers its own question. This puts the answers side by side so the next question can be
chosen by size rather than by habit. Three queues:

  gaps         demand that is measured and unserved or under-served, in dollars, with what stands in
               the way (which rail fails, what is missing)
  risks        places where other people's money depends on something fragile; watched, not served
  unexplained  activity the agent cannot yet name: contracts, events and failing calls. This is the
               investigator's work queue.

Every item says what was measured, from which source, who serves it today and what a product would
need. An item keeps the same id from one map to the next, so the map also records how long a gap has
stood: a gap seen for thirty days is better evidence than one seen once.

    python -m agent.opportunity     # build from the latest measurements, publish to ledger/opportunities/
"""
from __future__ import annotations
import hashlib
import json
import time
from . import census, intent, ledger, settings

OUT_DIR = settings.LEDGER_DIR / "opportunities"
HISTORY = settings.STATE_DIR / "opportunity_history.json"
DAY = 86400


def _id(kind: str, subject: str) -> str:
    return hashlib.sha256(f"{kind}|{subject}".encode()).hexdigest()[:12]


def _latest(directory, live_only: bool = False) -> dict | None:
    best = None
    for p in directory.glob("*.json"):
        if best is None or p.stat().st_mtime > best.stat().st_mtime:
            if live_only and json.loads(p.read_bytes()).get("backfilled"):
                continue
            best = p
    return json.loads(best.read_bytes()) if best else None


def _float_usd(card: dict, prices: dict | None) -> dict[str, float]:
    """On-chain float of each stock token in dollars: supply x Witness price (Uniswap price as fallback)."""
    px = {a.lower(): v.get("price_usdg") for a, v in ((prices or {}).get("items") or {}).items()}
    upx = {a.lower(): v.get("price_usdg") for a, v in (card.get("uniswap", {}).get("prices_usdg") or {}).items()}
    out = {}
    for t in card.get("stock_tokens", {}).get("tokens", []):
        p = px.get(t["address"].lower()) or upx.get(t["address"].lower())
        if p and t.get("total_supply_raw") is not None and t.get("decimals") is not None:
            out[t["symbol"]] = t["total_supply_raw"] / 10 ** t["decimals"] * p
    return out


def build() -> dict:
    card = _latest(settings.SCORECARD_DIR, live_only=True) or {}
    prices = _latest(settings.LEDGER_DIR / "prices") if (settings.LEDGER_DIR / "prices").exists() else None
    audit_path = settings.STATE_DIR / "oracle_audit.json"
    oracle = json.loads(audit_path.read_text()) if audit_path.exists() else {}
    floats = _float_usd(card, prices)
    feeds = (card.get("feeds") or {}).get("tokens") or {}
    depth = (card.get("depth") or {}).get("tokens") or {}
    gaps, risks, unexplained = [], [], []
    stock = {t["symbol"] for t in card.get("stock_tokens", {}).get("tokens", [])}
    flagged_markets = {m["market"]: m["finding"] for m in oracle.get("items", []) if m["finding"] != "ok"}

    def confident(sym: str) -> bool:
        """A float in dollars is only as good as the price under it: require $1k of real depth."""
        return (depth.get(sym) or {}).get("depth_usd_5pct", 0) >= 1_000

    # --- gaps -----------------------------------------------------------------------------------
    for c in (card.get("gap") or {}).get("items", []):
        f = c.get("feasibility") or {}
        d_usd = f.get("liquidation_depth_usd")
        blocked = [n for n, ok in (("no Chainlink feed", f.get("chainlink_feed")), ("under 30 pause-free days", f.get("halt_free_30d")),
                                   ("no liquidation depth", None if d_usd is None else d_usd > 0)) if ok is False]
        flagged = flagged_markets.get(c.get("market"))
        if flagged:
            blocked.append(f"oracle is {flagged.replace('_', ' ')}")
        sym = c.get("subject")
        thin_price = sym in stock and (depth.get(sym) or {}).get("depth_usd_5pct", 0) < 1_000
        if c.get("kind") == "raise_cap_or_add_capacity" and c.get("demand_usd"):
            ev = c.get("evidence") or {}
            gaps.append({"kind": "lending_capacity", "subject": c.get("subject"), "size_usd": c["demand_usd"],
                         "measured": f"market at {ev.get('utilization', 0):.0%} utilisation with ${float(ev.get('borrow_usd') or 0):,.0f} borrowed",
                         "served_by": f"Morpho market {str(c.get('market'))[:10]}, ${float(c.get('coverage_usd') or 0):,.0f} supplied",
                         "needs": "more USDG supplied to this market under session rules", "blocked_by": blocked,
                         "max_cap_by_depth_usd": f.get("max_cap_by_depth_usd"), "source": "scorecard gap table"})
        elif c.get("kind") == "new_listing" and c.get("demand_usd"):
            gaps.append({"kind": "unlisted_collateral", "subject": c.get("subject"), "size_usd": c["demand_usd"],
                         "size_confidence": "low: price rests on a pool that cannot absorb $1k" if thin_price else "ok",
                         "measured": "on-chain float of a stock token with no lending market (an upper bound on borrowable collateral, not borrow demand)",
                         "served_by": "nothing", "needs": "a lending market with a real feed and enough depth to liquidate",
                         "blocked_by": blocked, "max_cap_by_depth_usd": f.get("max_cap_by_depth_usd"), "source": "scorecard gap table"})
    if feeds:
        no_feed = sorted(((s, floats.get(s, 0.0)) for s in stock if not (feeds.get(s) or {}).get("answers_on_chain")), key=lambda x: -x[1])
        if no_feed:
            solid = [(s_, v) for s_, v in no_feed if confident(s_)]
            no_feed = solid + [(s_, v) for s_, v in no_feed if not confident(s_)]
            gaps.append({"kind": "missing_price_feed", "subject": "stock tokens without a Chainlink feed", "size_usd": sum(v for _, v in solid),
                         "measured": f"{len(no_feed)} of {len(stock)} stock tokens have no feed in Chainlink's directory; size is the combined float "
                                     f"of the {len(solid)} whose own price is backed by at least $1k of depth",
                         "served_by": "Uniswap pool prices only", "needs": "a published, manipulation-resistant price for each",
                         "blocked_by": [], "members": [{"symbol": s, "float_usd": round(v)} for s, v in no_feed[:15]], "source": "feeds scanner"})
    if depth:
        thin = sorted(((s, floats.get(s, 0.0), (depth.get(s) or {}).get("depth_usd_5pct", 0)) for s in stock
                       if (depth.get(s) or {}).get("depth_usd_5pct", 0) < 10_000 and floats.get(s, 0) > 0), key=lambda x: -x[1])
        if thin:
            gaps.append({"kind": "missing_depth", "subject": "stock tokens that cannot absorb $10k of selling", "size_usd": sum(v for _, v, _ in thin),
                         "measured": f"{len(thin)} stock tokens lose more than 5% on a $10k sale; size is their combined float",
                         "served_by": "thin Uniswap pools", "needs": "liquidity on the USDG pair",
                         "blocked_by": [], "members": [{"symbol": s, "float_usd": round(v), "depth_usd": d} for s, v, d in thin[:15]], "source": "depth scanner"})

    # --- risks ----------------------------------------------------------------------------------
    for m in oracle.get("items", []):
        if m["finding"] != "ok" and m.get("borrow_usd", 0) >= 10_000:
            risks.append({"kind": f"oracle_{m['finding']}", "subject": f"{m['collateral']}/{m['loan']} {m['market'][:10]}", "size_usd": m["borrow_usd"],
                          "measured": f"${m['borrow_usd']:,.0f} borrowed at {m['lltv']:.1%} loan-to-value; " + (
                              f"priced at a fixed {m.get('oracle_price')}; a live feed takes over only after a {mo['deviation_threshold']:.1%} gap "
                              f"lasts {mo['challenge_timelock_h']:.0f} hours and someone triggers it"
                              if (mo := m.get("meta_oracle")) and m["finding"] == "fixed_with_delayed_backup"
                              else f"oracle price {m.get('oracle_price')}; moved in 7 days: {m.get('moved_7d')}"),
                          "served_by": "existing Morpho market", "needs": "watch; no exposure (draft rail)", "source": "oracle audit"})

    # --- unexplained ---------------------------------------------------------------------------
    try:
        cr = census.report()
        for u in cr["unknown_contracts"]:
            unexplained.append({"kind": "unknown_contract", "subject": u["address"], "size_share": round(u["logs"] / max(cr["logs"], 1), 5),
                                "measured": f"{u['logs']} logs across {u['event_types']} event types in blocks {cr['blocks'][0]}-{cr['blocks'][1]}",
                                "source": "census"})
        for u in cr["unknown_events"][:10]:
            unexplained.append({"kind": "unnamed_event", "subject": u["topic0"], "size_share": round(u["logs"] / max(cr["logs"], 1), 5),
                                "measured": f"{u['logs']} logs from {u['contracts']} contracts", "source": "census"})
        coverage = {"by_contract_kind": cr["coverage_by_contract_kind"], "by_named_event": cr["coverage_by_named_event"], "logs": cr["logs"]}
    except Exception as e:
        coverage = {"error": str(e)[:120]}
    try:
        ir = intent.report(min_calls=30, top=15)
        for x in ir["items"]:
            if x["excess_failures"] >= 5 and x["failure_rate"] >= 2 * ir["base_failure_rate"]:
                unexplained.append({"kind": "failing_intent", "subject": f"{x['contract']}:{x['selector']}",
                                    "size_share": round(x["failed"] / max(ir["calls_sampled"], 1), 5),
                                    "measured": f"{x['function']} fails {x['failure_rate']:.0%} of {x['calls']} sampled calls "
                                                f"(chain base rate {ir['base_failure_rate']:.0%}) on a {x['contract_kind']} contract",
                                    "source": "intent sampler"})
    except Exception:
        pass

    now = int(time.time())
    hist = json.loads(HISTORY.read_text()) if HISTORY.exists() else {}
    for queue, key in ((gaps, "size_usd"), (risks, "size_usd"), (unexplained, "size_share")):
        queue.sort(key=lambda x: (str(x.get("size_confidence", "ok")).startswith("low"), -(x.get(key) or 0)))
        for rank, it in enumerate(queue, start=1):
            it["id"] = _id(it["kind"], it["subject"])
            h = hist.setdefault(it["id"], {"first_seen": now, "sizes": []})
            h["last_seen"] = now
            h["sizes"] = (h["sizes"] + [[now, it.get(key)]])[-60:]
            it["rank"], it["days_seen"] = rank, round((now - h["first_seen"]) / DAY, 2)
    HISTORY.parent.mkdir(exist_ok=True)
    HISTORY.write_text(json.dumps(hist))
    return {"kind": "opportunities", "ts": now, "chain_id": settings.CHAIN["chain"]["id"],
            "inputs": {"scorecard_ts": card.get("ts"), "prices_block": (prices or {}).get("block"), "oracle_audit_block": oracle.get("block")},
            "coverage": coverage,
            "totals": {"gaps": len(gaps), "gap_usd": round(sum(g["size_usd"] for g in gaps if g["kind"] in ("lending_capacity",))),
                       "risks": len(risks), "risk_usd": round(sum(r["size_usd"] for r in risks)), "unexplained": len(unexplained)},
            "gaps": gaps[:60], "risks": risks[:40], "unexplained": unexplained[:40]}


def publish(doc: dict) -> str:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    h = ledger.sha(doc)
    (OUT_DIR / f"{time.strftime('%Y-%m-%d', time.gmtime(doc['ts']))}_{h[:16]}.json").write_bytes(ledger.canonical(doc))
    return h


def main() -> None:
    doc = build()
    h = publish(doc)
    t = doc["totals"]
    print(f"opportunity map {h[:16]}: {t['gaps']} gaps, {t['risks']} risks (${t['risk_usd']:,} borrowed), {t['unexplained']} unexplained; coverage {doc['coverage']}")
    for name in ("gaps", "risks", "unexplained"):
        print(f"{name}:")
        for it in doc[name][:7]:
            size = f"${it['size_usd']:>13,.0f}" if "size_usd" in it else f"{it['size_share']:>7.2%} of activity"
            print(f"  {it['rank']:2d}. {size}  {it['kind']:20} {str(it['subject'])[:44]:44} {('blocked: ' + ', '.join(it['blocked_by'])) if it.get('blocked_by') else ''}")


if __name__ == "__main__":
    main()
