"""Reference price arithmetic; no network."""
from agent.prices import weighted_median, usdg_per_token, Q96


def test_weighted_median_follows_the_liquidity():
    assert weighted_median([(100.0, 1), (101.0, 98), (250.0, 1)]) == 101.0   # a thin outlier pool cannot move it
    assert weighted_median([(1.0, 0), (2.0, 5)]) == 2.0                       # empty pools are ignored
    assert weighted_median([]) is None and weighted_median([(1.0, 0)]) is None


def test_price_from_sqrt_price_both_orderings():
    # token (18 decimals) is token0, USDG (6) is token1, price 250: raw ratio = 250 * 1e6 / 1e18
    sp = int((250 * 10 ** 6 / 10 ** 18) ** 0.5 * Q96)
    assert abs(usdg_per_token(sp, True, 18) - 250) < 1e-6
    # USDG is token0, token is token1: raw ratio = 1e18 / (250 * 1e6)
    sp = int((10 ** 18 / (250 * 10 ** 6)) ** 0.5 * Q96)
    assert abs(usdg_per_token(sp, False, 18) - 250) < 1e-6
