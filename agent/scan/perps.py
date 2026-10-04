"""Perp funding and open interest per stock-token market (Lighter/Arcus). Idle until endpoints are set."""
from __future__ import annotations
from .. import settings


def scan() -> dict:
    if not settings.CHAIN["perps"].get("lighter_api"):
        return {"markets": 0, "items": [], "note": "perps.lighter_api not set; scanner idle"}
    return {"markets": 0, "items": [], "note": "not implemented"}
