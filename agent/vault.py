"""The Witness vault: what to set when creating it, and a watch on it once it exists.

    python -m agent.vault sheet [certificate]   the curator sheet: market, LLTV and cap for each listing,
                                                from a certificate or from the adopted policy
    python -m agent.vault status                the vault against the adopted policy (once config/vault.toml
                                                names it): deposits, allocations, every cap compared with
                                                the policy's, markets the policy does not list

Witness never signs. The curator sheet is what the owner enters in Morpho's app; status reports drift
between what the vault does and what the record says it should do, and the sidecar alerts on it.
"""
from __future__ import annotations
import json
import sys
import time
import tomllib
import httpx
from web3 import Web3
from . import ledger, settings

CONFIG = settings.ROOT / "config" / "vault.toml"
OUT_DIR = settings.LEDGER_DIR / "vault"
QUERY = """query($chainId:Int!){ vaultV2s(where:{chainId_in:[$chainId]}, first:200){ items {
  address name totalAssets totalAssetsUsd idleAssetsUsd netApy performanceFee managementFee curator { address } owner { address }
  caps { items { id absoluteCap relativeCap allocation type } } } } }"""
USDG_DEC = 6


def policy_from(cert_prefix: str | None) -> tuple[dict, str]:
    if cert_prefix:
        e = next((e for e in ledger.entries() if e["hash"].startswith(cert_prefix) and e.get("kind") == "policy"), None)
        if not e:
            raise SystemExit("no policy certificate with that hash")
        return e["after"], f"certificate {e['hash'][:16]} ({e['verdict']})"
    return json.loads(settings.POLICY_PATH.read_text()), "adopted policy"


def sheet(cert_prefix: str | None = None) -> str:
    pol, src = policy_from(cert_prefix)
    lines = [f"Curator sheet from {src}, policy version {pol.get('version')}.",
             "Vault: Morpho Vault V2 on Robinhood Chain (4663), asset USDG 0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168.",
             "Roles: owner and curator = your wallet. Timelock on cap increases: 24 h or more.", ""]
    total = 0
    for l in pol.get("listings", []):
        total += l["cap_usd"]
        lines.append(f"{l['symbol']:6} market {l['market']}  LLTV {l['lltv']:.1%}  cap ${l['cap_usd']:,} "
                     f"(= {l['cap_usd'] * 10 ** USDG_DEC} USDG base units)  collateral {l['address']}")
    lines += ["", f"Total of caps: ${total:,}. Every cap is depth / 5 at the time of the certificate; raise none without a new one."]
    return "\n".join(lines)


def status() -> dict:
    cfg = tomllib.loads(CONFIG.read_text()) if CONFIG.exists() else {}
    if not cfg.get("address"):
        return {"status": "no vault configured", "note": "set config/vault.toml once the vault exists"}
    addr = Web3.to_checksum_address(cfg["address"])
    r = httpx.post(settings.CHAIN["morpho"].get("graphql", "https://blue-api.morpho.org/graphql"),
                   json={"query": QUERY, "variables": {"chainId": settings.CHAIN["chain"]["id"]}}, timeout=60)
    r.raise_for_status()
    v = next((x for x in r.json()["data"]["vaultV2s"]["items"] if Web3.to_checksum_address(x["address"]) == addr), None)
    if not v:
        return {"status": "vault not found in Morpho's API yet", "address": addr}
    pol = json.loads(settings.POLICY_PATH.read_text())
    policy_total = sum(l["cap_usd"] for l in pol.get("listings", [])) * 10 ** USDG_DEC
    caps = v["caps"]["items"]
    unlimited = [c for c in caps if int(c["absoluteCap"]) >= 2 ** 127]
    over = [c for c in caps if c["type"] == "MarketV1" and int(c["absoluteCap"]) > policy_total]
    drift = []
    if (v.get("curator") or {}).get("address", "").lower() != (cfg.get("curator") or "").lower():
        drift.append("curator is not the configured wallet")
    if unlimited:
        drift.append(f"{len(unlimited)} caps are unlimited")
    if over:
        drift.append(f"{len(over)} market caps exceed the whole policy's total")
    doc = {"kind": "vault", "ts": int(time.time()), "address": addr, "name": v["name"], "deposits_usd": v.get("totalAssetsUsd"),
           "idle_usd": v.get("idleAssetsUsd"), "net_apy": v.get("netApy"), "performance_fee": v.get("performanceFee"),
           "caps": caps, "policy_version": pol.get("version"), "policy_cap_total_usd": policy_total / 10 ** USDG_DEC, "drift": drift,
           "status": "drift" if drift else "matches policy"}
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    h = ledger.sha(doc)
    (OUT_DIR / f"{time.strftime('%Y-%m-%d', time.gmtime(doc['ts']))}_{h[:16]}.json").write_bytes(ledger.canonical(doc))
    return doc


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "sheet":
        print(sheet(sys.argv[2] if len(sys.argv) > 2 else None))
    else:
        print(json.dumps(status(), indent=1, default=str)[:3000])
