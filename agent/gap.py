"""The gap test: demand - coverage, with feasibility and risk flags, per candidate.

Gate 0 emits candidates and ZERO proposals (rung r5). Gate 2 hands candidates to the proposer.
Every number here must be reproducible from the scorecard it came from; the scorecard hash is
carried into any certificate that cites it.
"""
from __future__ import annotations


def _usd(x) -> float:
    try:
        return float(x or 0)
    except (TypeError, ValueError):
        return 0.0


def _supply_usd(token: dict, price_usdg) -> float | None:
    """On-chain float in USD: totalSupply / 10**decimals * price. None when any input is missing."""
    try:
        if price_usdg is None or token.get("total_supply_raw") is None or token.get("decimals") is None:
            return None
        return float(token["total_supply_raw"]) / 10 ** int(token["decimals"]) * float(price_usdg)
    except (TypeError, ValueError, OverflowError):
        return None


def build(scorecard: dict) -> dict:
    candidates: list[dict] = []
    morpho = scorecard.get("morpho", {})
    tokens = {t["symbol"]: t for t in scorecard.get("stock_tokens", {}).get("tokens", []) if t.get("symbol")}

    # Signal 1: markets pinned at high utilisation = unmet borrow demand, sized in USD.
    for m in morpho.get("items", []):
        u = _usd(m.get("utilization"))
        if u >= 0.9:
            candidates.append({
                "kind": "raise_cap_or_add_capacity",
                "subject": m.get("collateral"),
                "market": m.get("key"),
                "demand_usd": _usd(m.get("borrow_usd")) * (u - 0.8),  # rough: excess above comfortable util
                "coverage_usd": _usd(m.get("supply_usd")),
                "evidence": {"utilization": u, "borrow_usd": m.get("borrow_usd")},
            })

    # Signal 2: stock tokens with no lending market at all = idle collateral.
    # Demand = on-chain float valued at the token's Uniswap USDG price, minus anything already posted
    # as Morpho collateral (zero for an unlisted token). A token with no USDG pool has no price and
    # keeps demand_usd = null rather than a guess.
    listed = {m.get("collateral") for m in morpho.get("items", [])}
    prices = scorecard.get("uniswap", {}).get("prices_usdg", {})
    for sym, t in tokens.items():
        if sym not in listed:
            px = prices.get(t["address"]) or {}
            supply_usd = _supply_usd(t, px.get("price_usdg"))
            candidates.append({
                "kind": "new_listing",
                "subject": sym,
                "address": t["address"],
                "demand_usd": supply_usd,
                "demand_basis": "on-chain supply x uniswap USDG price; nothing posted as collateral yet" if supply_usd is not None else None,
                "coverage_usd": 0.0,
                "evidence": {"paused": t.get("paused"), "multiplier_raw": t.get("multiplier_raw"),
                             "price_usdg": px.get("price_usdg"), "price_pool": px.get("pool"),
                             "price_stale": px.get("stale"), "price_as_of_block": px.get("as_of_block"),
                             "total_supply_raw": t.get("total_supply_raw")},
            })

    # Signal 3: reverted intent against known venues.
    for c in scorecard.get("reverts", {}).get("items", [])[:20]:
        candidates.append({
            "kind": "reverted_intent",
            "subject": c.get("to"),
            "reason": c.get("reason"),
            "demand_usd": None,
            "coverage_usd": None,
            "evidence": {"count": c.get("count"), "example": c.get("example")},
        })

    # Feasibility / risk flags. These are rails from northstar.md, applied as data checks.
    for c in candidates:
        t = tokens.get(c.get("subject")) if c.get("kind") == "new_listing" else None
        c["feasibility"] = {
            "chainlink_feed": bool(t and t.get("chainlink_feed")),
            "halt_free_30d": None,          # needs halt history (Gate 1)
            "liquidation_depth_ok": None,   # needs uniswap scanner
        }
        c["risk"] = {"needs_price_when_market_closed": c.get("kind") in ("new_listing", "raise_cap_or_add_capacity")}
        c["proposable"] = False  # Gate 0: nothing is proposable; the proposer is off

    candidates.sort(key=lambda c: -(c.get("demand_usd") or 0))
    return {"candidates": len(candidates), "proposals": 0, "items": candidates[:100]}
