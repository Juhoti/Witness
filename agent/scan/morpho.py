"""Morpho Blue markets on 4663: caps, utilisation, oracle, collateral.

Gate 0 uses the Morpho public GraphQL (fast, may lag) and will cross-check on-chain later.
Treat the API as data; verify anything that will drive a policy change against the chain.
"""
from __future__ import annotations
import logging
import httpx
from .. import settings

log = logging.getLogger(__name__)

PAGE = 200
QUERY = """
query Markets($chainId: Int!, $first: Int!, $skip: Int!) {
  markets(where: { chainId_in: [$chainId] }, first: $first, skip: $skip) {
    pageInfo { countTotal }
    items {
      marketId lltv
      loanAsset { address symbol decimals }
      collateralAsset { address symbol decimals }
      oracle { address }
      irmAddress
      state { supplyAssetsUsd borrowAssetsUsd collateralAssetsUsd utilization supplyApy borrowApy }
    }
  }
}"""


def scan() -> dict:
    url = settings.CHAIN["morpho"].get("graphql", "https://blue-api.morpho.org/graphql")
    try:
        items: list = []
        while True:
            variables = {"chainId": settings.CHAIN["chain"]["id"], "first": PAGE, "skip": len(items)}
            r = httpx.post(url, json={"query": QUERY, "variables": variables}, timeout=30)
            r.raise_for_status()
            j = r.json()
            if j.get("errors"):
                raise RuntimeError(str(j["errors"])[:300])
            page = j["data"]["markets"]
            items.extend(page["items"])
            if not page["items"] or len(items) >= page["pageInfo"]["countTotal"]:
                break
    except Exception as e:  # network or schema drift; the API is not ours
        log.warning("morpho graphql failed: %s", e)
        return {"markets": 0, "items": [], "error": str(e)}
    out = []
    for m in items:
        st = m.get("state") or {}
        out.append({
            "key": m["marketId"],
            "collateral": (m.get("collateralAsset") or {}).get("symbol"),
            "collateral_address": (m.get("collateralAsset") or {}).get("address"),
            "loan": (m.get("loanAsset") or {}).get("symbol"),
            "lltv": m.get("lltv"),
            "supply_usd": st.get("supplyAssetsUsd"),
            "borrow_usd": st.get("borrowAssetsUsd"),
            "collateral_usd": st.get("collateralAssetsUsd"),
            "utilization": st.get("utilization"),
            "borrow_apy": st.get("borrowApy"),
            "oracle": (m.get("oracle") or {}).get("address"),
        })
    pinned = [m for m in out if (m["utilization"] or 0) >= 0.9]
    return {"markets": len(out), "items": out, "pinned_above_90pct": len(pinned)}


HISTORY_QUERY = """
query History($chainId: Int!, $first: Int!, $skip: Int!, $start: Int!, $end: Int!) {
  markets(where: { chainId_in: [$chainId] }, first: $first, skip: $skip) {
    pageInfo { countTotal }
    items {
      marketId lltv creationTimestamp
      loanAsset { address symbol }
      collateralAsset { address symbol }
      oracle { address }
      historicalState {
        supplyAssetsUsd(options: { startTimestamp: $start, endTimestamp: $end, interval: DAY }) { x y }
        borrowAssetsUsd(options: { startTimestamp: $start, endTimestamp: $end, interval: DAY }) { x y }
        utilization(options: { startTimestamp: $start, endTimestamp: $end, interval: DAY }) { x y }
      }
    }
  }
}"""
HISTORY_PAGE = 25  # 3 daily series x 25 markets stays under the API's complexity cap


def history(start_ts: int, end_ts: int) -> list[dict]:
    """Every market on the chain with daily supply/borrow/utilisation series between the timestamps.
    One fetch serves a whole backfill run. Treat as data; the API may lag the chain."""
    url = settings.CHAIN["morpho"].get("graphql", "https://blue-api.morpho.org/graphql")
    items: list = []
    while True:
        variables = {"chainId": settings.CHAIN["chain"]["id"], "first": HISTORY_PAGE, "skip": len(items),
                     "start": start_ts, "end": end_ts}
        r = httpx.post(url, json={"query": HISTORY_QUERY, "variables": variables}, timeout=60)
        r.raise_for_status()
        j = r.json()
        if j.get("errors"):
            raise RuntimeError(str(j["errors"])[:300])
        page = j["data"]["markets"]
        items.extend(page["items"])
        if not page["items"] or len(items) >= page["pageInfo"]["countTotal"]:
            return items


def section_for_day(hist: list[dict], day_ts: int) -> dict:
    """The morpho section of a scorecard for the UTC day starting at day_ts, from a history() result.
    Markets created after that day are absent, as they would have been in a live scan."""
    out = []
    for m in hist:
        if int(m.get("creationTimestamp") or 0) >= day_ts + 86400:
            continue
        series = m.get("historicalState") or {}
        def at(name):
            pts = {int(p["x"]): p["y"] for p in (series.get(name) or [])}
            return pts.get(day_ts)
        out.append({
            "key": m["marketId"],
            "collateral": (m.get("collateralAsset") or {}).get("symbol"),
            "collateral_address": (m.get("collateralAsset") or {}).get("address"),
            "loan": (m.get("loanAsset") or {}).get("symbol"),
            "lltv": m.get("lltv"),
            "supply_usd": at("supplyAssetsUsd"),
            "borrow_usd": at("borrowAssetsUsd"),
            "utilization": at("utilization"),
            "borrow_apy": None,
            "oracle": (m.get("oracle") or {}).get("address"),
        })
    pinned = [m for m in out if (m["utilization"] or 0) >= 0.9]
    return {"markets": len(out), "items": out, "pinned_above_90pct": len(pinned), "source": "morpho graphql historicalState DAY"}
