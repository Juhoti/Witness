"""Token check: what Witness's record says about one token, in a few plain lines.

Given an address (or a ticker), it answers from the published record only:
  is it a Robinhood stock token on the verified registry, or something copying one
  have holders been unable to sell it (simulated, from ledger/warnings)
  Witness's price for it and how solid that price is
  if it is a stock token: Chainlink feed, how much can be sold within 5%, pause and halt history
  if Morpho markets lend against it: how their oracles are priced
Every figure is a measurement with its time. Nothing here is advice and nothing is executed; the
input is matched against two patterns and otherwise ignored.
"""
from __future__ import annotations
import json
import re
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STATE, LEDGER, FEED = ROOT / "state", ROOT / "ledger", ROOT / "feed"
ADDRESS = re.compile(r"\b0x[0-9a-fA-F]{40}\b")
TICKER = re.compile(r"^\$?([A-Za-z][A-Za-z0-9.]{0,9})$")
NOTE = "Measurements from the public record, not advice. github.com/Juhoti/Witness"


def _json(p: Path) -> dict:
    try:
        return json.loads(p.read_text())
    except (OSError, ValueError):
        return {}


def _latest(folder: str) -> dict:
    d = LEDGER / folder
    files = sorted(d.glob("*.json"), key=lambda p: p.stat().st_mtime) if d.exists() else []
    return _json(files[-1]) if files else {}


def _age(ts) -> str:
    if not ts:
        return "unknown time"
    m = int((time.time() - ts) / 60)
    return f"{m} min ago" if m < 120 else f"{m // 60} h ago"


def _usd(x) -> str:
    if x is None:
        return "-"
    return f"${x:,.2f}" if x < 100 else f"${x:,.0f}"


def _census(address: str) -> dict:
    try:
        con = sqlite3.connect(f"file:{STATE / 'chain.db'}?mode=ro", uri=True, timeout=5)
        r = con.execute("SELECT kind, symbol, name, logs, first_block FROM contracts WHERE lower(address)=lower(?)", (address,)).fetchone()
        return dict(zip(("kind", "symbol", "name", "logs", "first_block"), r)) if r else {}
    except sqlite3.Error:
        return {}


def _same_symbol(symbol: str) -> int:
    try:
        con = sqlite3.connect(f"file:{STATE / 'chain.db'}?mode=ro", uri=True, timeout=5)
        return con.execute("SELECT count(*) FROM contracts WHERE upper(symbol)=upper(?)", (symbol,)).fetchone()[0]
    except sqlite3.Error:
        return 0


def resolve(text: str) -> tuple[str | None, str | None]:
    """(address, None) for an address, (None, ticker) for a ticker, (None, None) otherwise."""
    m = ADDRESS.search(text or "")
    if m:
        return m.group(0), None
    m = TICKER.match((text or "").strip())
    return (None, m.group(1).upper()) if m else (None, None)


def check(text: str) -> str:
    address, ticker = resolve(text)
    collateral = _json(FEED / "collateral.json")
    stock = {x["address"].lower(): x for x in collateral.get("items", [])}
    by_symbol = {x["symbol"]: x for x in collateral.get("items", [])}
    if ticker and not address:
        x = by_symbol.get(ticker)
        n = _same_symbol(ticker)
        if not x:
            return (f"{ticker}: not a Robinhood stock token on the verified registry."
                    + (f" {n} contracts on the chain use that symbol; send an address to check one." if n else "") + f"\n\n{NOTE}")
        address = x["address"]
    if not address:
        return ("Send a token address (0x...) or a stock ticker such as NVDA, and Witness replies with what its record shows.\n"
                "/watch to get de-peg and risky-market alerts, /unwatch to stop.\n\n" + NOTE)
    a = address.lower()
    lines = [f"Token {address}"]
    cen = _census(address)
    x = stock.get(a)
    card_ts = collateral.get("as_of")
    if x:
        lines.append(f"Robinhood stock token {x['symbol']}, on the verified registry.")
    else:
        fakes = {t["address"].lower(): t for t in _json(STATE / "stock_tokens.json").get("off_beacon", [])}
        lookalike = fakes.get(a)
        name = (cen.get("name") or "")
        sym = (cen.get("symbol") or "")
        if lookalike or "robinhood token" in name.lower() or sym.upper() in by_symbol:
            lines.append(f"WARNING: copies a Robinhood stock token ({sym or '?'} / {name[:40] or '?'}) but is NOT on the verified registry.")
        elif cen:
            lines.append(f"Not a Robinhood stock token. Seen by Witness as {cen.get('kind') or 'unclassified'} {sym}".rstrip() + ".")
        else:
            lines.append("Not a Robinhood stock token, and not yet seen by Witness's census.")
    w = _latest("warnings")
    hit = next((i for i in w.get("items", []) if i["token"].lower() == a), None)
    if hit and hit["finding"] == "sell_blocked":
        lines.append(f"WARNING: holders cannot sell. Every sampled sell failed ({hit['distinct_sellers']} wallets); a transfer into its pool reverts. "
                     f"{(hit['eth_in_pool'] or 0):.2f} ETH sits in the pool. Checked {_age(w.get('ts'))}.")
    elif hit:
        lines.append(f"Caution: every sampled sell failed ({hit['distinct_sellers']} wallets); cause not established.")
    pr = _latest("prices").get("items", {})
    p = next((v for k, v in pr.items() if k.lower() == a), None)
    if p and p.get("price_usdg"):
        solid = ("from one pool only" if p.get("pools") == 1 else f"from {p['pools']} pools")
        extra = f"; 24h median {_usd(p['median_24h'])}" if p.get("median_24h") else ""
        lines.append(f"Witness price {_usd(p['price_usdg'])} USDG, {solid}{extra}.")
    if x:
        feed = "yes" if x.get("chainlink_feed") else "no"
        lines.append(f"Chainlink feed: {feed}. Sellable within 5%: {_usd(x.get('depth_usd_5pct'))}. "
                     f"Pause-free {x.get('pause_free_days') or 0:.0f} days; exchange halt in last 30 days: {'no' if x.get('exchange_halt_free_30d') else 'yes' if x.get('exchange_halt_free_30d') is False else '?'}.")
        if x.get("max_lltv_tier_surviving_worst"):
            lines.append(f"Worst closed-market fall in 5 years {x['worst_closed_fall_5y']:.1%}; the highest loan-to-value that survived it: {x['max_lltv_tier_surviving_worst']:.1%}.")
        lines.append("Passes Witness's listing rails." if x.get("passes_listing_rails") else "Does not pass Witness's listing rails.")
    oracles = _json(FEED / "oracles.json").get("items", [])
    mk = [m for m in oracles if (m.get("collateral") or "").upper() == ((x or {}).get("symbol") or (cen.get("symbol") or "#")).upper() and m.get("borrow_usd", 0) >= 1000]
    for m in mk[:3]:
        lines.append(f"Morpho market at {m['lltv']:.1%}: {_usd(m['borrow_usd'])} borrowed; oracle: {m['reading']}.")
    lines.append(f"Record as of {_age(card_ts)}.\n\n{NOTE}")
    return "\n".join(lines)[:3500]
