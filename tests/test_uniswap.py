"""Slippage arithmetic in scan/uniswap._row; no network."""
from agent.scan.uniswap import _row

Q96 = 2 ** 96
USDG = "0x5fc5360D0400a0Fd4f2af552ADD042D716F1d168"
META = {"venue": "v3", "token0": USDG, "token1": "0x000000000000000000000000000000000000dEaD", "fee": 3000}
TOKENS = {USDG: {"symbol": "USDG", "decimals": 6}}


def test_row_measures_impact_between_consecutive_swaps():
    # price goes 1.00 -> 1.01 (sqrt ratio 1.005): impact ~100 bps on a $200 swap -> ~$1 paid
    swaps = [(1, 0, 100_000_000, -1, Q96), (2, 0, 200_000_000, -1, int(Q96 * 1.005))]
    r = _row("p", swaps, META, TOKENS, USDG)
    assert r["pair"] == "USDG/0x00000000"
    assert r["swaps"] == 2 and r["volume_usd"] == 300.0
    assert abs(r["median_impact_bps"] - 100.25) < 0.1
    assert abs(r["slippage_paid_usd_est"] - 1.0025) < 0.01
    assert r["degenerate_swaps"] == 0


def test_row_caps_degenerate_price_moves():
    swaps = [(1, 0, 1_000_000, -1, Q96), (2, 0, 9_000_000, -1, Q96 * 10 ** 6)]
    r = _row("p", swaps, META, TOKENS, USDG)
    assert r["degenerate_swaps"] == 1
    assert r["median_impact_bps"] == 10_000.0
    assert r["slippage_paid_usd_est"] == 4.5  # capped at half the swap size


def test_row_without_usdg_leg_reports_no_usd():
    meta = {**META, "token0": "0x000000000000000000000000000000000000bEEF"}
    r = _row("p", [(1, 0, 5, -5, Q96), (2, 0, 5, -5, Q96)], meta, TOKENS, USDG)
    assert r["volume_usd"] is None and r["slippage_paid_usd_est"] is None
    assert r["median_impact_bps"] == 0.0


def test_row_unresolved_pool():
    r = _row("0xabc", [(1, 0, 1, -1, Q96)], None, TOKENS, USDG)
    assert r["pair"] is None and r["swaps"] == 1 and r["median_impact_bps"] is None
