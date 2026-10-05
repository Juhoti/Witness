"""Other lending vaults on the chain: who a Witness vault would be measured against.

The north star compares depositor yield with the best comparable vault. This records every Morpho
vault on the chain with its deposits, idle share, net yield and fees, from Morpho's public API. Names
and symbols are the vaults' own text, kept as data.

    python -m agent.vaults       # publishes ledger/vaults/<day>_<hash>.json
"""
from __future__ import annotations
import time
import httpx
from . import ledger, settings

OUT_DIR = settings.LEDGER_DIR / "vaults"
QUERY = """query($chainId:Int!){ vaultV2s(where:{chainId_in:[$chainId]}, first:200){ items{
  address name symbol asset{symbol address} totalAssetsUsd idleAssetsUsd netApy performanceFee managementFee listed creationTimestamp } } }"""


def build() -> dict:
    url = settings.CHAIN["morpho"].get("graphql", "https://blue-api.morpho.org/graphql")
    r = httpx.post(url, json={"query": QUERY, "variables": {"chainId": settings.CHAIN["chain"]["id"]}}, timeout=60)
    r.raise_for_status()
    j = r.json()
    if j.get("errors"):
        raise RuntimeError(str(j["errors"])[:300])
    items = sorted(j["data"]["vaultV2s"]["items"], key=lambda v: -(v.get("totalAssetsUsd") or 0))
    total = sum(v.get("totalAssetsUsd") or 0 for v in items) or 1
    rows = [{"address": v["address"], "name": (v.get("name") or "")[:80], "asset": (v.get("asset") or {}).get("symbol"),
             "deposits_usd": v.get("totalAssetsUsd") or 0, "idle_usd": v.get("idleAssetsUsd") or 0, "net_apy": v.get("netApy"),
             "performance_fee": v.get("performanceFee"), "management_fee": v.get("managementFee"), "listed_by_morpho": bool(v.get("listed")),
             "share_of_deposits": round((v.get("totalAssetsUsd") or 0) / total, 5), "created": v.get("creationTimestamp")} for v in items]
    funded = [x for x in rows if x["deposits_usd"] >= 1000]
    usdg = [x for x in funded if x["asset"] == "USDG" and x["net_apy"] is not None]
    return {"kind": "vaults", "ts": int(time.time()), "chain_id": settings.CHAIN["chain"]["id"], "source": url,
            "vaults": len(rows), "with_over_1k": len(funded), "total_deposits_usd": round(total),
            "largest_share": rows[0]["share_of_deposits"] if rows else None,
            "best_usdg_net_apy": max((x["net_apy"] for x in usdg), default=None),
            "largest_usdg_net_apy": usdg[0]["net_apy"] if usdg else None, "items": rows}


def publish(doc: dict) -> str:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    h = ledger.sha(doc)
    (OUT_DIR / f"{time.strftime('%Y-%m-%d', time.gmtime(doc['ts']))}_{h[:16]}.json").write_bytes(ledger.canonical(doc))
    return h


if __name__ == "__main__":
    d = build()
    print(f"vaults {publish(d)[:16]}: {d['vaults']} vaults, {d['with_over_1k']} with over $1k, ${d['total_deposits_usd']:,} deposited; "
          f"largest holds {d['largest_share']:.1%}; best USDG net yield {d['best_usdg_net_apy']}")
