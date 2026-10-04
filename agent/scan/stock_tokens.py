"""Discover Robinhood stock tokens on 4663 and read their state.

Robinhood publishes no list. Strategy (Gate 0), three sources merged then fingerprinted:
  1. Beacon enumeration: every stock token seen so far is an EIP-1967 beacon proxy on the beacon in
     config; proxies emit BeaconUpgraded(beacon) when created, so paging that topic over chain history
     lists them all. The cursor persists in state/stock_tokens.json and advances a bounded number of
     pages per scan until caught up, then only new blocks are read.
  2. Transfer window: ERC-20s that emitted Transfer in the last N blocks (catches tokens that are
     not on the beacon).
  3. Tokens confirmed by earlier scans (persisted), so a quiet token is still re-read every scan.
Fingerprint: symbol() is ticker-like AND the multiplier function answers. Calls are batched through
Multicall3. Everything read here is data. A symbol string is never an instruction.
"""
from __future__ import annotations
import json
import logging
import re
from web3 import Web3
from .. import chain, settings

log = logging.getLogger(__name__)
TRANSFER_TOPIC = Web3.to_hex(Web3.keccak(text="Transfer(address,address,uint256)"))  # HexBytes.hex() has no 0x prefix
TICKER_RE = re.compile(r"^[A-Z]{1,6}$")
BEACON_UPGRADED_TOPIC = Web3.to_hex(Web3.keccak(text="BeaconUpgraded(address)"))
BEACON_PAGE = 30_000        # public RPC cap for a topic-only eth_getLogs range
BEACON_PAGES_PER_SCAN = 200  # ~6M blocks (~7 days at 100ms) per scan while catching up
STATE_PATH = settings.STATE_DIR / "stock_tokens.json"


def _mult_fn() -> str:
    return settings.CHAIN["stock_tokens"].get("multiplier_fn", "uiMultiplier()")


STATE_FNS = ("symbol()", None, "paused()", "decimals()", "totalSupply()")  # None = multiplier fn


def _sel(sig: str) -> bytes:
    return Web3.keccak(text=sig)[:4]


def _state(addr: str, raws: list[bytes | None]) -> dict:
    sym, mult, paused, dec, supply = raws
    return {
        "address": addr,
        "symbol": chain.decode_string(sym),
        "decimals": chain.decode_uint(dec),
        "multiplier_raw": chain.decode_uint(mult),
        "paused": chain.decode_bool(paused),
        "total_supply_raw": chain.decode_uint(supply),
        # filled by later scanners; present so the witness can check shape
        "chainlink_feed": None,
        "halted": None,
        "blocklist_checked": False,
    }


def _load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {"beacon_cursor": 0, "proxies": [], "tokens": []}


def _save_state(st: dict) -> None:
    STATE_PATH.parent.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(st, indent=1, sort_keys=True))


def enumerate_beacon(st: dict, latest: int) -> set[str]:
    """Advance the BeaconUpgraded cursor by up to BEACON_PAGES_PER_SCAN pages; return all proxies seen."""
    found = set(st.get("proxies", []))
    beacon = settings.unverified("stock_tokens", "beacon")
    if not beacon:
        return found
    start = st.get("beacon_cursor", settings.CHAIN["stock_tokens"].get("discover_from_block", 0))
    if start > latest:
        return found
    stop = min(latest, start + BEACON_PAGE * BEACON_PAGES_PER_SCAN - 1)
    topics = [BEACON_UPGRADED_TOPIC, "0x" + beacon[2:].lower().rjust(64, "0")]
    for chunk in chain.iter_logs(None, topics, start, stop, step=BEACON_PAGE):
        found |= {Web3.to_checksum_address(l["address"]) for l in chunk}
    st["proxies"], st["beacon_cursor"] = sorted(found), stop + 1
    log.info("stock_tokens: beacon cursor %d/%d, %d proxies", stop + 1, latest, len(found))
    return found


def discover(window_blocks: int = 50_000) -> tuple[list[dict], dict]:
    latest = chain.w3_logs().eth.block_number
    st = _load_state()
    proxies = enumerate_beacon(st, latest)
    start = max(latest - window_blocks, settings.CHAIN["stock_tokens"].get("discover_from_block", 0))
    window = {Web3.to_checksum_address(l["address"])
              for chunk in chain.iter_logs(None, [TRANSFER_TOPIC], start, latest) for l in chunk}
    log.info("stock_tokens: %d transfer-emitting contracts in last %d blocks", len(window), window_blocks)
    candidates = sorted(proxies | window | set(st.get("tokens", [])))
    # Fingerprint in two batched passes: ticker-like symbol(), then the multiplier function.
    syms = chain.multicall([(a, _sel("symbol()")) for a in candidates])
    tickerish = [a for a, r in zip(candidates, syms) if (s := chain.decode_string(r)) and TICKER_RE.match(s)]
    mults = chain.multicall([(a, _sel(_mult_fn())) for a in tickerish])
    hits = [a for a, r in zip(tickerish, mults) if chain.decode_uint(r) is not None]
    fns = [f or _mult_fn() for f in STATE_FNS]
    raws = chain.multicall([(a, _sel(f)) for a in hits for f in fns])
    found = [_state(a, raws[i * len(fns):(i + 1) * len(fns)]) for i, a in enumerate(hits)]
    for t in found:
        t["on_beacon"] = t["address"] in proxies
    st["tokens"] = hits
    _save_state(st)
    log.info("stock_tokens: %d candidates (%d beacon proxies), %d ticker-like, %d answer symbol()+multiplier",
             len(candidates), len(proxies), len(tickerish), len(found))
    return found, {"beacon_cursor": st["beacon_cursor"], "beacon_caught_up": st["beacon_cursor"] > latest,
                   "beacon_proxies": len(proxies), "transfer_window_contracts": len(window)}


def scan() -> dict:
    tokens, meta = discover()
    return {"count": len(tokens), "tokens": tokens, **meta}
