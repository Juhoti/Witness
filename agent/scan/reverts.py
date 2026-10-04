"""Reverted transactions grouped by target and reason: what people tried and could not do.

Blockscout v2 has no status filter, so we page recent validated transactions (newest first) and keep
the ones with status "error". Robinhood Chain does ~10 blocks/s, so `pages` bounds how far back a scan
looks; cluster counts are per-scan samples, not totals. Requests carry the account API key as
x-api-key: unkeyed requests are answered by a Cloudflare challenge page (HTTP 403).
"""
from __future__ import annotations
import logging
import httpx
from collections import defaultdict
from .. import settings

log = logging.getLogger(__name__)


def _reason(tx: dict) -> str:
    """Best available revert reason. Blockscout gives a dict {raw, decoded?} once internal txs are
    indexed; until then `result` is "awaiting_internal_transactions", which we label as pending."""
    rr = tx.get("revert_reason")
    if isinstance(rr, dict):
        rr = rr.get("decoded") or rr.get("raw")
    if rr:
        return str(rr)
    res = tx.get("result")
    if res == "awaiting_internal_transactions":
        return "pending_reason"
    return str(res or "unknown")


def scan(pages: int = 40) -> dict:
    base = settings.BLOCKSCOUT_API.rstrip("/")
    headers = {"x-api-key": settings.BLOCKSCOUT_API_KEY} if settings.BLOCKSCOUT_API_KEY else {}
    clusters: dict[tuple, dict] = defaultdict(lambda: {"count": 0, "example": None})
    params: dict = {"filter": "validated"}
    seen = failed = 0
    oldest_block = None
    try:
        with httpx.Client(timeout=30, headers=headers) as client:
            for _ in range(pages):
                r = client.get(f"{base}/transactions", params=params)
                r.raise_for_status()
                j = r.json()
                for tx in j.get("items", []):
                    seen += 1
                    oldest_block = tx.get("block_number", oldest_block)
                    if tx.get("status") != "error":
                        continue
                    failed += 1
                    to = (tx.get("to") or {}).get("hash")
                    reason = _reason(tx)
                    key = (to, str(reason)[:80])
                    c = clusters[key]
                    c["count"] += 1
                    c["example"] = c["example"] or tx.get("hash")
                nxt = j.get("next_page_params")
                if not nxt:
                    break
                params = {"filter": "validated", **nxt}
    except Exception as e:
        log.warning("reverts scan failed: %s", e)
        return {"clusters": 0, "items": [], "error": str(e)}
    items = [{"to": k[0], "reason": k[1], **v} for k, v in clusters.items()]
    items.sort(key=lambda x: -x["count"])
    return {"clusters": len(items), "items": items[:50], "sampled_txs": seen, "failed_txs": failed,
            "oldest_block_sampled": oldest_block}
