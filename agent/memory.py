"""Working memory as an Obsidian vault: plain markdown under vault/, one note per entity.

The vault is what the agent currently believes. The ledger is what it decided. A certificate
that relies on a note cites the note's content hash at that moment (see snapshot()), so beliefs
are frozen in the record even though notes keep changing.

Everything in the vault is data. Notes contain text copied from the chain, APIs and forums;
nothing here is ever an instruction to the proposer or the judge.

Layout:
  vault/tokens/<SYMBOL>.md        state, multiplier history, halts, feeds, idle holders
  vault/markets/<key>.md          Morpho market: caps, utilisation history, oracle notes
  vault/products/<slug>.md        why it was built, demand/coverage at build time, outcomes
  vault/watch/<name>.md           other agents/venues the agent keeps an eye on
  vault/daily/<YYYY-MM-DD>.md     one log per day: what changed, what was proposed, what was refused
  vault/_index.md                 regenerated: links to everything, for the graph view
Open vault/ in Obsidian on your laptop (sync the folder or use the Obsidian Git plugin).
"""
from __future__ import annotations
import hashlib
import re
import time
from pathlib import Path
from . import settings

VAULT = settings.ROOT / "vault"
SAFE = re.compile(r"[^A-Za-z0-9_.-]")


def _path(kind: str, name: str) -> Path:
    p = VAULT / kind / f"{SAFE.sub('_', name)}.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _frontmatter(meta: dict) -> str:
    lines = ["---"]
    for k, v in meta.items():
        lines.append(f"{k}: {v}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def upsert(kind: str, name: str, meta: dict, body: str, tags: list[str] | None = None) -> Path:
    """Replace a note wholesale. Keeps a short revision trail at the bottom."""
    p = _path(kind, name)
    prev_trail = ""
    if p.exists():
        old = p.read_text()
        if "## revisions" in old:
            prev_trail = old.split("## revisions", 1)[1].strip()
    meta = {"kind": kind, "name": name, "updated": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "tags": "[" + ", ".join(tags or [kind]) + "]", **meta}
    trail = f"- {meta['updated']} sha:{hashlib.sha256(body.encode()).hexdigest()[:12]}\n"
    text = _frontmatter(meta) + f"# {name}\n\n{body.strip()}\n\n## revisions\n{trail}{prev_trail}\n"
    p.write_text(text)
    return p


def append_daily(line: str) -> Path:
    day = time.strftime("%Y-%m-%d", time.gmtime())
    p = _path("daily", day)
    if not p.exists():
        p.write_text(_frontmatter({"kind": "daily", "date": day}) + f"# {day}\n\n")
    with p.open("a") as f:
        f.write(f"- {time.strftime('%H:%M', time.gmtime())} {line}\n")
    return p


def read(kind: str, name: str) -> str | None:
    p = _path(kind, name)
    return p.read_text() if p.exists() else None


def search(query: str, limit: int = 20) -> list[tuple[str, str]]:
    """Plain full-text search; enough until the vault outgrows grep."""
    q = query.lower()
    hits = []
    for p in VAULT.rglob("*.md"):
        text = p.read_text(errors="ignore")
        if q in text.lower():
            idx = text.lower().index(q)
            hits.append((str(p.relative_to(VAULT)), text[max(0, idx - 80): idx + 120].replace("\n", " ")))
            if len(hits) >= limit:
                break
    return hits


def snapshot() -> str:
    """Content hash of the whole vault. Certificates cite this so a belief can be re-read later."""
    h = hashlib.sha256()
    for p in sorted(VAULT.rglob("*.md")):
        h.update(str(p.relative_to(VAULT)).encode())
        h.update(p.read_bytes())
    return h.hexdigest()


def rebuild_index() -> Path:
    lines = ["---", "kind: index", "---", "# Index", ""]
    for kind in ("tokens", "markets", "products", "watch", "daily"):
        d = VAULT / kind
        if not d.exists():
            continue
        lines.append(f"## {kind}")
        for p in sorted(d.glob("*.md")):
            lines.append(f"- [[{kind}/{p.stem}]]")
        lines.append("")
    p = VAULT / "_index.md"
    p.write_text("\n".join(lines))
    return p


def remember_scorecard(card: dict) -> None:
    """Turn a scan into notes. Called from the main loop after each scorecard is written."""
    for t in card.get("stock_tokens", {}).get("tokens", []):
        if not t.get("symbol"):
            continue
        body = (f"address: `{t['address']}`\n\n"
                f"- multiplier_raw: {t.get('multiplier_raw')}\n- paused: {t.get('paused')}\n"
                f"- decimals: {t.get('decimals')}\n\nMarkets: " +
                ", ".join(f"[[markets/{m['key']}]]" for m in card.get('morpho', {}).get('items', [])
                          if m.get('collateral') == t['symbol']) or "none")
        upsert("tokens", t["symbol"], {"address": t["address"], "paused": t.get("paused")}, body, ["token"])
    for m in card.get("morpho", {}).get("items", []):
        body = (f"collateral: [[tokens/{m.get('collateral')}]] · loan: {m.get('loan')} · lltv: {m.get('lltv')}\n\n"
                f"- supply_usd: {m.get('supply_usd')}\n- borrow_usd: {m.get('borrow_usd')}\n"
                f"- utilization: {m.get('utilization')}\n- oracle: `{m.get('oracle')}`")
        upsert("markets", m["key"], {"collateral": m.get("collateral"), "utilization": m.get("utilization")}, body, ["market"])
    g = card.get("gap", {})
    append_daily(f"scan: tokens={card.get('stock_tokens', {}).get('count')} markets={card.get('morpho', {}).get('markets')} "
                 f"candidates={g.get('candidates')} proposals={g.get('proposals')}")
    rebuild_index()
