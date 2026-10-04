"""Gap table arithmetic; no network."""
from agent.gap import build, _supply_usd


def test_supply_usd():
    assert _supply_usd({"total_supply_raw": 5 * 10 ** 18, "decimals": 18}, 200.0) == 1000.0
    assert _supply_usd({"total_supply_raw": 5 * 10 ** 18, "decimals": 18}, None) is None
    assert _supply_usd({"total_supply_raw": None, "decimals": 18}, 1.0) is None


def test_new_listing_demand_uses_uniswap_price():
    card = {
        "stock_tokens": {"tokens": [
            {"symbol": "AAPL", "address": "0xA", "total_supply_raw": 10 * 10 ** 18, "decimals": 18},
            {"symbol": "NOPX", "address": "0xB", "total_supply_raw": 10 * 10 ** 18, "decimals": 18},
            {"symbol": "AMZN", "address": "0xC", "total_supply_raw": 10 * 10 ** 18, "decimals": 18},
        ]},
        "morpho": {"items": [{"collateral": "AMZN", "key": "0xm", "utilization": 0.5, "borrow_usd": 1, "supply_usd": 2}]},
        "uniswap": {"prices_usdg": {"0xA": {"price_usdg": 250.0, "pool": "0xp"}}},
        "reverts": {"items": []},
    }
    g = build(card)
    by = {c["subject"]: c for c in g["items"] if c["kind"] == "new_listing"}
    assert set(by) == {"AAPL", "NOPX"}            # AMZN is listed, so not a new-listing candidate
    assert by["AAPL"]["demand_usd"] == 2500.0 and by["AAPL"]["evidence"]["price_pool"] == "0xp"
    assert by["NOPX"]["demand_usd"] is None        # no USDG pool -> no price -> no guess
    assert g["items"][0]["subject"] == "AAPL"      # priced demand sorts first
    assert g["proposals"] == 0
