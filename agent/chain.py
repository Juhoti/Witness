"""Thin read-only web3 helpers with a fallback RPC. No signing anywhere in this module."""
from __future__ import annotations
import logging
import time
from requests.exceptions import HTTPError, ConnectionError, Timeout, ChunkedEncodingError
from web3 import Web3
from web3.exceptions import Web3Exception
from . import settings

log = logging.getLogger(__name__)

_w3: dict[str, Web3] = {}

def _connect(url: str) -> Web3:
    if url not in _w3:
        w = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 30}))
        cid = _retry(lambda: w.eth.chain_id)
        if cid != settings.CHAIN["chain"]["id"]:
            raise RuntimeError(f"RPC chain id {cid} != 4663; refusing to scan the wrong chain")
        _w3[url] = w
    return _w3[url]

def w3() -> Web3:
    """Primary endpoint: eth_call, multicall, block numbers."""
    return _connect(settings.RPC_URL)

def w3_logs() -> Web3:
    """Endpoint for bulk eth_getLogs; see settings.RPC_URL_LOGS."""
    return _connect(settings.RPC_URL_LOGS)

def call(to: str, selector_sig: str, abi_out: list[str] | None = None, args_data: bytes = b""):
    """eth_call with a selector; returns raw bytes (decode at the call site). Returns None on revert."""
    sel = Web3.keccak(text=selector_sig)[:4]
    try:
        return _retry(lambda: w3().eth.call({"to": Web3.to_checksum_address(to), "data": sel + args_data}))
    except (Web3Exception, ValueError) as e:
        log.debug("call %s %s reverted: %s", to, selector_sig, settings.redact(str(e)))
        return None

def decode_string(raw: bytes | None) -> str | None:
    if not raw or len(raw) < 64:
        return None
    try:
        return w3().codec.decode(["string"], raw)[0]
    except Exception:
        return None

def decode_uint(raw: bytes | None) -> int | None:
    if not raw or len(raw) < 32:
        return None
    return int.from_bytes(raw[:32], "big")

def decode_bool(raw: bytes | None) -> bool | None:
    u = decode_uint(raw)
    return None if u is None else bool(u)

TRANSIENT = (ConnectionError, Timeout, ChunkedEncodingError)


def _retry(fn, tries: int = 6):
    """Run fn, backing off on HTTP 429 and on dropped or timed-out connections; public RPCs do both."""
    for i in range(tries):
        try:
            return fn()
        except HTTPError as e:
            if e.response is None or e.response.status_code != 429 or i == tries - 1:
                raise
            time.sleep(min(2 ** i, 30))
        except TRANSIENT as e:
            if i == tries - 1:
                raise
            log.debug("transient rpc error (%s); retrying", settings.redact(str(e))[:80])
            time.sleep(min(2 ** i, 30))

def head() -> int:
    """Latest block on the logs endpoint. Retried: it is the first call after the loop's sleep, when
    the server has usually closed the idle keep-alive connection."""
    return _retry(lambda: w3_logs().eth.block_number)

def iter_logs(address: str | None, topics: list, from_block: int, to_block: int | str = "latest", step: int = 20_000, min_step: int = 10):
    """Chunked eth_getLogs, one chunk at a time; public RPCs cap ranges and result counts aggressively."""
    latest = head() if to_block == "latest" else to_block
    start = from_block
    while start <= latest:
        end = min(start + step - 1, latest)
        params = {"fromBlock": start, "toBlock": end, "topics": topics}
        if address:
            params["address"] = Web3.to_checksum_address(address)
        try:
            chunk = _retry(lambda: w3_logs().eth.get_logs(params))
        except (Web3Exception, ValueError, HTTPError) as e:
            if step <= min_step:
                raise
            log.debug("get_logs %s-%s failed (%s); halving step", start, end, settings.redact(str(e)))
            step = max(step // 2, min_step)
            continue
        yield chunk
        start = end + 1

def get_logs(address: str | None, topics: list, from_block: int, to_block: int | str = "latest", step: int = 20_000):
    return [l for chunk in iter_logs(address, topics, from_block, to_block, step) for l in chunk]


def multicall(calls: list[tuple[str, bytes]], batch: int = 300, block: int | None = None) -> list[bytes | None]:
    """Batch read calls through Multicall3.aggregate3 with allowFailure; None where a call reverted.

    Read-only: aggregate3 is a view aggregator and nothing here is signed. Falls back to smaller
    batches if the RPC rejects a chunk (gas cap or payload size)."""
    mc = settings.CHAIN["chain"].get("multicall3", "0xcA11bde05977b3631167028862bE2a173976CA11")
    sel = Web3.keccak(text="aggregate3((address,bool,bytes)[])")[:4]
    out: list[bytes | None] = []
    for i in range(0, len(calls), batch):
        chunk = [(Web3.to_checksum_address(a), True, d) for a, d in calls[i:i + batch]]
        data = sel + w3().codec.encode(["(address,bool,bytes)[]"], [chunk])
        try:
            raw = _retry(lambda: w3().eth.call({"to": Web3.to_checksum_address(mc), "data": data}, block_identifier=block or "latest"))
        except (Web3Exception, ValueError, HTTPError) as e:
            if batch <= 20:
                raise
            log.debug("multicall batch of %d failed (%s); halving", batch, settings.redact(str(e)))
            out.extend(multicall(calls[i:i + batch], batch // 2, block))
            continue
        for ok, ret in w3().codec.decode(["(bool,bytes)[]"], raw)[0]:
            out.append(bytes(ret) if ok else None)
    return out


def block_at(ts: int, lo: int = 0, hi: int | None = None) -> int:
    """First block whose timestamp is >= ts (bisection over eth_getBlockByNumber)."""
    w = w3()
    hi = _retry(lambda: w.eth.block_number) if hi is None else hi
    while lo < hi:
        mid = (lo + hi) // 2
        if _retry(lambda: w.eth.get_block(mid))["timestamp"] < ts:
            lo = mid + 1
        else:
            hi = mid
    return lo
