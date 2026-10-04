"""Gate 0c: replay the scanners over chain history and write dated scorecards marked backfilled.

For each UTC day since the chain's first block (or --from), take the same sample a live scan takes:
  stock_tokens  state of every known beacon proxy at the sample block (historical eth_call via Multicall3)
  morpho        the Morpho API's daily series for that day (markets created later are absent)
  uniswap       swaps in a 30-minute window at --hour UTC (default 15:00, US market open), same
                arithmetic as the live scanner
  reverts       not backfillable (Blockscout has no historical failed-tx query); left empty with a note
  perps         idle, as live
The card carries `backfilled: true`, the sample blocks and the run time. Nothing is judged and no
ledger entry is appended: backfill fills the gap table's history, it does not make decisions.
Idempotent: a day that already has a backfilled scorecard is skipped.

    python -m agent.backfill [--from YYYY-MM-DD] [--to YYYY-MM-DD] [--hour 15]
"""
from __future__ import annotations
import argparse
import calendar
import json
import logging
import sys
import time
from datetime import date, datetime, timedelta, timezone
from . import settings, ledger, gap, chain
from .scan import stock_tokens, morpho, uniswap

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                    handlers=[logging.StreamHandler(sys.stdout), logging.FileHandler(settings.LOG_DIR / "backfill.log")])
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("backfill")
WINDOW_SECONDS = 1800


def day_ts(d: date) -> int:
    return calendar.timegm(d.timetuple())


def backfilled_days() -> set[str]:
    out = set()
    for p in settings.SCORECARD_DIR.glob("*.json"):
        try:
            if json.loads(p.read_bytes()).get("backfilled"):
                out.add(p.name[:10])
        except (ValueError, OSError):
            continue
    return out


def card_for_day(d: date, hour: int, proxies: list[str], hist: list[dict]) -> dict | None:
    start_ts = day_ts(d) + hour * 3600
    b_start = chain.block_at(start_ts)
    b_end = max(chain.block_at(start_ts + WINDOW_SECONDS) - 1, b_start)
    latest = chain.w3().eth.block_number
    if b_start > latest:
        return None  # the chain had not reached this time yet
    card: dict = {"chain_id": settings.CHAIN["chain"]["id"], "ts": start_ts + WINDOW_SECONDS,
                  "backfilled": True, "backfill_run_ts": int(time.time()),
                  "sample": {"day": d.isoformat(), "hour_utc": hour, "block_start": b_start, "block_end": b_end}}
    tokens = stock_tokens.fingerprint(proxies, set(proxies), block=b_end)
    card["stock_tokens"] = {"count": len(tokens), "tokens": tokens, "as_of_block": b_end,
                            "note": "backfill: beacon proxies only, state read at block_end"}
    card["morpho"] = morpho.section_for_day(hist, day_ts(d))
    card["uniswap"] = uniswap.report(b_start, b_end) if b_end > b_start else {"pairs": 0, "items": [], "note": "no blocks in window"}
    card["reverts"] = {"clusters": 0, "items": [], "note": "not backfillable: Blockscout has no historical failed-tx query"}
    card["perps"] = {"markets": 0, "items": [], "note": "perps scanner idle"}
    card["gap"] = gap.build(card)
    return card


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="frm", help="first UTC day (default: day of block 1)")
    ap.add_argument("--to", dest="to", help="last UTC day inclusive (default: yesterday)")
    ap.add_argument("--hour", type=int, default=15, help="UTC hour of the 30-minute sample window")
    a = ap.parse_args()
    today = datetime.now(timezone.utc).date()
    last = date.fromisoformat(a.to) if a.to else today - timedelta(days=1)
    if a.frm:
        first = date.fromisoformat(a.frm)
    else:
        first = datetime.fromtimestamp(chain.w3().eth.get_block(1)["timestamp"], timezone.utc).date()
    proxies = stock_tokens.load_state().get("proxies", [])
    if not proxies:
        sys.exit("state/stock_tokens.json has no beacon proxies; run one live scan first")
    log.info("backfill %s..%s, %d beacon proxies, sample %02d:00-%02d:30 UTC", first, last, len(proxies), a.hour, a.hour)
    hist = morpho.history(day_ts(first), day_ts(last) + 86400)
    log.info("morpho history: %d markets", len(hist))
    done = backfilled_days()
    d = first
    while d <= last:
        if d.isoformat() in done:
            d += timedelta(days=1)
            continue
        t = time.time()
        try:
            card = card_for_day(d, a.hour, proxies, hist)
        except Exception as e:
            log.error("%s failed: %s", d, settings.redact(str(e)))
            time.sleep(10)
            continue  # retry the same day
        if card is None:
            break
        h = ledger.write_scorecard(card, day=d.isoformat())
        log.info("%s %s: tokens=%d morpho=%d pairs=%d swaps=%s candidates=%d (%.0fs)", d, h[:16],
                 card["stock_tokens"]["count"], card["morpho"]["markets"], card["uniswap"].get("pairs", 0),
                 card["uniswap"].get("swaps"), card["gap"]["candidates"], time.time() - t)
        d += timedelta(days=1)


if __name__ == "__main__":
    main()
