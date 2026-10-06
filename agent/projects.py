"""Other projects on the chain, measured: what the chain says against what they say.

config/projects.toml lists projects, their claims in one line, and the contracts they or the explorer
name, each with its source. For every contract this reads from the chain and the explorer:
  what it is (token, vault, other), supply, holders, transactions, age, burnt balance
  Witness's price and the pools behind it, where there is one
  share of the chain's event logs, from the census
  for a dollar token with a named reserve asset: supply against reserve held by the contract
  for a vault (ERC-4626): assets under management and the asset
It publishes the measurements with the project's claim beside them and does not grade the claim; a
reader can. Names and claims are the projects' own words, kept as data.

    python -m agent.projects       # publishes ledger/projects/<day>_<hash>.json
"""
from __future__ import annotations
import time
import tomllib
import httpx
from web3 import Web3
from . import census, chain, ledger, settings
from .scan import uniswap

OUT_DIR = settings.LEDGER_DIR / "projects"
BURN = ("0x000000000000000000000000000000000000dEaD", "0x0000000000000000000000000000000000000000")


def _sel(s: str) -> bytes:
    return Web3.keccak(text=s)[:4]


def _addr(r):
    return Web3.to_checksum_address(r[-20:]) if r and len(r) >= 32 and int.from_bytes(r[-20:], "big") else None


def _explorer(path: str) -> dict:
    if not settings.BLOCKSCOUT_API_KEY:
        return {}
    try:
        r = httpx.get(settings.BLOCKSCOUT_API.rstrip("/") + path, headers={"x-api-key": settings.BLOCKSCOUT_API_KEY}, timeout=20)
        return r.json() if r.status_code == 200 else {}
    except Exception:
        return {}


def measure_contract(c: dict, total_logs: int) -> dict:
    a = Web3.to_checksum_address(c["address"])
    w = chain.w3(); codec = w.codec
    raws = chain.multicall([(a, _sel(s)) for s in ("symbol()", "name()", "decimals()", "totalSupply()", "asset()", "totalAssets()")])
    sym, name, dec, sup, asset, tot = raws
    d = chain.decode_uint(dec)
    out = {"label": c["label"], "address": a, "source": c["source"], "code_bytes": len(chain._retry(lambda: w.eth.get_code(a))),
           "symbol": chain.decode_string(sym), "name": chain.decode_string(name), "decimals": d}
    supply = chain.decode_uint(sup)
    out["kind"] = "erc4626_vault" if _addr(asset) and chain.decode_uint(tot) is not None else "token" if (out["symbol"] and d is not None) else "contract"
    if supply is not None and d is not None and d < 80:
        out["total_supply"] = supply / 10 ** d
        burnt = chain.multicall([(a, _sel("balanceOf(address)") + codec.encode(["address"], [b])) for b in BURN])
        out["in_burn_addresses"] = sum((chain.decode_uint(x) or 0) for x in burnt) / 10 ** d
    if out["kind"] == "erc4626_vault":
        asset_a = _addr(asset)
        ad = chain.decode_uint(chain.call(asset_a, "decimals()")) or 18
        out["asset"] = {"address": asset_a, "symbol": chain.decode_string(chain.call(asset_a, "symbol()"))}
        out["assets_under_management"] = (chain.decode_uint(tot) or 0) / 10 ** ad
    if c.get("reserve") and supply is not None and d is not None:
        r = Web3.to_checksum_address(c["reserve"]); rd = chain.decode_uint(chain.call(r, "decimals()")) or 18
        held = (chain.decode_uint(chain.call(r, "balanceOf(address)", args_data=codec.encode(["address"], [a]))) or 0) / 10 ** rd
        out["reserve"] = {"asset": chain.decode_string(chain.call(r, "symbol()")), "held_by_contract": held, "supply": out["total_supply"],
                          "backing_ratio": round(held / out["total_supply"], 6) if out["total_supply"] else None}
    tok = _explorer(f"/tokens/{a}")
    cnt = _explorer(f"/addresses/{a}/counters")
    info = _explorer(f"/addresses/{a}")
    out["explorer"] = {"holders": tok.get("holders_count") or tok.get("holders"), "transactions": cnt.get("transactions_count"),
                       "verified": info.get("is_verified"), "verified_name": info.get("name"), "created_tx": info.get("creation_transaction_hash")}
    cache = uniswap._load_cache()
    px = (cache.get("prices") or {}).get(a) or (cache.get("prices") or {}).get(a.lower())
    pools = [m for m in cache["pools"].values() if a.lower() in (m["token0"].lower(), m["token1"].lower())]
    out["market"] = {"witness_price_usdg": px and px.get("price_usdg"), "pools_seen": len(pools),
                     "fully_diluted_usd": (px["price_usdg"] * out["total_supply"]) if (px and out.get("total_supply")) else None}
    con = census.db()
    row = con.execute("SELECT logs, first_block, last_block FROM contracts WHERE address=?", (a,)).fetchone()
    out["activity"] = {"logs_in_census": row[0] if row else 0, "share_of_chain_logs": round((row[0] if row else 0) / max(total_logs, 1), 6),
                       "first_block_seen": row[1] if row else None, "last_block_seen": row[2] if row else None}
    return out


def build() -> dict:
    cfg = tomllib.loads((settings.ROOT / "config" / "projects.toml").read_text())
    total = census.db().execute("SELECT coalesce(sum(n),0) FROM activity").fetchone()[0]
    projects = []
    for p in cfg.get("project", []):
        contracts = [measure_contract(c, total) for c in p.get("contract", [])]
        projects.append({"name": p["name"], "site": p.get("site"), "claims": p.get("claims"), "contracts": contracts,
                         "share_of_chain_logs": round(sum(c["activity"]["share_of_chain_logs"] for c in contracts), 6)})
    return {"kind": "projects", "ts": int(time.time()), "chain_id": settings.CHAIN["chain"]["id"],
            "method": "chain reads via Multicall3, explorer counters, Witness price cache, census log counts; claims are the projects' own words",
            "census_logs": total, "projects": projects}


def publish(doc: dict) -> str:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    h = ledger.sha(doc)
    (OUT_DIR / f"{time.strftime('%Y-%m-%d', time.gmtime(doc['ts']))}_{h[:16]}.json").write_bytes(ledger.canonical(doc))
    return h


if __name__ == "__main__":
    d = build()
    print(f"projects {publish(d)[:16]}: {len(d['projects'])} projects against {d['census_logs']:,} census logs")
    for p in d["projects"]:
        print(f"\n{p['name']}  (share of chain logs {p['share_of_chain_logs']:.3%})  claims: {p['claims'][:70]}")
        for c in p["contracts"]:
            e, m, act = c["explorer"], c["market"], c["activity"]
            line = f"  {c['label'][:34]:34} {c['kind']:13} holders {e.get('holders')!s:>6} txs {e.get('transactions')!s:>7} logs {act['logs_in_census']:>7}"
            if c.get("total_supply") is not None: line += f"  supply {c['total_supply']:,.0f} burnt {c.get('in_burn_addresses', 0):,.0f}"
            if m.get("fully_diluted_usd"): line += f"  fdv ${m['fully_diluted_usd']:,.0f}"
            if c.get("assets_under_management") is not None: line += f"  AUM {c['assets_under_management']:,.0f} {c['asset']['symbol']}"
            if c.get("reserve"): line += f"  backing {c['reserve']['backing_ratio']} ({c['reserve']['held_by_contract']:,.2f} {c['reserve']['asset']} vs {c['reserve']['supply']:,.2f})"
            print(line)
