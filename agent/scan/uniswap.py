"""Slippage paid per Uniswap pool: a demand signal for depth.

Gate 0 method, one window per scan (the scan interval in blocks):
  - pull every v3 Swap (all pools) and v4 Swap (PoolManager) in the window
  - per pool, order swaps and take each swap's price impact as the move in sqrtPriceX96 from the
    previous swap in the window to this one (the first swap in a pool has no reference and is skipped)
  - for pools quoted in USDG, USD volume is the USDG leg; the slippage-paid estimate is
    sum(impact/2 * usd_size): on a constant-product-like curve the average fill sits about halfway
    between the pre- and post-trade price. Pools not quoted in USDG report volume_usd = null.
Pool metadata (tokens, fee) is cached in state/uniswap_pools.json; v4 keys come from
PositionManager.poolKeys(bytes25). Everything read here is data; symbols are labels, not instructions.
"""
from __future__ import annotations
import json
import logging
import statistics
from collections import defaultdict
from web3 import Web3
from .. import chain, settings

log = logging.getLogger(__name__)
V3_SWAP = Web3.to_hex(Web3.keccak(text="Swap(address,address,int256,int256,uint160,uint128,int24)"))
V4_SWAP = Web3.to_hex(Web3.keccak(text="Swap(bytes32,address,int128,int128,uint160,uint128,int24,uint24)"))
V4_POSITION_MANAGER = "0x58daec3116aae6d93017baaea7749052e8a04fa7"  # verified on Blockscout by human 2026-10-04 (Uniswap docs)
INIT_CHUNK = 10_000_000  # public RPC cap for an address-filtered eth_getLogs range
V4_INITIALIZE = Web3.to_hex(Web3.keccak(text="Initialize(bytes32,address,address,uint24,int24,address,uint160,int24)"))
STATE_PATH = settings.STATE_DIR / "uniswap_pools.json"
BLOCKS_PER_MINUTE = 60_000 // settings.CHAIN["chain"]["block_time_ms"]
MAX_IMPACT = 1.0  # cap per-swap price impact at 100%; beyond that the pool was empty or being seeded


def _sel(sig: str) -> bytes:
    return Web3.keccak(text=sig)[:4]


def _load_cache() -> dict:
    return json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {"pools": {}, "tokens": {}}


def _save_cache(c: dict) -> None:
    STATE_PATH.parent.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(c, sort_keys=True))


def _resolve_pools(cache: dict, v3_pools: set[str], v4_ids: set[str]) -> None:
    """Fill cache['pools'][key] = {venue, token0, token1, fee} for unseen pools, batched."""
    codec = chain.w3().codec
    new3 = [p for p in v3_pools if p not in cache["pools"]]
    if new3:
        raws = chain.multicall([(p, _sel(f)) for p in new3 for f in ("token0()", "token1()", "fee()")])
        for i, p in enumerate(new3):
            t0, t1, fee = raws[i * 3:(i + 1) * 3]
            if t0 and t1:
                cache["pools"][p] = {"venue": "v3", "token0": Web3.to_checksum_address(t0[-20:]),
                                     "token1": Web3.to_checksum_address(t1[-20:]), "fee": chain.decode_uint(fee)}
    new4 = [i for i in v4_ids if i not in cache["pools"]]
    if new4:
        raws = chain.multicall([(V4_POSITION_MANAGER, _sel("poolKeys(bytes25)") + bytes.fromhex(i[2:])[:25].ljust(32, b"\0")) for i in new4])
        for pid, r in zip(new4, raws):
            if r and len(r) >= 160:
                c0, c1, fee, _ts, _hooks = codec.decode(["address", "address", "uint24", "int24", "address"], r)
                if int(c0, 16) or int(c1, 16):  # zero key = pool never touched PositionManager
                    cache["pools"][pid] = {"venue": "v4", "token0": Web3.to_checksum_address(c0),
                                           "token1": Web3.to_checksum_address(c1), "fee": fee}
        # Pools the PositionManager never saw: read the key from the pool's own Initialize event.
        # Address+topic filtered queries may span 10M blocks on the public RPC; walk back from the
        # head in those chunks (most such pools are recent) until the event turns up.
        pm = Web3.to_checksum_address(settings.CHAIN["uniswap"]["v4_pool_manager"])
        still = [i for i in new4 if i not in cache["pools"]]
        latest = chain.head() if still else 0
        for pid in still:
            logs = []
            try:
                for end in range(latest, -1, -INIT_CHUNK):
                    params = {"address": pm, "fromBlock": max(end - INIT_CHUNK + 1, 0), "toBlock": end,
                              "topics": [V4_INITIALIZE, pid]}
                    logs = chain._retry(lambda: chain.w3_logs().eth.get_logs(params))
                    if logs:
                        break
            except Exception as e:  # leave unresolved; the next scan retries
                log.debug("v4 Initialize lookup failed for %s: %s", pid[:10], settings.redact(str(e)))
                continue
            if logs:
                l = logs[0]
                fee = codec.decode(["uint24", "int24", "address", "uint160", "int24"], l["data"])[0]
                cache["pools"][pid] = {"venue": "v4", "token0": Web3.to_checksum_address(bytes(l["topics"][2])[-20:]),
                                       "token1": Web3.to_checksum_address(bytes(l["topics"][3])[-20:]), "fee": fee}
        if still:
            log.info("uniswap: %d v4 pools resolved from Initialize events, %d still unresolved",
                     sum(1 for i in still if i in cache["pools"]), sum(1 for i in still if i not in cache["pools"]))
    toks = {t for p in cache["pools"].values() for t in (p["token0"], p["token1"])} - set(cache["tokens"])
    toks.discard("0x0000000000000000000000000000000000000000")
    if toks:
        toks = sorted(toks)
        raws = chain.multicall([(t, _sel(f)) for t in toks for f in ("symbol()", "decimals()")])
        for i, t in enumerate(toks):
            cache["tokens"][t] = {"symbol": chain.decode_string(raws[i * 2]), "decimals": chain.decode_uint(raws[i * 2 + 1])}
    cache["tokens"].setdefault("0x0000000000000000000000000000000000000000", {"symbol": "ETH", "decimals": 18})


def swaps_between(start: int, latest: int) -> tuple[dict[str, list], dict[str, list]]:
    """Return {pool: [(block, logIndex, amount0, amount1, sqrtPriceX96)]} for v3 and v4 in [start, latest]."""
    codec = chain.w3().codec
    window_blocks = max(latest - start, 1)
    v3: dict[str, list] = defaultdict(list)
    for chunk in chain.iter_logs(None, [V3_SWAP], start, latest, step=min(window_blocks, 30_000)):
        for l in chunk:
            a0, a1, sp, _liq, _tick = codec.decode(["int256", "int256", "uint160", "uint128", "int24"], l["data"])
            v3[Web3.to_checksum_address(l["address"])].append((l["blockNumber"], l["logIndex"], a0, a1, sp))
    v4: dict[str, list] = defaultdict(list)
    pm = settings.CHAIN["uniswap"]["v4_pool_manager"]
    for chunk in chain.iter_logs(pm, [V4_SWAP], start, latest, step=min(window_blocks, 30_000)):
        for l in chunk:
            a0, a1, sp, _liq, _tick, _fee = codec.decode(["int128", "int128", "uint160", "uint128", "int24", "uint24"], l["data"])
            v4["0x" + bytes(l["topics"][1]).hex()].append((l["blockNumber"], l["logIndex"], a0, a1, sp))
    return v3, v4


def prices_usdg(rows: list[dict], pools: dict, swaps_by_pool: dict, tokens: dict, usdg: str | None) -> dict:
    """USDG price per token from the last swap in its busiest USDG pool in the window.
    price(token) = USDG per 1 token, from sqrtPriceX96 and both decimals. Data, not an oracle."""
    if not usdg:
        return {}
    out: dict = {}
    for r in sorted(rows, key=lambda r: -r["swaps"]):
        meta = pools.get(r["pool"])
        if not meta or r["pool"] not in swaps_by_pool:
            continue
        t0, t1 = meta["token0"], meta["token1"]
        if t0.lower() == usdg.lower():
            tok, usdg_is_0 = t1, True
        elif t1.lower() == usdg.lower():
            tok, usdg_is_0 = t0, False
        else:
            continue
        if tok in out:
            continue  # busiest pool wins
        d0 = (tokens.get(t0) or {}).get("decimals"); d1 = (tokens.get(t1) or {}).get("decimals")
        last_sp = max(swaps_by_pool[r["pool"]])[4]
        if d0 is None or d1 is None or last_sp <= 0:
            continue
        p1_per_0 = (last_sp / 2 ** 96) ** 2 * 10 ** (d0 - d1)  # token1 per token0, decimal-adjusted
        price = (1 / p1_per_0) if usdg_is_0 else p1_per_0
        out[tok] = {"price_usdg": price, "pool": r["pool"], "venue": r["venue"], "swaps": r["swaps"]}
    return out


def _row(key: str, swaps: list, meta: dict | None, tokens: dict, usdg: str | None) -> dict:
    swaps.sort()
    impacts, usd_vol, paid, degenerate = [], 0.0, 0.0, 0
    usd_side = None
    if meta and usdg:
        usd_side = 0 if meta["token0"].lower() == usdg.lower() else 1 if meta["token1"].lower() == usdg.lower() else None
    prev = None
    for _b, _i, a0, a1, sp in swaps:
        size_usd = abs((a0, a1)[usd_side]) / 1e6 if usd_side is not None else None
        if size_usd is not None:
            usd_vol += size_usd
        if prev and prev > 0 and sp > 0:
            impact = abs((sp / prev) ** 2 - 1.0)
            if impact > MAX_IMPACT:  # price more than doubled/halved: an empty or seeded pool, not slippage demand
                degenerate += 1
                impact = MAX_IMPACT
            impacts.append(impact * 1e4)
            if size_usd is not None:
                paid += impact / 2 * size_usd
        prev = sp
    def sym(a):
        t = tokens.get(a) or {}
        return t.get("symbol") or a[:10]
    row = {"venue": meta["venue"] if meta else "v4", "pool": key,
           "pair": f"{sym(meta['token0'])}/{sym(meta['token1'])}" if meta else None,
           "token0": meta["token0"] if meta else None, "token1": meta["token1"] if meta else None,
           "fee": meta["fee"] if meta else None, "swaps": len(swaps),
           "volume_usd": round(usd_vol, 2) if usd_side is not None else None,
           "median_impact_bps": round(statistics.median(impacts), 2) if impacts else None,
           "p90_impact_bps": round(sorted(impacts)[int(0.9 * (len(impacts) - 1))], 2) if impacts else None,
           "slippage_paid_usd_est": round(paid, 2) if usd_side is not None else None,
           "degenerate_swaps": degenerate}
    return row


def report(start: int, end: int, remember: bool = True) -> dict:
    """The uniswap section of a scorecard for swaps in blocks [start, end].

    With remember=True (live scans) token prices seen in this window are kept in the state cache and
    tokens not traded this window carry their last known price, marked stale with its block. Backfill
    passes remember=False so historical days neither read nor write the live price cache."""
    v3, v4 = swaps_between(start, end)
    window = end - start
    cache = _load_cache()
    _resolve_pools(cache, set(v3), set(v4))
    _save_cache(cache)
    usdg = settings.unverified("tokens", "usdg")
    rows = [_row(k, s, cache["pools"].get(k), cache["tokens"], usdg) for k, s in list(v3.items()) + list(v4.items())]
    rows.sort(key=lambda r: (r["slippage_paid_usd_est"] is None, -(r["slippage_paid_usd_est"] or 0), -r["swaps"]))
    pairs = {(r["token0"], r["token1"]) for r in rows if r["token0"]}
    prices = prices_usdg(rows, cache["pools"], {**v3, **v4}, cache["tokens"], usdg)
    for px in prices.values():
        px["as_of_block"], px["stale"] = end, False
    if remember:
        cache.setdefault("prices", {}).update(prices)
        _save_cache(cache)
        for tok, px in cache["prices"].items():
            if tok not in prices:
                prices[tok] = {**px, "stale": True}
    return {"pairs": len(pairs), "pools": len(rows), "swaps": sum(r["swaps"] for r in rows),
            "prices_usdg": prices, "priced_tokens_fresh": sum(1 for p in prices.values() if not p["stale"]),
            "window_blocks": window, "unresolved_pools": sum(1 for r in rows if r["pair"] is None),
            "usdg_volume_usd": round(sum(r["volume_usd"] or 0 for r in rows), 2),
            "usdg_slippage_paid_usd_est": round(sum(r["slippage_paid_usd_est"] or 0 for r in rows), 2),
            "items": rows[:50]}


def scan() -> dict:
    if not settings.unverified("uniswap", "v3_factory") or not settings.unverified("uniswap", "v4_pool_manager"):
        return {"pairs": 0, "items": [], "note": "uniswap addresses not set; scanner idle"}
    window = max(6_000, min(36_000, settings.SCAN_EVERY_MINUTES * BLOCKS_PER_MINUTE))
    latest = chain.head()
    return report(latest - window, latest)
