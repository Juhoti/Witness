"""The scoreboard: the record, made readable.

One static page built from what is already published: the latest scorecard, the oracle audit, the
opportunity map, the census, Witness's prices and the gate count. No figure on it is computed here
that is not in those sources; the page says which scorecard and block each section comes from, and
ends with how to check the record yourself. Every string that came from the chain is escaped.

    python -m agent.scoreboard      # writes state/scoreboard.html
"""
from __future__ import annotations
import html
import json
import time
from . import census, gate, grade, opportunity, settings

OUT = settings.STATE_DIR / "scoreboard.html"
FINDING_LABEL = {"oracle_constant": ("critical", "Fixed price"), "oracle_frozen": ("critical", "Frozen 7 days"),
                 "oracle_vault_rate_only": ("serious", "Prices itself"), "oracle_unlisted_feed": ("warning", "Feed not in Chainlink's list"),
                 "oracle_stale_feed": ("warning", "Stale feed"), "oracle_off_market": ("warning", "Off market"), "oracle_no_oracle": ("serious", "No oracle")}
GAP_LABEL = {"lending_capacity": "Borrowers want more than is supplied", "unlisted_collateral": "No lending market exists",
             "missing_price_feed": "No Chainlink price feed", "missing_depth": "Too thin to liquidate"}
KIND_LABEL = {"erc20": "Other tokens", "stablecoin": "USDG", "wrapped_native": "WETH", "stock_token": "Stock tokens",
              "uniswap_v4_pool_manager": "Uniswap v4", "uniswap_v3_pool": "Uniswap v3 pools", "erc721": "NFTs", "unknown": "Not yet named",
              "unprobed": "Not yet examined", "price_feed": "Price feeds", "v2_style_pool": "v2-style pools", "erc4626_vault": "Vaults",
              "morpho_blue": "Morpho", "stock_token_like": "Stock-token lookalikes"}
ICON = {"critical": "✕", "serious": "▲", "warning": "!", "good": "✓"}

CSS = """
/* layout: one reading column; a header that names the record, four figures, then tables in order of what needs attention */
:root{--bg:#f7f8fa;--surface:#ffffff;--ink:#11161d;--ink-2:#4a5563;--ink-3:#7b8694;--rule:#dfe3e9;--accent:#2a78d6;--bar:#2a78d6;--bar-quiet:#b9c2cf;
--good:#0ca30c;--warning:#fab219;--serious:#ec835a;--critical:#d03b3b;--chip:#eef1f5;
--sans:"IBM Plex Sans",system-ui,-apple-system,"Segoe UI",sans-serif;--cond:"IBM Plex Sans Condensed","IBM Plex Sans",system-ui,sans-serif;--mono:"IBM Plex Mono",ui-monospace,Menlo,monospace}
@media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--bg:#101418;--surface:#171c22;--ink:#f2f4f7;--ink-2:#b4bdc9;--ink-3:#8590a0;--rule:#2a323c;--accent:#5a9ff0;--bar:#3987e5;--bar-quiet:#465160;--chip:#222a33;color-scheme:dark}}
:root[data-theme="dark"]{--bg:#101418;--surface:#171c22;--ink:#f2f4f7;--ink-2:#b4bdc9;--ink-3:#8590a0;--rule:#2a323c;--accent:#5a9ff0;--bar:#3987e5;--bar-quiet:#465160;--chip:#222a33;color-scheme:dark}
body{background:var(--bg);color:var(--ink);font-family:var(--sans);font-size:15px;line-height:1.5}
.wrap{max-width:1040px;margin:0 auto;padding-inline:20px;padding-block:28px 56px;display:flex;flex-direction:column;gap:36px}
header{display:flex;flex-direction:column;gap:10px;border-bottom:2px solid var(--ink);padding-bottom:18px}
h1{font-family:var(--cond);font-weight:600;font-size:clamp(30px,5vw,44px);line-height:1.05;margin:0;letter-spacing:-.01em}
.lede{color:var(--ink-2);max-width:66ch;margin:0}
.stamp{font-family:var(--mono);font-size:12px;color:var(--ink-3);display:flex;flex-wrap:wrap;gap:4px 18px}
h2{font-family:var(--cond);font-weight:600;font-size:22px;margin:0;text-wrap:balance}
.sub{color:var(--ink-2);margin:4px 0 0;max-width:70ch}
section{display:flex;flex-direction:column;gap:14px;min-width:0}
.figures{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:0;border-block:1px solid var(--rule)}
.fig{padding:16px 18px 16px 0;display:flex;flex-direction:column;gap:2px;min-width:0}
.fig+.fig{border-left:1px solid var(--rule);padding-left:18px}
.fig .n{font-family:var(--cond);font-weight:600;font-size:34px;line-height:1.1;font-variant-numeric:tabular-nums}
.fig .l{color:var(--ink-2);font-size:13px}
.scroll{overflow-x:auto;background:var(--surface);border:1px solid var(--rule);border-radius:6px}
table{border-collapse:collapse;width:100%;font-size:14px}
th{font-family:var(--sans);font-weight:500;font-size:11px;letter-spacing:.07em;text-transform:uppercase;color:var(--ink-3);text-align:left;padding:10px 14px;border-bottom:1px solid var(--rule);white-space:nowrap}
td{padding:10px 14px;border-bottom:1px solid var(--rule);vertical-align:top}
tr:last-child td{border-bottom:0}
.num{text-align:right;font-family:var(--mono);font-variant-numeric:tabular-nums;white-space:nowrap}
.mono{font-family:var(--mono);font-size:12.5px;overflow-wrap:anywhere}
.quiet{color:var(--ink-3)}
.pill{display:inline-flex;align-items:center;gap:6px;background:var(--chip);border-radius:999px;padding:2px 10px 2px 4px;font-size:12.5px;white-space:nowrap;color:var(--ink)}
.pill i{font-style:normal;display:inline-grid;place-items:center;width:17px;height:17px;border-radius:50%;font-size:10px;font-weight:700;color:#fff}
.pill.critical i{background:var(--critical)}.pill.serious i{background:var(--serious)}.pill.warning i{background:var(--warning);color:#11161d}.pill.good i{background:var(--good)}
.bars{display:grid;grid-template-columns:minmax(110px,max-content) 1fr max-content;gap:8px 12px;align-items:center;background:var(--surface);border:1px solid var(--rule);border-radius:6px;padding:16px 18px}
.bars .k{font-size:13.5px}.bars .v{font-family:var(--mono);font-size:12.5px;color:var(--ink-2);font-variant-numeric:tabular-nums}
.track{height:14px;min-width:0}.bar{height:100%;background:var(--bar);border-radius:0 4px 4px 0;min-width:2px}
.bar.quiet{background:var(--bar-quiet)}
.meter{height:10px;background:var(--chip);border-radius:5px;overflow:hidden}.meter b{display:block;height:100%;background:var(--accent)}
.two{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:24px}
.note{font-size:13px;color:var(--ink-2);max-width:74ch;margin:0}
code{font-family:var(--mono);font-size:12.5px;background:var(--chip);padding:1px 5px;border-radius:3px}
footer{border-top:1px solid var(--rule);padding-top:16px;color:var(--ink-3);font-size:12.5px}
a{color:var(--accent)}a:focus-visible{outline:2px solid var(--accent);outline-offset:2px}
@media (max-width:520px){.fig+.fig{border-left:0;padding-left:0;border-top:1px solid var(--rule)}.bars{grid-template-columns:1fr max-content}.bars .track{grid-column:1/-1;order:3}}
"""


def e(x) -> str:
    return html.escape(str(x if x is not None else "—"))


def usd(x) -> str:
    if x is None:
        return "—"
    x = float(x)
    return f"${x / 1e6:,.1f}M" if abs(x) >= 1e6 else f"${x / 1e3:,.0f}k" if abs(x) >= 1e4 else f"${x:,.0f}"


def _latest(name: str) -> dict:
    d = settings.LEDGER_DIR / name
    return opportunity._latest(d) or {} if d.exists() else {}


def build() -> str:
    card = opportunity._latest(settings.SCORECARD_DIR, live_only=True) or {}
    opp, prices = _latest("opportunities"), _latest("prices")
    g = gate.status()
    try:
        cen = census.report()
    except Exception:
        cen = {"by_kind": [], "logs": 0, "coverage_by_contract_kind": None, "coverage_by_named_event": None, "blocks": [0, 0], "contracts": 0}
    st, feeds, depth = card.get("stock_tokens", {}), card.get("feeds", {}), (card.get("depth", {}).get("tokens") or {})
    risks, gaps, unexplained = opp.get("risks", []), opp.get("gaps", []), opp.get("unexplained", [])
    risk_usd = sum(r["size_usd"] for r in risks)
    stamp = time.strftime("%Y-%m-%d %H:%M UTC", time.gmtime(card.get("ts", 0)))
    deep = sum(1 for d in depth.values() if d.get("depth_usd_5pct", 0) >= 100_000)
    rec = grade.record()

    figures = [(f"{st.get('count', '—')}", "stock tokens on the verified registry"),
               (f"{(cen['coverage_by_contract_kind'] or 0):.0%}", "of chain activity Witness can name"),
               (usd(risk_usd), "borrowed against prices that would not see a fall"),
               (f"{feeds.get('count', '—')} / {st.get('count', '—')}", "stock tokens with a Chainlink feed")]
    fig_html = "".join(f'<div class="fig"><span class="n">{e(n)}</span><span class="l">{e(l)}</span></div>' for n, l in figures)

    risk_rows = ""
    for r in risks[:10]:
        sev, label = FINDING_LABEL.get(r["kind"], ("warning", r["kind"]))
        pair = r["subject"].rsplit(" ", 1)
        risk_rows += (f'<tr><td>{e(pair[0])}<div class="mono quiet">{e(pair[-1])}…</div></td>'
                      f'<td><span class="pill {sev}"><i aria-hidden="true">{ICON[sev]}</i>{e(label)}</span></td>'
                      f'<td class="num">{usd(r["size_usd"])}</td><td class="quiet">{e(r["measured"].split(";", 1)[-1].strip())}</td></tr>')

    gap_rows = ""
    for x in [x for x in gaps if not str(x.get("size_confidence", "ok")).startswith("low")][:12]:
        blocked = ", ".join(x.get("blocked_by") or []) or "nothing measured"
        gap_rows += (f'<tr><td>{e(x["subject"])}</td><td>{e(GAP_LABEL.get(x["kind"], x["kind"]))}</td>'
                     f'<td class="num">{usd(x["size_usd"])}</td><td class="quiet">{e(x.get("served_by"))}</td><td class="quiet">{e(blocked)}</td></tr>')

    kinds = sorted(cen["by_kind"], key=lambda k: -k["share"])
    top = [k for k in kinds if k["share"] >= 0.015]
    rest = sum(k["share"] for k in kinds if k["share"] < 0.015)
    mx = max([k["share"] for k in top] + [rest, 0.0001])
    bars = ""
    for k in top:
        quiet = " quiet" if k["kind"] in ("unknown", "unprobed") else ""
        bars += (f'<span class="k">{e(KIND_LABEL.get(k["kind"], k["kind"]))}</span><span class="track" title="{k["logs"]:,} logs from {k["contracts"]:,} contracts">'
                 f'<span class="bar{quiet}" style="width:{k["share"] / mx * 100:.1f}%;display:block"></span></span><span class="v">{k["share"]:.1%}</span>')
    if rest:
        bars += f'<span class="k">Everything else named</span><span class="track"><span class="bar" style="width:{rest / mx * 100:.1f}%;display:block"></span></span><span class="v">{rest:.1%}</span>'

    un_rows = "".join(f'<tr><td>{e({"unknown_contract": "Contract", "unnamed_event": "Event type", "failing_intent": "Failing call"}.get(u["kind"], u["kind"]))}</td>'
                      f'<td class="mono">{e(u["subject"][:56])}</td><td class="num">{u["size_share"]:.2%}</td><td class="quiet">{e(u["measured"])}</td></tr>' for u in unexplained[:8])

    pct = min(g["run"] / g["target"], 1.0)
    return f"""<title>Witness Scoreboard</title>
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500&family=IBM+Plex+Sans:wght@400;500;600&family=IBM+Plex+Sans+Condensed:wght@600&display=swap">
<style>{CSS}</style>
<div class="wrap">
<header>
  <h1>Witness</h1>
  <p class="lede">An independent, replayable record of Robinhood Chain: how its markets are priced, what can be lent against safely, and where demand goes unserved. Measured from the chain every few minutes. Witness holds no keys and has nothing to sell.</p>
  <div class="stamp"><span>scan {e(stamp)}</span><span>stock-token state at block {e(st.get('as_of_block'))}</span><span>prices at block {e(prices.get('block'))}</span><span>chain id 4663</span></div>
</header>

<div class="figures">{fig_html}</div>

<section>
  <div><h2>Markets whose price would not see a fall</h2>
  <p class="sub">Every Morpho market reads its collateral price from an oracle. These read one that is fixed, frozen, or outside Chainlink's published list, ranked by what is borrowed against them. An unusual oracle can be a deliberate choice; this is a list of what to look at, not a verdict.</p></div>
  <div class="scroll"><table><thead><tr><th>Market</th><th>Finding</th><th class="num">Borrowed</th><th>Oracle</th></tr></thead><tbody>{risk_rows or '<tr><td colspan="4" class="quiet">No audit yet.</td></tr>'}</tbody></table></div>
</section>

<section>
  <div><h2>Demand nothing serves well</h2>
  <p class="sub">Measured gaps, largest first, with what stands in the way of serving each one safely. Sizes resting on a price too thin to trust are left out.</p></div>
  <div class="scroll"><table><thead><tr><th>Asset</th><th>Gap</th><th class="num">Size</th><th>Served today by</th><th>Blocked by</th></tr></thead><tbody>{gap_rows or '<tr><td colspan="5" class="quiet">No map yet.</td></tr>'}</tbody></table></div>
</section>

<div class="two">
<section>
  <div><h2>What the chain is doing</h2>
  <p class="sub">Share of all event logs by the kind of contract that emitted them, over blocks {cen['blocks'][0]:,}–{cen['blocks'][1]:,} ({cen['logs']:,} logs, {cen['contracts']:,} contracts). Grey is activity not yet named.</p></div>
  <div class="bars" role="img" aria-label="Share of chain activity by contract kind">{bars}</div>
</section>
<section>
  <div><h2>Stock tokens as collateral</h2>
  <p class="sub">What the three listing rails say today.</p></div>
  <div class="scroll"><table><tbody>
    <tr><td>On the verified registry</td><td class="num">{e(st.get('count'))}</td></tr>
    <tr><td>Lookalikes on another beacon, not counted</td><td class="num">{e(st.get('off_beacon_count', 0))}</td></tr>
    <tr><td>With a Chainlink feed</td><td class="num">{e(feeds.get('count'))}</td></tr>
    <tr><td>Can absorb a $100k sale within 5%</td><td class="num">{deep}</td></tr>
    <tr><td>Ever seen paused</td><td class="num">{sum(1 for h in (card.get('pause_history', {}).get('tokens') or {}).values() if h.get('last_paused'))}</td></tr>
    <tr><td>Candidates passing all three rails</td><td class="num">{e(card.get('gap', {}).get('pass_rails'))}</td></tr>
    <tr><td>Witness reference prices published</td><td class="num">{e(prices.get('priced'))}</td></tr>
  </tbody></table></div>
</section>
</div>

<section>
  <div><h2>Not yet explained</h2>
  <p class="sub">The largest things on the chain Witness cannot name. This is the queue it works down; each answer is recorded as a claim with a check, and graded a week later. Claims graded so far: {rec['held'] + rec['failed']} ({rec['held']} held).</p></div>
  <div class="scroll"><table><thead><tr><th>Kind</th><th>Subject</th><th class="num">Share</th><th>Measured</th></tr></thead><tbody>{un_rows or '<tr><td colspan="4" class="quiet">Nothing queued.</td></tr>'}</tbody></table></div>
</section>

<section>
  <div><h2>The loop, unattended</h2>
  <p class="sub">{g['run']} of {g['target']} consecutive scans since the loop last started; {g['scans_with_an_error']} with a scanner error ({g['error_rate']:.1%}). {g['live_scans']:,} live scans in the record, including every failed one.</p></div>
  <div class="meter" role="img" aria-label="{g['run']} of {g['target']} scans"><b style="width:{pct * 100:.1f}%"></b></div>
  <p class="note">Check it yourself: clone the repository and run <code>python -m agent.audit</code>. It recomputes the hash of every published file against its name and walks the chain of entries; it needs no network. Prices here are measurements with their method attached, not an oracle.</p>
</section>

<footer>Built from scorecard at {e(stamp)}. Source and record: github.com/Juhoti/Witness</footer>
</div>
"""


def main() -> None:
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(build())
    print(f"wrote {OUT} ({OUT.stat().st_size:,} bytes)")


if __name__ == "__main__":
    main()
