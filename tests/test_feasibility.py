"""Feasibility flags, pause history and alerts; no network."""
from agent import gap, history, main


def _card():
    return {
        "ts": 100 * 86400,
        "stock_tokens": {"tokens": [
            {"symbol": "AAA", "address": "0xA", "total_supply_raw": 10 ** 18, "decimals": 18, "paused": False},
            {"symbol": "BBB", "address": "0xB", "total_supply_raw": 10 ** 18, "decimals": 18, "paused": False},
        ]},
        "morpho": {"items": []}, "uniswap": {"prices_usdg": {}}, "reverts": {"items": []},
        "feeds": {"tokens": {"AAA": {"answers_on_chain": True, "age_s": 60}}},
        "pause_history": {"tokens": {"AAA": {"pause_free_days": 45.0}, "BBB": {"pause_free_days": 3.0}}},
        "depth": {"tokens": {"AAA": {"depth_usd_5pct": 100_000}}},
    }


def test_flags_follow_the_measurements():
    by = {c["subject"]: c for c in gap.build(_card())["items"]}
    a, b = by["AAA"]["feasibility"], by["BBB"]["feasibility"]
    assert a["chainlink_feed"] is True and a["halt_free_30d"] is True
    assert a["liquidation_depth_usd"] == 100_000 and a["max_cap_by_depth_usd"] == 20_000
    assert by["AAA"]["passes_rails"] is True
    assert b["chainlink_feed"] is False and b["halt_free_30d"] is False and b["liquidation_depth_usd"] == 0
    assert by["BBB"]["passes_rails"] is False


def test_flags_are_null_when_not_measured():
    card = _card()
    for k in ("feeds", "pause_history", "depth"):
        card.pop(k)
    f = gap.build(card)["items"][0]["feasibility"]
    assert f["chainlink_feed"] is None and f["halt_free_30d"] is None and f["liquidation_depth_usd"] is None


def test_pause_history_counts_from_last_pause():
    state = {}
    day = 86400
    history._apply(state, {"ts": 100 * day, "stock_tokens": {"tokens": [{"symbol": "AAA", "paused": False}]}})
    history._apply(state, {"ts": 110 * day, "stock_tokens": {"tokens": [{"symbol": "AAA", "paused": True}]}})
    history._apply(state, {"ts": 111 * day, "stock_tokens": {"error": "x"}})  # failed scan changes nothing
    s = history.summarise(state, 150 * day)["AAA"]
    assert s["days_observed"] == 50 and s["pause_free_days"] == 40 and s["last_paused"] == 110 * day


def test_alerts_fire_on_pause_and_multiplier_step(monkeypatch):
    sent = []
    monkeypatch.setattr(main.notify, "send", sent.append)
    prev = {"stock_tokens": {"tokens": [{"symbol": "AAA", "paused": False, "multiplier_raw": 10}]}}
    cur = {"stock_tokens": {"tokens": [{"symbol": "AAA", "paused": True, "multiplier_raw": 11}]}}
    main.health_alerts(cur, prev)
    assert any("HALT: AAA" in m for m in sent) and any("MULTIPLIER STEP: AAA" in m for m in sent)
    sent.clear()
    main.health_alerts(prev, prev)
    assert sent == []
