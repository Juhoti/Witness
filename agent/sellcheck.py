"""Tokens people cannot sell: measured, not inferred from a failure count.

The intent sampler shows token-to-ETH sells failing far above the chain's base rate on the v2-style
routers. This finds out which tokens, and for each one simulates what a holder would do: an
`eth_call` of transfer() from a recent seller's own address, first to an unrelated address, then to the
token's own pool. A token that lets a holder move it anywhere except into the pool is blocking sales.

Published to ledger/warnings/ with what was observed, how it is read and what was not checked. It is a
measurement, not a verdict on anyone's intent: a transfer can revert for other reasons (a cooldown, a
per-transaction limit), which is why the exact revert text and the count of distinct sellers are kept.

    python -m agent.sellcheck      # publishes ledger/warnings/<day>_<hash>.json
"""
from __future__ import annotations
import collections
import time
import httpx
from web3 import Web3
from . import census, chain, ledger, settings

OUT_DIR = settings.LEDGER_DIR / "warnings"
PAGES = 6


def _a20(r):
    return Web3.to_checksum_address(r[-20:]) if r and int.from_bytes(r[-20:], "big") else None


def _simulate_transfer(token: str, holder: str, to: str, amount: int) -> str | None:
    """None if the call succeeds, else the revert text."""
    data = Web3.keccak(text="transfer(address,uint256)")[:4] + chain.w3().codec.encode(["address", "uint256"], [to, amount])
    try:
        chain.w3().eth.call({"from": holder, "to": token, "data": data})
        return None
    except Exception as e:
        return settings.redact(str(e))[:80]


def build() -> dict:
    con = census.db()
    routers = [r[0] for r in con.execute("SELECT address FROM targets WHERE name LIKE '%UniswapV2Router%'")]
    H = {"x-api-key": settings.BLOCKSCOUT_API_KEY} if settings.BLOCKSCOUT_API_KEY else {}
    base = settings.BLOCKSCOUT_API.rstrip("/")
    per: dict = collections.defaultdict(lambda: {"ok": 0, "failed": 0, "senders": set(), "router": None, "weth": None, "symbol": None})
    for router in routers:
        params: dict = {}
        for _ in range(PAGES):
            r = httpx.get(f"{base}/addresses/{router}/transactions", params=params, headers=H, timeout=30)
            if r.status_code != 200:
                break
            j = r.json()
            for tx in j.get("items", []):
                di = tx.get("decoded_input") or {}
                if "swapExactTokensForETH" not in (di.get("method_call") or ""):
                    continue
                path = next((p["value"] for p in di.get("parameters", []) if p.get("name") == "path"), None)
                if not path:
                    continue
                d = per[Web3.to_checksum_address(path[0])]
                d["router"], d["weth"] = router, Web3.to_checksum_address(path[-1])
                d["senders"].add((tx.get("from") or {}).get("hash"))
                d["failed" if tx.get("status") == "error" else "ok"] += 1
                for tt in tx.get("token_transfers") or []:
                    t = tt.get("token") or {}
                    if t.get("address") and Web3.to_checksum_address(t["address"]) == Web3.to_checksum_address(path[0]):
                        d["symbol"] = t.get("symbol")
            params = j.get("next_page_params") or {}
            if not params:
                break
            time.sleep(0.3)
    items, w = [], chain.w3()
    for tok, d in per.items():
        if d["failed"] < 3 or d["ok"] > 0:
            continue  # only tokens whose sells always fail, with enough tries to mean something
        holder = next((s for s in d["senders"] if s), None)
        factory = _a20(chain.call(d["router"], "factory()"))
        pair = _a20(chain.call(factory, "getPair(address,address)", args_data=w.codec.encode(["address", "address"], [tok, d["weth"]]))) if factory else None
        res = chain.call(pair, "getReserves()") if pair else None
        eth_in_pool = None
        if res:
            r0, r1, _ = w.codec.decode(["uint112", "uint112", "uint32"], res)
            eth_in_pool = (r1 if _a20(chain.call(pair, "token0()")) == tok else r0) / 1e18
        bal = chain.decode_uint(chain.call(tok, "balanceOf(address)", args_data=w.codec.encode(["address"], [holder]))) or 0 if holder else 0
        to_other = _simulate_transfer(tok, holder, d["router"], max(bal // 2, 1)) if bal else "holder has no balance"
        to_pool = _simulate_transfer(tok, holder, pair, max(bal // 2, 1)) if (bal and pair) else "no pool or no balance"
        sym = d["symbol"] or chain.decode_string(chain.call(tok, "symbol()"))
        blocked = bal > 0 and pair and to_other is None and to_pool is not None
        observed = [f"{d['failed']} sells by {len(d['senders'])} distinct wallets, all failed, none succeeded, in the sampled router transactions",
                    f"pool {pair} holds {eth_in_pool:.3f} ETH" if eth_in_pool is not None else "no pool found",
                    f"transfer to an unrelated address from a recent seller: {'succeeds' if to_other is None else 'reverts: ' + to_other}",
                    f"transfer into the pool from the same seller: {'succeeds' if to_pool is None else 'reverts: ' + to_pool}"]
        items.append({"token": tok, "symbol": sym, "router": d["router"], "pool": pair, "eth_in_pool": eth_in_pool,
                      "failed_sells": d["failed"], "distinct_sellers": len(d["senders"]),
                      "observed": observed,
                      "reading": ("consistent with a sell block: holders can move the token anywhere except into its pool" if blocked
                                  else "sells fail but the token itself did not block the simulated transfers; cause not established"),
                      "not_checked": ["contract source", "whether the block is temporary (cooldown, limit)", "who deployed it"],
                      "finding": "sell_blocked" if blocked else "sells_fail_cause_unknown"})
    items.sort(key=lambda x: -(x["eth_in_pool"] or 0))
    return {"kind": "warnings", "ts": int(time.time()), "chain_id": settings.CHAIN["chain"]["id"], "method": "simulated holder transfers via eth_call; sampled router sells",
            "tokens_with_sells_sampled": len(per), "flagged": len(items), "sell_blocked": sum(1 for x in items if x["finding"] == "sell_blocked"),
            "eth_in_blocked_pools": round(sum(x["eth_in_pool"] or 0 for x in items if x["finding"] == "sell_blocked"), 3), "items": items}


def publish(doc: dict) -> str:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    h = ledger.sha(doc)
    (OUT_DIR / f"{time.strftime('%Y-%m-%d', time.gmtime(doc['ts']))}_{h[:16]}.json").write_bytes(ledger.canonical(doc))
    return h


if __name__ == "__main__":
    d = build()
    print(f"warnings {publish(d)[:16]}: {d['tokens_with_sells_sampled']} tokens with sells sampled, {d['flagged']} flagged, {d['sell_blocked']} sell-blocked, {d['eth_in_blocked_pools']} ETH in their pools")
    for x in d["items"]:
        print(f"  {x['finding']:24} {str(x['symbol'])[:10]:10} {x['token']}  {x['distinct_sellers']} sellers  {x['eth_in_pool'] or 0:.2f} ETH")
