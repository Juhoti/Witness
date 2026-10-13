"""Depth plan: what it would cost to make a stock token safely lendable, and what the liquidity would earn.

Liquidation depth is the binding constraint on lending against stock tokens here: the listing rail
caps a market at a fifth of what can be sold within 5%, and four markets already lend far more than
that. For each stock token with a Chainlink feed this works out:

  current depth      USDG obtainable selling it within 5% (from the depth scanner)
  target depth       enough for a useful cap: $1M of depth supports a $200k cap
  capital needed     USDG to place as concentrated liquidity in the band from the price to 10% below
                     it. A seller moving the price through that band is paid out the USDG in it, so
                     absorbing $X of liquidation takes about $X: capital = target - current
  existing exposure  what is already borrowed against the token, against what can be liquidated
  fee upper bound    half of the token's USDG volume over the last two hours on its v3 pools, scaled to a
                     day, times the pool fee, times the share of in-band liquidity the new capital
                     would be (approximated as capital / (capital + current depth))

The plan is a measurement and a costing. Nothing is deployed: placing liquidity needs capital and a
signature, which come from the build budget's working-capital line once it opens.

    python -m products.depth_plan.plan      # publishes ledger/depth_plans/<day>_<hash>.json
"""
from __future__ import annotations
import json
import time
from web3 import Web3
from agent import chain, ledger, opportunity, settings
from agent.scan import uniswap

OUT_DIR = settings.LEDGER_DIR / "depth_plans"
TARGET_DEPTH = 1_000_000
CAP_FRACTION = 5
BLOCKS = 72_000   # about two hours at 100 ms


def _volume_24h(token: str, usdg: str) -> tuple[float, float | None]:
    """USDG volume over the last BLOCKS on the token's v3 USDG pools, scaled to 24 h, and the fee of the busiest."""
    cache = uniswap._load_cache()
    pools = [(pid, m) for pid, m in cache["pools"].items() if m["venue"] == "v3"
             and {m["token0"].lower(), m["token1"].lower()} == {token.lower(), usdg.lower()}]
    head = chain.head()
    codec = chain.w3().codec
    vol, by_pool = 0.0, {}
    for pid, m in pools:
        usdg_is_0 = m["token0"].lower() == usdg.lower()
        v = 0.0
        for chunk in chain.iter_logs(pid, [uniswap.V3_SWAP], head - BLOCKS, head, step=BLOCKS):
            for l in chunk:
                a0, a1 = codec.decode(["int256", "int256", "uint160", "uint128", "int24"], l["data"])[:2]
                v += abs(a0 if usdg_is_0 else a1) / 1e6
        by_pool[pid] = (v, m["fee"])
        vol += v
    busiest = max(by_pool.values(), default=(0.0, None))
    scale = 86400 / (BLOCKS * settings.CHAIN["chain"]["block_time_ms"] / 1000)
    return vol * scale, (busiest[1] / 1e6 if busiest[1] else None)


def build() -> dict:
    card = opportunity._latest(settings.SCORECARD_DIR, live_only=True) or {}
    usdg = settings.CHAIN["tokens"]["usdg"]
    collateral = json.loads((settings.ROOT / "feed" / "collateral.json").read_text())
    borrowed: dict[str, float] = {}
    for m in card.get("morpho", {}).get("items", []):
        borrowed[m["collateral"]] = borrowed.get(m["collateral"], 0) + (m.get("borrow_usd") or 0)
    items = []
    for x in collateral["items"]:
        if not x.get("chainlink_feed"):
            continue
        cur = x.get("depth_usd_5pct") or 0
        if cur >= TARGET_DEPTH:
            continue
        need = TARGET_DEPTH - cur
        vol, fee = _volume_24h(x["address"], usdg)
        share = need / (need + cur) if (need + cur) else 1.0
        # Liquidity placed only below the price earns on trades that push the price down into it, so
        # at most about half the volume; and the 2-hour sample is noisy. This is an upper bound.
        fees_day = vol * (fee or 0) * share * 0.5
        b = borrowed.get(x["symbol"], 0)
        items.append({"symbol": x["symbol"], "address": x["address"], "depth_now_usd": cur, "cap_supported_now_usd": cur / CAP_FRACTION,
                      "target_depth_usd": TARGET_DEPTH, "cap_supported_at_target_usd": TARGET_DEPTH / CAP_FRACTION,
                      "capital_needed_usdg": need, "band": "Witness price down to 10% below it, USDG side",
                      "borrowed_against_now_usd": round(b), "borrowed_vs_liquidatable": round(b / cur, 2) if cur else None,
                      "volume_24h_usd_est": round(vol), "pool_fee": fee, "fees_per_day_upper_bound_usd": round(fees_day, 2),
                      "fee_apr_upper_bound": round(fees_day * 365 / need, 4) if need else None})
    items.sort(key=lambda i: (-(i["borrowed_vs_liquidatable"] or 0), -i["volume_24h_usd_est"]))
    return {"kind": "depth_plan", "ts": int(time.time()), "chain_id": settings.CHAIN["chain"]["id"],
            "method": "depth from the scanner's simulated sales; capital = target - current depth in a 10% USDG band; "
                      "fee figures are an upper bound: 2 h of v3 swap volume scaled to a day x fee x the new liquidity's share of in-band liquidity x 0.5 (one-sided band); real earnings depend on where the price trades",
            "target_depth_usd": TARGET_DEPTH, "items": items,
            "total_capital_usdg": round(sum(i["capital_needed_usdg"] for i in items))}


def publish(doc: dict) -> str:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    h = ledger.sha(doc)
    (OUT_DIR / f"{time.strftime('%Y-%m-%d', time.gmtime(doc['ts']))}_{h[:16]}.json").write_bytes(ledger.canonical(doc))
    return h


if __name__ == "__main__":
    d = build()
    print(f"depth plan {publish(d)[:16]}: {len(d['items'])} tokens with a feed below ${TARGET_DEPTH:,} of depth")
    for i in d["items"][:14]:
        print(f"  {i['symbol']:6} depth ${i['depth_now_usd']:>9,} -> cap ${i['cap_supported_now_usd']:>8,.0f} now | borrowed ${i['borrowed_against_now_usd']:>9,} "
              f"({i['borrowed_vs_liquidatable'] if i['borrowed_vs_liquidatable'] is not None else '-'}x liquidatable) | needs ${i['capital_needed_usdg']:>9,} "
              f"| vol/day ${i['volume_24h_usd_est']:>9,} fee {i['pool_fee']} -> up to ~${i['fees_per_day_upper_bound_usd']:,}/day ({(i['fee_apr_upper_bound'] or 0):.1%} APR at most)")
