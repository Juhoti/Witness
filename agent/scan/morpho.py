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
      state { supplyAssetsUsd borrowAssetsUsd utilization supplyApy borrowApy }
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
            "utilization": st.get("utilization"),
            "borrow_apy": st.get("borrowApy"),
            "oracle": (m.get("oracle") or {}).get("address"),
        })
    pinned = [m for m in out if (m["utilization"] or 0) >= 0.9]
    return {"markets": len(out), "items": out, "pinned_above_90pct": len(pinned)}
