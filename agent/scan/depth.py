"""Liquidation depth per stock token: how many dollars of it can be sold into USDG before the price
received falls more than a set amount. Measured by simulating the sale through the Uniswap quoters at
several sizes, never by reading liquidity numbers off a page.

For each token, every v3 USDG pool (four fee tiers, looked up on the factory) and every known v4 USDG
pool is quoted at each size and the best single-pool output kept. The reference price is what a $100
sale receives; depth_usd_5pct is the largest tested size whose realised price is within 5% of that.
A liquidator could split across pools, so this is a floor. v4 pools are quoted only when the v4
Quoter address is set in config; `v4_quoted` says whether they were.
"""
from __future__ import annotations
import logging
from web3 import Web3
from .. import chain, settings
from . import uniswap

log = logging.getLogger(__name__)
FEES = (100, 500, 3000, 10000)
SIZES_USD = (100, 1_000, 10_000, 100_000, 1_000_000)
MAX_SLIP = 0.05
V4_POOLS_PER_TOKEN = 3
ZERO = "0x0000000000000000000000000000000000000000"


def _sel(sig: str) -> bytes:
    return Web3.keccak(text=sig)[:4]


def scan(card: dict) -> dict:
    factory = settings.unverified("uniswap", "v3_factory")
    quoter = settings.unverified("uniswap", "v3_quoter_v2")
    usdg = settings.unverified("tokens", "usdg")
    if not (factory and quoter and usdg):
        return {"count": 0, "tokens": {}, "note": "uniswap v3 factory/quoter or usdg not set; scanner idle"}
    codec = chain.w3().codec
    feeds = (card.get("feeds") or {}).get("tokens", {})
    prices = (card.get("uniswap") or {}).get("prices_usdg", {})
    toks = []
    for t in card.get("stock_tokens", {}).get("tokens", []):
        px = (feeds.get(t["symbol"]) or {}).get("price_usd") or (prices.get(t["address"]) or {}).get("price_usdg")
        if px and px > 0 and t.get("decimals") is not None:
            toks.append((t, float(px)))
    # 1. which v3 USDG pools exist
    calls = [(factory, _sel("getPool(address,address,uint24)") + codec.encode(["address", "address", "uint24"], [t["address"], usdg, f]))
             for t, _ in toks for f in FEES]
    raws = chain.multicall(calls)
    pools = {}
    for i, (t, _) in enumerate(toks):
        fs = [f for j, f in enumerate(FEES) if raws[i * 4 + j] and int.from_bytes(raws[i * 4 + j][-20:], "big")]
        if fs:
            pools[t["symbol"]] = fs
    # 2. quote every pool at every size
    qsel = _sel("quoteExactInputSingle((address,address,uint256,uint24,uint160))")
    plan, qcalls = [], []
    for t, px in toks:
        for f in pools.get(t["symbol"], []):
            for size in SIZES_USD:
                amount = int(size / px * 10 ** t["decimals"])
                plan.append((t["symbol"], f, size))
                qcalls.append((quoter, qsel + codec.encode(["(address,address,uint256,uint24,uint160)"], [(t["address"], usdg, amount, f, 0)])))
    # 2b. the same sale through each known v4 USDG pool
    v4q = settings.unverified("uniswap", "v4_quoter")
    v4pools: dict = {}
    if v4q:
        try:
            v4pools = uniswap.v4_usdg_pools([t["address"] for t, _ in toks])
        except Exception as e:
            log.warning("v4 pool lookup failed: %s", settings.redact(str(e))[:120])
        q4 = _sel("quoteExactInputSingle(((address,address,uint24,int24,address),bool,uint128,bytes))")

        def v4call(t, px, m, size):
            key = (m["token0"], m["token1"], m["fee"], m["tick_spacing"], m["hooks"])
            amount = min(int(size / px * 10 ** t["decimals"]), 2 ** 128 - 1)
            return (v4q, q4 + codec.encode(["((address,address,uint24,int24,address),bool,uint128,bytes)"],
                                           [(key, m["token0"].lower() == t["address"].lower(), amount, b"")]))

        # A token can have dozens of v4 pools, most of them empty. Quote the smallest size on all of
        # them first and carry only the best few to the larger sizes.
        probe = [(t, px, m) for t, px in toks for m in v4pools.get(t["address"], [])]
        small = chain.multicall([v4call(t, px, m, SIZES_USD[0]) for t, px, m in probe], batch=60) if probe else []
        ranked: dict = {}
        for (t, px, m), r in zip(probe, small):
            out = int.from_bytes(r[:32], "big") / 1e6 if r and len(r) >= 32 else 0.0
            if out > 0:
                ranked.setdefault(t["symbol"], []).append((out, t, px, m))
        for sym, rows in ranked.items():
            rows.sort(key=lambda x: -x[0])
            for out, t, px, m in rows[:V4_POOLS_PER_TOKEN]:
                for size in SIZES_USD:
                    plan.append((sym, "v4:" + m["id"][:10], size))
                    qcalls.append(v4call(t, px, m, size))
    outs = chain.multicall(qcalls, batch=40) if qcalls else []
    best: dict = {}
    for (sym, f, size), r in zip(plan, outs):
        out = int.from_bytes(r[:32], "big") / 1e6 if r and len(r) >= 32 else 0.0
        cur = best.setdefault(sym, {}).get(size)
        if cur is None or out > cur[0]:
            best[sym][size] = (out, f)
    tokens: dict = {}
    for t, px in toks:
        sym = t["symbol"]
        if sym not in best or not any(o > 0 for o, _ in best[sym].values()):
            continue
        ref_out = best[sym].get(SIZES_USD[0], (0.0, None))[0]
        ref = ref_out / SIZES_USD[0] if ref_out > 0 else None  # USDG received per $1 of token at the smallest size
        depth, quotes = 0, {}
        for size in SIZES_USD:
            out, f = best[sym].get(size, (0.0, None))
            realised = out / size if size else 0.0
            quotes[str(size)] = {"usdg_out": round(out, 2), "fee": f, "vs_ref": round(realised / ref, 4) if ref else None}
            if ref and realised >= ref * (1 - MAX_SLIP):
                depth = size
        tokens[sym] = {"ref_price_source": "feed" if (feeds.get(sym) or {}).get("price_usd") else "uniswap",
                       "ref_price_usd": px, "pool_vs_ref_price": round(ref, 4) if ref else None,
                       "v3_fee_tiers": pools.get(sym, []), "v4_pools": len(v4pools.get(t["address"], [])),
                       "quotes": quotes, "depth_usd_5pct": depth, "v4_quoted": bool(v4q)}
    return {"count": len(tokens), "tokens": tokens, "sizes_usd": list(SIZES_USD), "max_slip": MAX_SLIP,
            "tokens_priced": len(toks), "tokens_with_v3_usdg_pool": len(pools),
            "tokens_with_v4_usdg_pool": len(v4pools), "v4_quoted": bool(v4q),
            "note": "best single pool per size; a split across pools would do better"}
