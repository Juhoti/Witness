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


def test_tick_price_matches_sqrt_price():
    from agent.prices import tick_to_price, usdg_per_token
    import math
    tick = math.log(250 * 10 ** 6 / 10 ** 18, 1.0001)          # token0 = 18-dec token, token1 = USDG
    assert abs(tick_to_price(tick, True, 18) - 250) < 1e-6
    sp = int((1.0001 ** tick) ** 0.5 * Q96)
    assert abs(tick_to_price(tick, True, 18) - usdg_per_token(sp, True, 18)) < 1e-6
