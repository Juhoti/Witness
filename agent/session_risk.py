"""Session risk: how far a stock can move while its market is closed, and what loan-to-value survives it.

A stock token trades on chain around the clock, but its Chainlink feed follows a market that shuts at
night, at weekends and during halts. A lending market cannot liquidate while the price it reads is
frozen, so the question for each ticker is how large a fall has ever arrived in one closed stretch.

For every stock-token ticker this sets side by side:
  the underlying share's worst closed-market fall in five years of daily bars, and its 99th percentile
  the days since its last exchange halt (LULD pause or news-pending), from the US halt record
  the highest standard Morpho loan-to-value tier at which a position opened at the limit would still
  be liquidated without bad debt after that worst fall, liquidation incentive included
  every existing Morpho market for that collateral whose loan-to-value is above that tier

The gap figures are priors from the share market, not outcomes on this chain, and are labelled so.
The past does not bound the future: a tier that survived five years is a floor on caution, not a
guarantee.

    python -m agent.session_risk      # publishes ledger/session_risk/<day>_<hash>.json
"""
from __future__ import annotations
import csv
import io
import json
import time
from datetime import date
import httpx
from . import ledger, opportunity, settings
from .priors import HALTS_URL, UA

OUT_DIR = settings.LEDGER_DIR / "session_risk"
TIERS = (0.385, 0.625, 0.77, 0.86, 0.915, 0.945, 0.965)   # the loan-to-value tiers Morpho enables
GAPS = settings.ROOT / "vault" / "priors" / "stock_gaps.json"


def incentive(lltv: float) -> float:
    """Morpho Blue's liquidation incentive factor for a market: min(1.15, 1 / (1 - 0.3 * (1 - lltv)))."""
    return min(1.15, 1 / (1 - 0.3 * (1 - lltv)))


def survives(lltv: float, fall: float) -> bool:
    """Can a position borrowed up to lltv be liquidated in full, incentive paid, after a fall of `fall`?"""
    return (1 - fall) >= lltv * incentive(lltv)


def max_tier(fall: float) -> float | None:
    ok = [t for t in TIERS if survives(t, fall)]
    return max(ok) if ok else None


def last_halts(symbols: set[str]) -> dict[str, dict]:
    r = httpx.get(HALTS_URL, headers=UA, timeout=120)
    r.raise_for_status()
    out: dict[str, dict] = {}
    today = date.today()
    for row in csv.DictReader(io.StringIO(r.text)):
        s = row.get("Symbol")
        if s not in symbols or not row.get("Halt Date"):
            continue
        reason = (row.get("Reason") or "").strip().lower()
        h = out.setdefault(s, {"last_halt": None, "last_halt_reason": None, "last_news_halt": None, "halts_12m": 0})
        d = row["Halt Date"]
        if h["last_halt"] is None or d > h["last_halt"]:
            h["last_halt"], h["last_halt_reason"] = d, reason
        if "news" in reason and (h["last_news_halt"] is None or d > h["last_news_halt"]):
            h["last_news_halt"] = d
        if (today - date.fromisoformat(d)).days <= 365:
            h["halts_12m"] += 1
    for h in out.values():
        h["days_since_halt"] = (today - date.fromisoformat(h["last_halt"])).days
    return out


def build() -> dict:
    card = opportunity._latest(settings.SCORECARD_DIR, live_only=True) or {}
    symbols = {t["symbol"] for t in card.get("stock_tokens", {}).get("tokens", [])}
    gaps = json.loads(GAPS.read_text()) if GAPS.exists() else {}
    per = gaps.get("per_ticker", {})
    try:
        halts, halts_ok = last_halts(symbols), True
    except Exception:
        halts, halts_ok = {}, False
    markets: dict[str, list] = {}
    for m in card.get("morpho", {}).get("items", []):
        if m.get("collateral") in symbols and m.get("lltv"):
            markets.setdefault(m["collateral"], []).append({"market": m["key"], "lltv": int(m["lltv"]) / 1e18,
                                                            "borrow_usd": m.get("borrow_usd") or 0, "supply_usd": m.get("supply_usd") or 0})
    items, exposed = {}, []
    for s in sorted(symbols):
        g = per.get(s)
        h = halts.get(s)
        row = {"exchange_halt_free_30d": (h is None or h["days_since_halt"] >= 30) if halts_ok else None,
               "days_since_exchange_halt": h["days_since_halt"] if h else None, "last_halt_reason": h and h["last_halt_reason"],
               "halts_12m": h["halts_12m"] if h else (0 if halts_ok else None)}
        if g:
            worst = abs(min(g["weekend"]["worst_down"] or 0, g["overnight"]["worst_down"] or 0, 0))
            p99 = max(g["weekend"]["p99_abs"] or 0, g["overnight"]["p99_abs"] or 0)
            tier = max_tier(worst)
            row.update({"bars": g["bars"], "worst_closed_fall": round(worst, 4), "p99_closed_move": round(p99, 4),
                        "max_tier_surviving_worst": tier, "max_tier_surviving_p99": max_tier(p99)})
            for m in markets.get(s, []):
                if tier is None or m["lltv"] > tier:
                    exposed.append({"collateral": s, **m, "worst_closed_fall": round(worst, 4), "max_tier_surviving_worst": tier})
        row["markets"] = markets.get(s, [])
        items[s] = row
    exposed.sort(key=lambda x: -x["borrow_usd"])
    with_gap = [v for v in items.values() if "worst_closed_fall" in v]
    tiers = {}
    for v in with_gap:
        k = str(v["max_tier_surviving_worst"])
        tiers[k] = tiers.get(k, 0) + 1
    return {"kind": "session_risk", "ts": int(time.time()), "chain_id": settings.CHAIN["chain"]["id"],
            "basis": "share-market priors: five years of daily bars (vault/priors/stock_gaps) and the US halt record; not outcomes on this chain",
            "tickers": len(items), "with_gap_history": len(with_gap), "halt_record_fetched": halts_ok,
            "exchange_halt_within_30d": sum(1 for v in items.values() if v["exchange_halt_free_30d"] is False),
            "tickers_by_max_surviving_tier": dict(sorted(tiers.items())),
            "markets_above_surviving_tier": len(exposed), "borrowed_above_surviving_tier_usd": round(sum(x["borrow_usd"] for x in exposed)),
            "exposed_markets": exposed[:40], "items": items}


def publish(doc: dict) -> str:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    h = ledger.sha(doc)
    (OUT_DIR / f"{time.strftime('%Y-%m-%d', time.gmtime(doc['ts']))}_{h[:16]}.json").write_bytes(ledger.canonical(doc))
    return h


if __name__ == "__main__":
    d = build()
    h = publish(d)
    print(f"session risk {h[:16]}: {d['with_gap_history']} of {d['tickers']} tickers with gap history; exchange halt in last 30 days: {d['exchange_halt_within_30d']}")
    print("  tickers by highest tier surviving their worst closed-market fall:", d["tickers_by_max_surviving_tier"])
    print(f"  existing markets above that tier: {d['markets_above_surviving_tier']}, with ${d['borrowed_above_surviving_tier_usd']:,} borrowed")
    for x in d["exposed_markets"][:8]:
        print(f"    {x['collateral']:6} lltv {x['lltv']:.3f}  borrowed ${x['borrow_usd']:>10,.0f}  worst closed fall {x['worst_closed_fall']:.1%}  survives up to {x['max_tier_surviving_worst']}")
