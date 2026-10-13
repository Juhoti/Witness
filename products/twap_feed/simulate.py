"""Fork check of WitnessTwapFeed against live Uniswap v3 pools, without deploying anything.

Compiles the contract, runs its constructor in an eth_call to get the runtime code with its immutables
filled for a real pool, then calls latestRoundData() with that code placed at a scratch address by
state override. The answer is compared with the same 30-minute TWAP computed off chain from the pool.
A pool whose oracle cannot cover the window must revert, not return a price.

    python -m products.twap_feed.simulate [rpc_url]      # prints one JSON object
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
import solcx
from web3 import Web3
from agent import chain, opportunity, settings

SRC = Path(__file__).parent / "contracts" / "WitnessTwapFeed.sol"
SOLC = "0.8.26"
SCRATCH = Web3.to_checksum_address("0x000000000000000000000000000000000000beef")


def compile_feed() -> dict:
    out = solcx.compile_files([str(SRC)], output_values=["abi", "bin"], solc_version=SOLC, optimize=True, optimize_runs=200)
    return next(v for k, v in out.items() if k.endswith(":WitnessTwapFeed"))


def runtime_for(w3, art: dict, pool: str, base: str, window: int = 1800) -> bytes:
    ctor = w3.eth.contract(abi=art["abi"], bytecode=art["bin"]).constructor(Web3.to_checksum_address(pool), Web3.to_checksum_address(base), window, "test")
    return w3.eth.call({"data": ctor.data_in_transaction})


def simulate(rpc_url: str = "") -> dict:
    w3 = Web3(Web3.HTTPProvider(rpc_url)) if rpc_url else chain.w3()
    art = compile_feed()
    feed = w3.eth.contract(address=SCRATCH, abi=art["abi"])
    prices = opportunity._latest(settings.LEDGER_DIR / "prices") or {}
    cases = [(a, x) for a, x in prices.get("items", {}).items() if x.get("kind") == "stock_token" and x.get("twap_30m_v3")]
    cases.sort(key=lambda c: -c[1]["twap_30m_v3"]["pool_liquidity"])
    results, worst = [], 0.0
    for a, x in cases[:12]:
        tw = x["twap_30m_v3"]
        try:
            code = runtime_for(w3, art, tw["pool"], a)
            rd = feed.functions.latestRoundData().call(state_override={SCRATCH: {"code": code}})
            onchain = rd[1] / 1e8
            # recompute off chain at the same block for a like-for-like comparison
            ticks = feed.functions.averageTick().call(state_override={SCRATCH: {"code": code}})
            from agent.prices import tick_to_price
            usdg = settings.CHAIN["tokens"]["usdg"]
            offchain = tick_to_price(ticks, a.lower() < usdg.lower(), 18)
            err = abs(onchain / offchain - 1)
            worst = max(worst, err)
            results.append({"symbol": x["symbol"], "pool": tw["pool"], "contract_price": onchain, "offchain_price": offchain,
                            "relative_error": err, "witness_spot": x["price_usdg"], "chainlink": x.get("feed_price_usd")})
        except Exception as e:
            results.append({"symbol": x["symbol"], "pool": tw["pool"], "error": str(e)[:120]})
    # a pool that cannot cover the window must revert
    reverted = None
    cache_pools = [p for p in (json.loads((settings.STATE_DIR / "uniswap_pools.json").read_text())["pools"]).items() if p[1]["venue"] == "v3"]
    for pid, m in cache_pools[:200]:
        try:
            code = runtime_for(w3, art, pid, m["token0"], window=86400 * 30)
            feed.functions.latestRoundData().call(state_override={SCRATCH: {"code": code}})
        except Exception as e:
            if "OLD" in str(e) or "revert" in str(e).lower():
                reverted = {"pool": pid, "window_s": 86400 * 30, "reverted": True, "reason": str(e)[:80]}
                break
    ok = bool(results) and worst < 1e-6 and all("error" not in r for r in results) and bool(reverted)
    return {"ok": ok, "checked": len(results), "worst_relative_error": worst, "short_history_reverts": reverted, "results": results,
            "bytecode_bytes": len(bytes.fromhex(art["bin"]))}


if __name__ == "__main__":
    print(json.dumps(simulate(sys.argv[1] if len(sys.argv) > 1 else ""), indent=1, default=str))
