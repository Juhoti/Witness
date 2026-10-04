"""Gate 0c: borrowed priors, assembled once into vault/priors/ and refreshed on demand.

These are NOT outcomes on Robinhood Chain. They are what comparable systems did elsewhere, kept so
the session-risk and halt reasoning has something to stand on before 4663 has its own history.
Every note says so in its first line and names its source URL and retrieval time.

  morpho_bad_debt   realized bad debt per market on Morpho Blue, Ethereum / Base / Arbitrum (Morpho API)
  stock_gaps        overnight and weekend open-vs-previous-close gaps for the underlying tickers,
                    five years of daily bars (Yahoo Finance chart API)
  trading_halts     US equity trading halts since 2019 by reason, exchange and year, and per ticker
                    (NYSE historical halts download, which covers all US listing venues)

    python -m agent.priors [--years 5] [--only morpho_bad_debt|stock_gaps|trading_halts]
"""
from __future__ import annotations
import argparse
import csv
import io
import json
import logging
import statistics
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
import httpx
from . import settings, memory

log = logging.getLogger("priors")
PRIORS = memory.VAULT / "priors"
UA = {"User-Agent": "Mozilla/5.0 (Macintosh) witness-priors/0.1"}
DISCLAIMER = ("**PRIOR, not a 4663 outcome.** Borrowed from other venues so the reasoning has a base rate; "
              "never cite it as something that happened on Robinhood Chain.")


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _write(name: str, meta: dict, body: str, data: dict) -> None:
    PRIORS.mkdir(parents=True, exist_ok=True)
    (PRIORS / f"{name}.json").write_text(json.dumps(data, indent=1, sort_keys=True, default=str))
    memory.upsert("priors", name, meta, f"{DISCLAIMER}\n\n{body}\n\nMachine-readable: `vault/priors/{name}.json`", ["prior"])


def _q(xs: list[float], p: float) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    return s[min(len(s) - 1, int(round(p * (len(s) - 1))))]


def _tickers() -> list[str]:
    cards = sorted(settings.SCORECARD_DIR.glob("*.json"), key=lambda p: p.stat().st_mtime)
    for p in reversed(cards):
        c = json.loads(p.read_bytes())
        toks = c.get("stock_tokens", {}).get("tokens") or []
        if toks and not c.get("backfilled"):
            return sorted({t["symbol"] for t in toks if t.get("symbol")})
    return []


# --- 1. Morpho bad debt -----------------------------------------------------------------------
MORPHO_Q = """query($first:Int!,$skip:Int!){ markets(where:{chainId_in:[1,8453,42161]}, first:$first, skip:$skip){
  pageInfo{countTotal} items{ marketId chain{id} lltv creationTimestamp collateralAsset{symbol} loanAsset{symbol}
  realizedBadDebt{usd} badDebt{usd} state{supplyAssetsUsd} } } }"""
CHAIN_NAMES = {1: "Ethereum", 8453: "Base", 42161: "Arbitrum One"}


def morpho_bad_debt() -> None:
    url = settings.CHAIN["morpho"].get("graphql", "https://blue-api.morpho.org/graphql")
    items: list = []
    while True:
        r = httpx.post(url, json={"query": MORPHO_Q, "variables": {"first": 200, "skip": len(items)}}, timeout=60)
        r.raise_for_status()
        pg = r.json()["data"]["markets"]
        items += pg["items"]
        if not pg["items"] or len(items) >= pg["pageInfo"]["countTotal"]:
            break
        time.sleep(0.3)
    retrieved = _now()

    def usd(x):
        v = (x or {}).get("usd") or 0
        return v if 0 < v < 1e9 else 0.0  # the API's USD conversion is visibly broken on a few dead markets

    per_chain, lines = {}, []
    for cid, cname in CHAIN_NAMES.items():
        ms = [m for m in items if m["chain"]["id"] == cid]
        supply = sum((m["state"] or {}).get("supplyAssetsUsd") or 0 for m in ms)
        hit = [(usd(m["realizedBadDebt"]), m) for m in ms if usd(m["realizedBadDebt"]) > 0]
        hit.sort(key=lambda t: -t[0])
        lltvs = [int(m["lltv"]) / 1e18 for _, m in hit]
        per_chain[cname] = {
            "markets": len(ms), "supply_usd": supply,
            "markets_with_realized_bad_debt": len(hit), "realized_bad_debt_usd": sum(v for v, _ in hit),
            "realized_bad_debt_bps_of_supply": (sum(v for v, _ in hit) / supply * 1e4) if supply else None,
            "median_lltv_of_affected_markets": statistics.median(lltvs) if lltvs else None,
            "top": [{"market": f"{(m['collateralAsset'] or {}).get('symbol')}/{m['loanAsset']['symbol']}", "lltv": int(m["lltv"]) / 1e18,
                     "realized_bad_debt_usd": v, "supply_usd_now": (m["state"] or {}).get("supplyAssetsUsd")} for v, m in hit[:10]],
        }
        c = per_chain[cname]
        lines.append(f"| {cname} | {c['markets']} | ${c['supply_usd']:,.0f} | {c['markets_with_realized_bad_debt']} | "
                     f"${c['realized_bad_debt_usd']:,.0f} | {c['realized_bad_debt_bps_of_supply'] or 0:.2f} bps |")
    body = (f"Source: Morpho API `{url}` (`markets.realizedBadDebt`), retrieved {retrieved}. {len(items)} markets.\n\n"
            "| chain | markets | supply now | markets with realized bad debt | realized bad debt | share of supply |\n|---|---|---|---|---|---|\n"
            + "\n".join(lines) +
            "\n\nReading: realized bad debt on Morpho Blue is concentrated in long-tail collateral at 0.77-0.92 LLTV that was "
            "later abandoned (supply now near zero), not in blue-chip markets. Unrealized `badDebt` is not used: its USD "
            "conversion is unreliable on dead markets.\n\nUse for: a base rate for \"what fraction of markets ever realize "
            "bad debt\" and \"at what LLTV\". Not for: anything about stock-token collateral, which has no history here.")
    _write("morpho_bad_debt", {"source": url, "retrieved": retrieved, "markets": len(items)}, body, {"retrieved": retrieved, "source": url, "per_chain": per_chain})
    log.info("morpho_bad_debt: %d markets", len(items))


# --- 2. Overnight and weekend gaps -----------------------------------------------------------
def _yahoo_daily(sym: str, years: int) -> list[tuple[int, float, float]] | None:
    r = httpx.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}", params={"range": f"{years}y", "interval": "1d"}, headers=UA, timeout=30)
    if r.status_code != 200:
        return None
    res = (r.json().get("chart") or {}).get("result") or []
    if not res:
        return None
    q = res[0]["indicators"]["quote"][0]
    out = []
    for ts, o, c in zip(res[0]["timestamp"], q["open"], q["close"]):
        if o and c:
            out.append((ts, o, c))
    return out


def stock_gaps(years: int) -> None:
    tickers = _tickers()
    if not tickers:
        sys.exit("no live scorecard with stock tokens yet; run a scan first")
    retrieved = _now()
    per, pooled_on, pooled_we, failed = {}, [], [], []
    for sym in tickers:
        try:
            bars = _yahoo_daily(sym, years)
        except Exception as e:
            bars = None
            log.warning("%s: %s", sym, e)
        if not bars or len(bars) < 60:
            failed.append(sym)
            continue
        on, we = [], []
        for (t0, _o0, c0), (t1, o1, _c1) in zip(bars, bars[1:]):
            g = o1 / c0 - 1
            (we if (t1 - t0) >= 2.5 * 86400 else on).append(g)
        ao, aw = [abs(x) for x in on], [abs(x) for x in we]
        per[sym] = {"bars": len(bars), "first": datetime.fromtimestamp(bars[0][0], timezone.utc).date().isoformat(),
                    "overnight": {"n": len(on), "median_abs": _q(ao, .5), "p95_abs": _q(ao, .95), "p99_abs": _q(ao, .99), "max_abs": max(ao) if ao else None, "worst_down": min(on) if on else None},
                    "weekend": {"n": len(we), "median_abs": _q(aw, .5), "p95_abs": _q(aw, .95), "p99_abs": _q(aw, .99), "max_abs": max(aw) if aw else None, "worst_down": min(we) if we else None,
                                "share_over_5pct": (sum(1 for x in aw if x > .05) / len(aw)) if aw else None,
                                "share_over_10pct": (sum(1 for x in aw if x > .10) / len(aw)) if aw else None}}
        pooled_on += ao; pooled_we += aw
        time.sleep(0.4)
    pooled = {k: {"n": len(v), "median_abs": _q(v, .5), "p95_abs": _q(v, .95), "p99_abs": _q(v, .99), "p999_abs": _q(v, .999),
                  "share_over_5pct": sum(1 for x in v if x > .05) / len(v) if v else None,
                  "share_over_10pct": sum(1 for x in v if x > .10) / len(v) if v else None}
              for k, v in (("overnight", pooled_on), ("weekend", pooled_we))}
    worst = sorted(per.items(), key=lambda kv: -(kv[1]["weekend"]["p99_abs"] or 0))[:15]
    body = (f"Source: Yahoo Finance chart API (`query1.finance.yahoo.com/v8/finance/chart/<symbol>`, daily bars, {years}y), "
            f"retrieved {retrieved}. Tickers are the stock-token symbols from the latest live scorecard; {len(per)} resolved, "
            f"{len(failed)} did not ({', '.join(failed) or 'none'}).\n\n"
            "Gap = next session's open / previous close - 1. \"Weekend\" = any gap spanning >= 2.5 calendar days (weekends and "
            "holidays), when the stock-token's Chainlink feed is frozen and the vault cannot liquidate.\n\n"
            "| pooled | n | median | p95 | p99 | p99.9 | > 5% | > 10% |\n|---|---|---|---|---|---|---|---|\n"
            + "\n".join(f"| {k} | {v['n']} | {v['median_abs']:.2%} | {v['p95_abs']:.2%} | {v['p99_abs']:.2%} | {v['p999_abs']:.2%} | {v['share_over_5pct']:.2%} | {v['share_over_10pct']:.2%} |" for k, v in pooled.items()) +
            "\n\nWidest weekend tails (p99 of |gap|):\n\n| ticker | weekend p99 | weekend max | worst down | n |\n|---|---|---|---|---|\n"
            + "\n".join(f"| {s} | {d['weekend']['p99_abs']:.2%} | {d['weekend']['max_abs']:.2%} | {d['weekend']['worst_down']:.2%} | {d['weekend']['n']} |" for s, d in worst) +
            "\n\nUse for: sizing session rules (how far a collateral price can move while the market is closed, per ticker). "
            "Earnings dates drive the tail; a per-ticker rule should read the issuer calendar (Gate 5, item 4).")
    _write("stock_gaps", {"source": "yahoo finance chart api", "retrieved": retrieved, "years": years, "tickers": len(per)}, body,
           {"retrieved": retrieved, "years": years, "pooled": pooled, "per_ticker": per, "unresolved": failed})
    log.info("stock_gaps: %d tickers, %d unresolved", len(per), len(failed))


# --- 3. Trading halts ----------------------------------------------------------------------------
HALTS_URL = "https://www.nyse.com/api/trade-halts/historical/download"


def trading_halts() -> None:
    r = httpx.get(HALTS_URL, headers=UA, timeout=120)
    r.raise_for_status()
    retrieved = _now()
    rows = list(csv.DictReader(io.StringIO(r.text)))
    for x in rows:
        x["Reason"] = (x.get("Reason") or "").strip().lower()
    dates = sorted(x["Halt Date"] for x in rows if x.get("Halt Date"))
    by_year: dict[str, Counter] = defaultdict(Counter)
    for x in rows:
        by_year[x["Halt Date"][:4]][x["Reason"] or "unknown"] += 1
    exch = Counter(x["Exchange"] for x in rows)
    tickers = set(_tickers())
    last12 = [x for x in rows if x["Halt Date"] >= (datetime.now(timezone.utc).date().replace(year=datetime.now(timezone.utc).year - 1)).isoformat()]
    per_ticker = {s: {"halts_12m": 0, "luld_12m": 0, "news_12m": 0, "halts_all": 0} for s in tickers}
    for x in rows:
        s = x["Symbol"]
        if s in per_ticker:
            per_ticker[s]["halts_all"] += 1
    for x in last12:
        s = x["Symbol"]
        if s in per_ticker:
            per_ticker[s]["halts_12m"] += 1
            if "luld" in x["Reason"]:
                per_ticker[s]["luld_12m"] += 1
            if "news" in x["Reason"]:
                per_ticker[s]["news_12m"] += 1
    halted = sum(1 for v in per_ticker.values() if v["halts_12m"])
    by_year_rows = "\n".join(f"| {y} | {sum(c.values())} | {c.get('luld pause', 0)} | {c.get('news pending', 0)} | {sum(v for k, v in c.items() if k not in ('luld pause', 'news pending'))} |" for y, c in sorted(by_year.items()))
    top = sorted(per_ticker.items(), key=lambda kv: -kv[1]["halts_12m"])[:15]
    body = (f"Source: NYSE historical trading-halts download `{HALTS_URL}` (covers all US listing venues: "
            f"{', '.join(f'{k} {v}' for k, v in exch.most_common(5))}), retrieved {retrieved}. {len(rows)} halts, {dates[0]} to {dates[-1]}.\n\n"
            "| year | halts | LULD pauses | news pending | other |\n|---|---|---|---|---|\n" + by_year_rows +
            f"\n\nFor the {len(tickers)} stock-token tickers: {halted} had at least one halt in the trailing 12 months "
            f"({halted / len(tickers):.0%}); {sum(v['halts_12m'] for v in per_ticker.values())} halts in total.\n\n"
            "| ticker | halts 12m | LULD | news | all-time |\n|---|---|---|---|---|\n"
            + "\n".join(f"| {s} | {v['halts_12m']} | {v['luld_12m']} | {v['news_12m']} | {v['halts_all']} |" for s, v in top if v["halts_12m"]) +
            "\n\nReading: LULD pauses are minutes long and clear on their own; news-pending halts can last hours and are the ones "
            "that matter for a frozen feed. Use for: the \"30 halt-free days\" rail and the halt-coverage fund (Gate 5, item 8). "
            "A Robinhood-side pause of the token (beacon `paused()`) is a different event and is read live by the scanner.")
    _write("trading_halts", {"source": HALTS_URL, "retrieved": retrieved, "halts": len(rows), "from": dates[0], "to": dates[-1]}, body,
           {"retrieved": retrieved, "source": HALTS_URL, "range": [dates[0], dates[-1]], "by_year": {y: dict(c) for y, c in by_year.items()},
            "by_exchange": dict(exch), "per_ticker": per_ticker})
    log.info("trading_halts: %d rows", len(rows))


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=int, default=5)
    ap.add_argument("--only", choices=["morpho_bad_debt", "stock_gaps", "trading_halts"])
    a = ap.parse_args()
    if a.only in (None, "morpho_bad_debt"):
        morpho_bad_debt()
    if a.only in (None, "trading_halts"):
        trading_halts()
    if a.only in (None, "stock_gaps"):
        stock_gaps(a.years)


if __name__ == "__main__":
    main()
