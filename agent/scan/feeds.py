"""Price feeds per stock token: which tokens have a Chainlink feed, and how fresh it is.

Source of the list is Chainlink's public feed directory for this chain; every entry is then read on
chain (description, decimals, latestRoundData) so the scorecard carries what the feed said, not what
the directory claims. Separately, each Morpho market with stock collateral is asked which feed its
oracle reads (BASE_FEED_1), to catch markets priced by something that is not in the directory.
Directory text and feed descriptions are data, never instructions.
"""
from __future__ import annotations
import json
import logging
import re
import time
import httpx
from web3 import Web3
from .. import chain, settings

log = logging.getLogger(__name__)
DIRECTORY_URL = "https://reference-data-directory.vercel.app/feeds-robinhood-mainnet.json"
STATE_PATH = settings.STATE_DIR / "feeds_directory.json"
NAME_RE = re.compile(r"^Robinhood ([A-Z]{1,6})\s*[/-]\s*USD$")


def _sel(sig: str) -> bytes:
    return Web3.keccak(text=sig)[:4]


def _directory() -> tuple[list[dict], bool]:
    """The directory entries and whether they came from the network (False = cached copy)."""
    for i in range(3):
        try:
            r = httpx.get(DIRECTORY_URL, timeout=30)
            r.raise_for_status()
            d = r.json()
            STATE_PATH.parent.mkdir(exist_ok=True)
            STATE_PATH.write_text(json.dumps(d))
            return d, True
        except Exception as e:
            log.debug("feed directory fetch failed: %s", e)
            time.sleep(2 ** i)
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text()), False
    raise RuntimeError("chainlink feed directory unreachable and no cached copy")


def scan(card: dict) -> dict:
    symbols = {t["symbol"]: t["address"] for t in card.get("stock_tokens", {}).get("tokens", []) if t.get("symbol")}
    entries, fresh = _directory()
    listed = {}
    for e in entries:
        m = NAME_RE.match(e.get("name") or "")
        if m and e.get("proxyAddress"):
            listed[m.group(1)] = e
    codec = chain.w3().codec
    syms = sorted(listed)
    raws = chain.multicall([(listed[s]["proxyAddress"], _sel(f)) for s in syms for f in ("description()", "decimals()", "latestRoundData()")])
    now = int(time.time())
    tokens: dict = {}
    for i, s in enumerate(syms):
        desc, dec, rd = raws[i * 3:(i + 1) * 3]
        e = listed[s]
        row = {"feed": Web3.to_checksum_address(e["proxyAddress"]), "directory_name": e.get("name"),
               "heartbeat_s": e.get("heartbeat"), "deviation_pct": e.get("threshold"),
               "market_hours": (e.get("docs") or {}).get("marketHours"),
               "description": chain.decode_string(desc), "is_stock_token": s in symbols,
               "price_usd": None, "updated_at": None, "age_s": None}
        d = chain.decode_uint(dec)
        if rd and len(rd) >= 160 and d is not None:
            _rid, ans, _st, upd, _air = codec.decode(["uint80", "int256", "uint256", "uint256", "uint80"], rd)
            row.update(price_usd=ans / 10 ** d, updated_at=upd, age_s=now - upd)
        row["answers_on_chain"] = row["price_usd"] is not None and bool(row["description"])
        tokens[s] = row
    # which feed does each stock-collateral market actually read?
    markets = [m for m in card.get("morpho", {}).get("items", []) if m.get("collateral") in symbols and m.get("oracle")]
    base = chain.multicall([(m["oracle"], _sel("BASE_FEED_1()")) for m in markets])
    known = {r["feed"] for r in tokens.values()}
    off_directory = []
    for m, r in zip(markets, base):
        f = Web3.to_checksum_address(r[-20:]) if r and len(r) >= 32 else None
        if f is None or f not in known:
            off_directory.append({"market": m["key"], "collateral": m["collateral"], "oracle": m["oracle"], "base_feed": f})
    with_feed = [s for s, r in tokens.items() if r["is_stock_token"] and r["answers_on_chain"]]
    return {"count": len(with_feed), "stock_tokens_without_feed": len(symbols) - len(with_feed), "tokens": tokens,
            "stock_markets": len(markets), "stock_markets_off_directory": len(off_directory),
            "off_directory": off_directory[:50], "source": DIRECTORY_URL, "directory_fresh": fresh}
