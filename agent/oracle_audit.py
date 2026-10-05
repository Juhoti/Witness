"""Oracle audit: how is each Morpho market on the chain actually priced, and would liquidations fire?

A lending market is only as safe as the price it reads. The failure this looks for is the one that
broke several vaults in late 2025: collateral priced by a constant, so that when its real value fell
the market never noticed. For every market the audit reads the oracle on chain and records:

  structure   which feeds and vault exchange rates the oracle is built from (Morpho's standard
              oracle exposes BASE_FEED_1/2, QUOTE_FEED_1/2, BASE_VAULT, QUOTE_VAULT)
  movement    price() now, one day ago and seven days ago, by historical call
  feeds       whether each feed is in Chainlink's directory for this chain, and its age
  market      the Uniswap USDG price of the collateral where one exists, and the gap to the oracle

and gives it one finding, most serious first:
  no_oracle        nothing at the oracle address, or price() reverts
  constant         no feed and no vault behind the price, and it has not moved in seven days
  fixed_with_delayed_backup
                   a meta-oracle whose active source is a constant, with a live feed as backup that
                   takes over only after the two disagree by a threshold for a timelock and someone
                   calls it. The market does see a fall, but late: the record states the threshold
                   and the delay
  frozen           built from feeds or vaults, yet unchanged for seven days
  vault_rate_only  priced purely from a vault's own exchange rate (the asset vouches for itself)
  unlisted_feed    reads a feed that is not in Chainlink's directory
  stale_feed       a feed is older than twice its heartbeat
  off_market       oracle and Uniswap disagree by more than 5%
  ok

Standalone and read-only; results go to state/oracle_audit.json. Nothing here is a verdict on a
market's curator: an unusual oracle can be deliberate. It is a list of what to look at, by size.

    python -m agent.oracle_audit
"""
from __future__ import annotations
import json
import logging
import time
from collections import Counter, defaultdict
import httpx
from web3 import Web3
from . import chain, settings
from .scan import feeds as feeds_scan

log = logging.getLogger("oracle_audit")
OUT = settings.STATE_DIR / "oracle_audit.json"
DAY_BLOCKS = 864_000
SLOTS = ("BASE_FEED_1()", "BASE_FEED_2()", "QUOTE_FEED_1()", "QUOTE_FEED_2()", "BASE_VAULT()", "QUOTE_VAULT()")
QUERY = """query($chainId:Int!,$first:Int!,$skip:Int!){ markets(where:{chainId_in:[$chainId]}, first:$first, skip:$skip){
  pageInfo{countTotal} items{ marketId lltv oracle{address} loanAsset{address symbol decimals} collateralAsset{address symbol decimals}
  state{supplyAssetsUsd borrowAssetsUsd collateralAssetsUsd utilization} } } }"""
ORDER = ("no_oracle", "constant", "fixed_with_delayed_backup", "frozen", "vault_rate_only", "unlisted_feed", "stale_feed", "off_market", "ok")
META = ("primaryOracle()", "backupOracle()", "currentOracle()", "deviationThreshold()", "challengeTimelockDuration()", "healingTimelockDuration()")


def _sel(sig: str) -> bytes:
    return Web3.keccak(text=sig)[:4]


def _addr(raw):
    if not raw or len(raw) < 32 or not int.from_bytes(raw[-20:], "big"):
        return None
    return Web3.to_checksum_address(raw[-20:])


def _markets() -> list[dict]:
    url = settings.CHAIN["morpho"].get("graphql", "https://blue-api.morpho.org/graphql")
    items: list = []
    while True:
        r = httpx.post(url, json={"query": QUERY, "variables": {"chainId": settings.CHAIN["chain"]["id"], "first": 200, "skip": len(items)}}, timeout=60)
        r.raise_for_status()
        pg = r.json()["data"]["markets"]
        items += pg["items"]
        if not pg["items"] or len(items) >= pg["pageInfo"]["countTotal"]:
            return items


def audit() -> dict:
    markets = [m for m in _markets() if m.get("collateralAsset") and (m.get("oracle") or {}).get("address")]
    head = chain._retry(lambda: chain.w3().eth.block_number)
    oracles = [m["oracle"]["address"] for m in markets]
    psel = _sel("price()")
    now_p = chain.multicall([(o, psel) for o in oracles])
    d1_p = chain.multicall([(o, psel) for o in oracles], block=head - DAY_BLOCKS)
    d7_p = chain.multicall([(o, psel) for o in oracles], block=head - 7 * DAY_BLOCKS)
    slots = chain.multicall([(o, _sel(s)) for o in oracles for s in SLOTS])
    # meta-oracles: a wrapper that picks between a primary and a backup oracle. Follow both.
    meta_raw = chain.multicall([(o, _sel(s)) for o in oracles for s in META])
    metas: dict[int, dict] = {}
    for i in range(len(oracles)):
        prim, back, cur, thr, chal, heal = meta_raw[i * 6:(i + 1) * 6]
        if _addr(prim) and _addr(back):
            metas[i] = {"primary": _addr(prim), "backup": _addr(back), "active": "primary" if _addr(cur) == _addr(prim) else "backup",
                        "deviation_threshold": (chain.decode_uint(thr) or 0) / 1e18, "challenge_timelock_h": (chain.decode_uint(chal) or 0) / 3600,
                        "healing_timelock_h": (chain.decode_uint(heal) or 0) / 3600}
    subs = sorted({a for m in metas.values() for a in (m["primary"], m["backup"])})
    sub_slots = chain.multicall([(o, _sel(s)) for o in subs for s in SLOTS])
    sub_now = chain.multicall([(o, psel) for o in subs])
    sub_d7 = chain.multicall([(o, psel) for o in subs], block=head - 7 * DAY_BLOCKS)
    sub_info = {}
    for j, o in enumerate(subs):
        sl = [_addr(x) for x in sub_slots[j * 6:(j + 1) * 6]]
        p_now, p_7 = chain.decode_uint(sub_now[j]), chain.decode_uint(sub_d7[j])
        sub_info[o] = {"feeds": [a for a in sl[:4] if a], "vaults": [a for a in sl[4:] if a], "price_raw": p_now,
                       "constant": not any(sl) and p_now is not None and p_now == p_7}
    # feeds: directory listing, plus what each referenced feed says on chain
    directory, _fresh = feeds_scan._directory()
    listed = {Web3.to_checksum_address(e["proxyAddress"]): e for e in directory if e.get("proxyAddress")}
    used = sorted({a for i in range(len(markets)) for a in map(_addr, slots[i * 6:i * 6 + 4]) if a} | {f for v in sub_info.values() for f in v["feeds"]})
    fraw = chain.multicall([(f, _sel(s)) for f in used for s in ("description()", "latestRoundData()")])
    codec, now = chain.w3().codec, int(time.time())
    feed_info = {}
    for i, f in enumerate(used):
        desc, rd = fraw[i * 2], fraw[i * 2 + 1]
        upd = codec.decode(["uint80", "int256", "uint256", "uint256", "uint80"], rd)[3] if rd and len(rd) >= 160 else None
        hb = (listed.get(f) or {}).get("heartbeat")
        feed_info[f] = {"description": chain.decode_string(desc), "in_directory": f in listed, "age_s": (now - upd) if upd else None,
                        "heartbeat_s": hb, "stale": bool(upd and hb and now - upd > 2 * hb)}
    # market prices from the uniswap scanner's cache
    prices = {}
    up = settings.STATE_DIR / "uniswap_pools.json"
    if up.exists():
        prices = {k.lower(): v for k, v in (json.loads(up.read_text()).get("prices") or {}).items()}
    usdg = (settings.unverified("tokens", "usdg") or "").lower()
    rows = []
    for i, m in enumerate(markets):
        coll, loan = m["collateralAsset"], m["loanAsset"]
        scale = 10 ** (36 + loan["decimals"] - coll["decimals"])
        p0, p1, p7 = (chain.decode_uint(x) for x in (now_p[i], d1_p[i], d7_p[i]))
        s = [_addr(x) for x in slots[i * 6:(i + 1) * 6]]
        fds, vaults = [a for a in s[:4] if a], [a for a in s[4:] if a]
        px = p0 / scale if p0 else None
        mkt = (prices.get(coll["address"].lower()) or {}).get("price_usdg") if loan["address"].lower() == usdg else None
        gap = (px / mkt - 1) if (px and mkt) else None
        meta = metas.get(i)
        if meta:
            act, oth = sub_info[meta[meta["active"]]], sub_info[meta["backup" if meta["active"] == "primary" else "primary"]]
            meta = {**meta, "active_is_constant": act["constant"], "other_has_feed": bool(oth["feeds"]),
                    "backup_feeds": [{"address": f, **feed_info[f]} for f in sub_info[meta["backup"]]["feeds"]],
                    "backup_price": (sub_info[meta["backup"]]["price_raw"] or 0) / scale or None}
            if not act["constant"]:
                fds, vaults = act["feeds"], act["vaults"]   # judged below on the source it is actually reading
        if p0 is None or p0 == 0:
            finding = "no_oracle"
        elif meta and meta["active_is_constant"] and meta["other_has_feed"]:
            finding = "fixed_with_delayed_backup"
        elif not fds and not vaults and p7 is not None and p0 == p7:
            finding = "constant"
        elif p7 is not None and p0 == p7 and p0 == p1:
            finding = "frozen"
        elif vaults and not fds:
            finding = "vault_rate_only"
        elif any(not feed_info[f]["in_directory"] for f in fds):
            finding = "unlisted_feed"
        elif any(feed_info[f]["stale"] for f in fds):
            finding = "stale_feed"
        elif gap is not None and abs(gap) > 0.05:
            finding = "off_market"
        else:
            finding = "ok"
        st = m.get("state") or {}
        rows.append({"market": m["marketId"], "collateral": coll["symbol"], "loan": loan["symbol"], "lltv": int(m["lltv"]) / 1e18,
                     "oracle": m["oracle"]["address"], "finding": finding, "oracle_price": px,
                     "moved_1d": (p0 != p1) if (p0 and p1) else None, "moved_7d": (p0 != p7) if (p0 and p7) else None,
                     "existed_7d_ago": p7 is not None, "feeds": [{"address": f, **feed_info[f]} for f in fds], "vaults": vaults,
                     "meta_oracle": meta,
                     "uniswap_price": mkt, "gap_to_uniswap": round(gap, 4) if gap is not None else None,
                     "supply_usd": st.get("supplyAssetsUsd") or 0, "borrow_usd": st.get("borrowAssetsUsd") or 0})
    rows.sort(key=lambda r: (ORDER.index(r["finding"]), -r["borrow_usd"]))
    by = defaultdict(lambda: {"markets": 0, "supply_usd": 0.0, "borrow_usd": 0.0})
    for r in rows:
        b = by[r["finding"]]
        b["markets"] += 1; b["supply_usd"] += r["supply_usd"]; b["borrow_usd"] += r["borrow_usd"]
    out = {"ts": now, "block": head, "markets": len(rows), "by_finding": {k: by[k] for k in ORDER if k in by}, "items": rows}
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(json.dumps(out, indent=1))
    return out


def publish(doc: dict) -> str:
    """Put an audit in the record: ledger/oracle_audits/<day>_<hash>.json, named by its content."""
    from . import ledger
    d = settings.LEDGER_DIR / "oracle_audits"
    d.mkdir(parents=True, exist_ok=True)
    doc = {"kind": "oracle_audit", "chain_id": settings.CHAIN["chain"]["id"], **doc}
    h = ledger.sha(doc)
    (d / f"{time.strftime('%Y-%m-%d', time.gmtime(doc['ts']))}_{h[:16]}.json").write_bytes(ledger.canonical(doc))
    return h


def main() -> None:
    logging.basicConfig(level=logging.WARNING)
    r = audit()
    publish(r)
    print(f"{r['markets']} markets audited at block {r['block']}")
    for k, b in r["by_finding"].items():
        print(f"  {k:16} {b['markets']:4d} markets   supplied ${b['supply_usd']:>14,.0f}   borrowed ${b['borrow_usd']:>14,.0f}")
    print("largest by borrow, not ok:")
    for x in sorted((x for x in r["items"] if x["finding"] != "ok"), key=lambda x: -x["borrow_usd"])[:12]:
        fd = ", ".join((f["description"] or f["address"][:10]) + ("" if f["in_directory"] else " [unlisted]") for f in x["feeds"]) or "-"
        print(f"  {x['finding']:16} {x['collateral'][:12]:12}/{x['loan'][:6]:6} lltv {x['lltv']:.3f} borrowed ${x['borrow_usd']:>13,.0f}  feeds: {fd[:44]:44} vaults: {len(x['vaults'])}  gap {x['gap_to_uniswap']}")


if __name__ == "__main__":
    main()
