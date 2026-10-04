"""Backfill day selection and the Morpho day picker; no network."""
from datetime import date
from agent.backfill import day_ts
from agent.scan.morpho import section_for_day


def test_day_ts_is_utc_midnight():
    assert day_ts(date(2026, 9, 1)) == 1788220800


def test_section_for_day_picks_that_day_and_hides_future_markets():
    d = day_ts(date(2026, 9, 1))
    hist = [
        {"marketId": "0xa", "lltv": "1", "creationTimestamp": d - 86400, "loanAsset": {"symbol": "USDG", "address": "0x1"},
         "collateralAsset": {"symbol": "AMZN", "address": "0x2"}, "oracle": {"address": "0x3"},
         "historicalState": {"supplyAssetsUsd": [{"x": d, "y": 100.0}, {"x": d + 86400, "y": 200.0}],
                             "borrowAssetsUsd": [{"x": d, "y": 95.0}], "utilization": [{"x": d, "y": 0.95}]}},
        {"marketId": "0xb", "lltv": "1", "creationTimestamp": d + 86400 * 3, "loanAsset": {"symbol": "USDG", "address": "0x1"},
         "collateralAsset": None, "oracle": None, "historicalState": {}},
    ]
    sec = section_for_day(hist, d)
    assert sec["markets"] == 1 and sec["items"][0]["key"] == "0xa"
    assert sec["items"][0]["supply_usd"] == 100.0 and sec["items"][0]["utilization"] == 0.95
    assert sec["pinned_above_90pct"] == 1
    # a day with no data point gives nulls, not a crash
    assert section_for_day(hist, d - 86400)["items"][0]["supply_usd"] is None
