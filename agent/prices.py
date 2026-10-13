"""Witness reference prices: what each asset is worth in USDG, measured from the chain.

For every stock token and every Morpho collateral or loan asset, read the current price in each of
its Uniswap USDG pools (v3 across the four fee tiers, v4 from the pools already known) straight from
pool state, and take the median weighted by each pool's in-range liquidity. Then set that beside the
asset's Chainlink feed where one exists.

Each price is a measurement and says how it was made: the block, the pools and their weights, how
far the pools disagree, how far the result sits from the feed, and the feed's age. A price from one
thin pool is published with that fact attached rather than withheld. Nothing here is an oracle:
there is no manipulation resistance beyond the liquidity weighting and no promise of availability.

    python -m agent.prices            # measure, write ledger/prices/<day>_<hash>.json, print a summary
"""
from __future__ import annotations
import json
import statistics
import logging
import time
from web3 import Web3
from . import chain, ledger, settings
from .scan import feeds as feeds_scan, uniswap

log = logging.getLogger("prices")
OUT_DIR = settings.LEDGER_DIR / "prices"
FEES = (100, 500, 3000, 10000)
Q96 = 2 ** 96
METHOD = ("spot: liquidity-weighted median of Uniswap v3+v4 USDG pool prices at one block; robust: 30-minute Uniswap v3 TWAP, "
          "else the median of Witness's last six hourly spot samples; 24 h median as context; Chainlink feed shown beside both")


def _sel(sig: str) -> bytes:
    return Web3.keccak(text=sig)[:4]


def weighted_median(pairs: list[tuple[float, float]]) -> float | None:
    """Median of values weighted by the second element. pairs = [(value, weight)]."""
    pairs = sorted((v, w) for v, w in pairs if w > 0)
    total = sum(w for _, w in pairs)
    if not pairs or total <= 0:
        return None
    acc = 0.0
    for v, w in pairs:
        acc += w
        if acc >= total / 2:
            return v
    return pairs[-1][0]


def usdg_per_token(sqrt_price_x96: int, token_is_0: bool, dec_token: int, dec_usdg: int = 6) -> float:
    """Spot price from a pool's sqrtPriceX96. token1 per token0 is (sp/2^96)^2, adjusted for decimals."""
    d0, d1 = (dec_token, dec_usdg) if token_is_0 else (dec_usdg, dec_token)
    one_per_zero = (sqrt_price_x96 / Q96) ** 2 * 10 ** (d0 - d1)
    return one_per_zero if token_is_0 else 1 / one_per_zero


def _assets(card: dict) -> dict[str, dict]:
    """address -> {symbol, decimals, why}. Stock tokens from the latest scorecard, plus Morpho assets."""
    out = {}
    for t in card.get("stock_tokens", {}).get("tokens", []):
        if t.get("decimals") is not None:
            out[Web3.to_checksum_address(t["address"])] = {"symbol": t["symbol"], "decimals": t["decimals"], "kind": "stock_token"}
    cache = uniswap._load_cache().get("tokens", {})
    for m in card.get("morpho", {}).get("items", []):
        a = m.get("collateral_address")
        if a:
            a = Web3.to_checksum_address(a)
            meta = cache.get(a) or {}
            if a not in out and meta.get("decimals") is not None:
                out[a] = {"symbol": m.get("collateral") or meta.get("symbol"), "decimals": meta["decimals"], "kind": "morpho_collateral"}
    return out


TWAP_S = 1800
MIN_SAMPLES = 12


def tick_to_price(avg_tick: float, token_is_0: bool, dec_token: int, dec_usdg: int = 6) -> float:
    """USDG per token from an average tick. 1.0001**tick is token1 per token0 in raw units."""
    d0, d1 = (dec_token, dec_usdg) if token_is_0 else (dec_usdg, dec_token)
    one_per_zero = 1.0001 ** avg_tick * 10 ** (d0 - d1)
    return one_per_zero if token_is_0 else 1 / one_per_zero


def twap_v3(pools: dict, assets: dict, usdg: str, block: int) -> dict:
    """30-minute time-weighted price from each asset's deepest v3 USDG pool whose oracle keeps enough
    history. A pool that cannot answer observe() for the window is skipped, not guessed."""
    deepest = {}
    for a, ps in pools.items():
        v3 = sorted((p for p in ps if p["venue"] == "v3"), key=lambda p: -p["liquidity"])
        if v3:
            deepest[a] = v3[0]
    codec = chain.w3().codec
    sel = _sel("observe(uint32[])")
    order = sorted(deepest)
    raws = chain.multicall([(deepest[a]["pool"], sel + codec.encode(["uint32[]"], [[TWAP_S, 0]])) for a in order], block=block)
    out = {}
    for a, r in zip(order, raws):
        if not r:
            continue
        try:
            ticks, _ = codec.decode(["int56[]", "uint160[]"], r)
        except Exception:
            continue
        avg = (ticks[1] - ticks[0]) / TWAP_S
        out[a] = {"price": tick_to_price(avg, a.lower() < usdg.lower(), assets[a]["decimals"]), "pool": deepest[a]["pool"],
                  "window_s": TWAP_S, "pool_liquidity": deepest[a]["liquidity"]}
    return out


def sample_history(hours: int = 24) -> dict[str, list[float]]:
    """Witness's own hourly spot prices over the last `hours`, from the published price sets."""
    files = sorted(OUT_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)[-hours:] if OUT_DIR.exists() else []
    cutoff = time.time() - hours * 3600
    hist: dict[str, list[float]] = {}
    for f in files:
        try:
            d = json.loads(f.read_bytes())
        except ValueError:
            continue
        if d.get("ts", 0) < cutoff:
            continue
        for addr, it in (d.get("items") or {}).items():
            if it.get("price_usdg"):
                hist.setdefault(addr.lower(), []).append(it["price_usdg"])
    return hist


def measure(card: dict) -> dict:
    usdg = Web3.to_checksum_address(settings.CHAIN["tokens"]["usdg"])
    factory, state_view = settings.CHAIN["uniswap"]["v3_factory"], settings.CHAIN["uniswap"]["v4_state_view"]
    assets = _assets(card)
    assets.pop(usdg, None)
    addrs = sorted(assets)
    codec = chain.w3().codec
    block = chain._retry(lambda: chain.w3().eth.block_number)
    # v3 pools: factory lookup, then slot0 + liquidity
    raws = chain.multicall([(factory, _sel("getPool(address,address,uint24)") + codec.encode(["address", "address", "uint24"], [a, usdg, f]))
                            for a in addrs for f in FEES], block=block)
    v3 = []
    for i, a in enumerate(addrs):
        for j, f in enumerate(FEES):
            r = raws[i * 4 + j]
            if r and int.from_bytes(r[-20:], "big"):
                v3.append((a, f, Web3.to_checksum_address(r[-20:])))
    st3 = chain.multicall([(p, _sel(s)) for _, _, p in v3 for s in ("slot0()", "liquidity()")], block=block)
    # v4 pools: from the scanner's cache, state through StateView
    v4 = [(a, m) for a, ms in uniswap.v4_usdg_pools(addrs).items() for m in ms]
    st4 = chain.multicall([(state_view, _sel(s) + bytes.fromhex(m["id"][2:])) for _, m in v4 for s in ("getSlot0(bytes32)", "getLiquidity(bytes32)")], block=block)
    pools: dict[str, list] = {}
    for i, (a, f, p) in enumerate(v3):
        s0, liq = st3[i * 2], chain.decode_uint(st3[i * 2 + 1])
        sp = chain.decode_uint(s0)
        if sp and liq:
            pools.setdefault(a, []).append({"venue": "v3", "pool": p, "fee": f, "liquidity": liq,
                                            "price": usdg_per_token(sp, a.lower() < usdg.lower(), assets[a]["decimals"])})
    for i, (a, m) in enumerate(v4):
        a = Web3.to_checksum_address(a)
        sp, liq = chain.decode_uint(st4[i * 2]), chain.decode_uint(st4[i * 2 + 1])
        if sp and liq and a in assets:
            pools.setdefault(a, []).append({"venue": "v4", "pool": m["id"], "fee": m["fee"], "liquidity": liq,
                                            "price": usdg_per_token(sp, m["token0"].lower() == a.lower(), assets[a]["decimals"])})
    feeds = (card.get("feeds") or {}).get("tokens") or feeds_scan.scan(card)["tokens"]
    now = int(time.time())
    twap = twap_v3(pools, assets, usdg, block)
    history = sample_history()
    items = {}
    for a in addrs:
        ps = pools.get(a, [])
        ref = weighted_median([(p["price"], p["liquidity"]) for p in ps])
        total = sum(p["liquidity"] for p in ps) or 1
        heavy = [p["price"] for p in ps if p["liquidity"] / total >= 0.10]
        feed = feeds.get(assets[a]["symbol"]) if assets[a]["kind"] == "stock_token" else None
        fp = feed.get("price_usd") if feed else None
        hist = history.get(a.lower(), [])
        med = statistics.median(hist) if len(hist) >= MIN_SAMPLES else None
        recent = hist[-6:]
        med6 = statistics.median(recent) if len(recent) >= 4 else None
        tw = twap.get(a)
        # A robust price must resist a one-block push and still follow a real fall: the on-chain
        # 30-minute average first, else the median of the last six hourly samples. The 24-hour
        # median is context only; it lags a falling market by several percent.
        robust, robust_src = ((tw["price"], "30-minute TWAP, Uniswap v3") if tw else
                              (med6, f"median of the last {len(recent)} hourly samples") if med6 else (None, None))
        items[a] = {
            "twap_30m_v3": tw, "median_6h": med6, "median_24h": med, "median_24h_samples": len(hist),
            "robust_price": robust, "robust_source": robust_src,
            "spot_vs_robust": round(ref / robust - 1, 6) if (ref and robust) else None,
            "symbol": assets[a]["symbol"], "kind": assets[a]["kind"], "price_usdg": ref, "pools": len(ps),
            "pool_spread": round(max(heavy) / min(heavy) - 1, 6) if len(heavy) > 1 else (0.0 if heavy else None),
            "top_pool_share": round(max((p["liquidity"] for p in ps), default=0) / total, 4) if ps else None,
            "feed_price_usd": fp, "feed_age_s": feed.get("age_s") if feed else None,
            "gap_to_feed": round(ref / fp - 1, 6) if (ref and fp) else None,
            "sources": sorted(({"venue": p["venue"], "pool": p["pool"], "fee": p["fee"], "price": p["price"],
                                "weight": round(p["liquidity"] / total, 4)} for p in ps), key=lambda x: -x["weight"])[:6],
        }
    priced = [x for x in items.values() if x["price_usdg"]]
    return {"kind": "prices", "chain_id": settings.CHAIN["chain"]["id"], "ts": now, "block": block, "quote": "USDG", "method": METHOD,
            "disclaimer": "measurements, not an oracle: no manipulation resistance beyond liquidity weighting, no availability promise",
            "assets": len(items), "priced": len(priced), "with_feed": sum(1 for x in priced if x["feed_price_usd"]), "items": items}


def publish(doc: dict) -> str:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    h = ledger.sha(doc)
    (OUT_DIR / f"{time.strftime('%Y-%m-%d', time.gmtime(doc['ts']))}_{h[:16]}.json").write_bytes(ledger.canonical(doc))
    return h


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    live = [p for p in settings.SCORECARD_DIR.glob("*.json")]
    card = json.loads(max(live, key=lambda p: p.stat().st_mtime).read_bytes())
    doc = measure(card)
    h = publish(doc)
    xs = [x for x in doc["items"].values() if x["price_usdg"]]
    gaps = sorted(abs(x["gap_to_feed"]) for x in xs if x["gap_to_feed"] is not None)
    print(f"prices {h[:16]} at block {doc['block']}: {doc['priced']} of {doc['assets']} assets priced, {doc['with_feed']} with a feed beside them")
    if gaps:
        print(f"  gap to feed: median {gaps[len(gaps)//2]:.2%}, worst {gaps[-1]:.2%}")
    print(f"  one-pool prices: {sum(1 for x in xs if x['pools'] == 1)}; pools disagree by >2%: {sum(1 for x in xs if (x['pool_spread'] or 0) > 0.02)}")
    for x in sorted((x for x in xs if x["gap_to_feed"] is not None), key=lambda x: -abs(x["gap_to_feed"]))[:6]:
        print(f"    {x['symbol']:6} witness {x['price_usdg']:>10.2f}  feed {x['feed_price_usd']:>10.2f}  gap {x['gap_to_feed']:+.2%}  pools {x['pools']}  feed age {round((x['feed_age_s'] or 0)/60)} min")


if __name__ == "__main__":
    main()
