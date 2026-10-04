from agent import judge, ledger

CARD = {"stock_tokens": {"count": 190}, "morpho": {"markets": 3}, "gap": {"candidates": 5, "proposals": 0}}

def test_witness_holds():
    assert judge.eval_witness("stock_tokens.count >= 150", CARD) == (True, "all clauses hold")

def test_witness_fails():
    ok, why = judge.eval_witness("morpho.markets >= 10", CARD)
    assert ok is False and "fails" in why

def test_witness_unparseable_is_refused_not_guessed():
    ok, _ = judge.eval_witness("import os", CARD)
    assert ok is None

def test_and_clauses():
    assert judge.eval_witness("gap.candidates >= 1 and gap.proposals == 0", CARD)[0] is True

def test_rails_block_unlisted_feed():
    before = {"version": 0, "listings": [], "max_cap_increase_24h": 2.0}
    after = {"version": 1, "parent": ledger.sha(before),
             "listings": [{"symbol": "NVDA", "cap_usd": 1000, "chainlink_feed": None, "halt_free_30d": True, "liquidation_depth_usd": 10000}]}
    assert any("Chainlink" in p for p in judge.check_policy_rails(before, after))

def test_rails_block_big_cap_jump():
    before = {"version": 3, "listings": [{"symbol": "SPY", "cap_usd": 1000}], "max_cap_increase_24h": 2.0}
    after = {"version": 4, "parent": ledger.sha(before), "listings": [{"symbol": "SPY", "cap_usd": 5000}]}
    assert any("raised" in p for p in judge.check_policy_rails(before, after))
