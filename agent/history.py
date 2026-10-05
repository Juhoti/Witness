"""Pause history per stock token, kept incrementally so the gap table can check "30 pause-free days".

What is recorded is what the scans saw: the first time a token appeared in a scorecard and the last
time one showed it paused. A pause that started and ended between two scans is not seen. This is the
token's own pause flag on chain; exchange halts of the underlying share are a separate signal (see
vault/priors/trading_halts).

State lives in state/pause_history.json and is rebuilt from ledger/scorecards/ if missing.
"""
from __future__ import annotations
import json
from . import settings

STATE_PATH = settings.STATE_DIR / "pause_history.json"
DAY = 86400


def _apply(state: dict, card: dict) -> None:
    ts = card.get("ts")
    toks = card.get("stock_tokens", {}).get("tokens")
    if not ts or not toks:
        return
    for t in toks:
        sym = t.get("symbol")
        if not sym:
            continue
        h = state.setdefault(sym, {"first_seen": ts, "last_paused": None})
        h["first_seen"] = min(h["first_seen"], ts)
        if t.get("paused") and (h["last_paused"] is None or ts > h["last_paused"]):
            h["last_paused"] = ts


def rebuild() -> dict:
    state: dict = {}
    for p in sorted(settings.SCORECARD_DIR.glob("*.json")):
        try:
            _apply(state, json.loads(p.read_bytes()))
        except (ValueError, OSError):
            continue
    return state


def summarise(state: dict, now: int) -> dict:
    out = {}
    for sym, h in state.items():
        observed = (now - h["first_seen"]) / DAY
        free = observed if h["last_paused"] is None else (now - h["last_paused"]) / DAY
        out[sym] = {"days_observed": round(observed, 2), "pause_free_days": round(min(observed, free), 2),
                    "last_paused": h["last_paused"]}
    return out


def update(card: dict) -> dict:
    """Fold this scan into the state and return the per-token summary for the scorecard."""
    state = json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else rebuild()
    _apply(state, card)
    STATE_PATH.parent.mkdir(exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, sort_keys=True))
    return {"tokens": summarise(state, card["ts"]), "basis": "token pause flag as seen at scan times; backfilled days included"}
