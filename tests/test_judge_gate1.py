"""Gate 1: policy certificates, adoption, replay and the held-out budget."""
import importlib.util, json, pathlib, sys
import pytest
from agent import ledger, settings


from agent import judge

CARD = {"stock_tokens": {"count": 190}, "morpho": {"markets": 3}, "gap": {"candidates": 5, "proposals": 0}}
GOOD_LISTING = {"symbol": "NVDA", "cap_usd": 1000, "chainlink_feed": "0xfeed", "halt_free_30d": True, "liquidation_depth_usd": 10000, "oracle_finding": "ok"}


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "ENTRY_DIR", tmp_path / "entries"); monkeypatch.setattr(settings, "SCORECARD_DIR", tmp_path / "cards")
    monkeypatch.setattr(settings, "POLICY_PATH", tmp_path / "policy.json"); monkeypatch.setattr(settings, "LEDGER_GIT_REMOTE", "")
    (tmp_path / "cards").mkdir()
    settings.POLICY_PATH.write_text(json.dumps({"version": 0, "parent": None, "listings": [], "max_cap_increase_24h": 2.0}))
    h = ledger.write_scorecard(CARD)
    return tmp_path, h


def _after(before, **changes):
    return {**before, "version": before["version"] + 1, "parent": ledger.sha(before), **changes}


def test_policy_change_is_certified_then_adopted_by_a_person(sandbox):
    _, h = sandbox
    before = judge.current_policy()
    cert = judge.propose_policy(_after(before, listings=[GOOD_LISTING]), "human", "stock_tokens.count >= 150", CARD, h)
    assert cert["verdict"] == "adoptable" and cert["before"] == before
    assert judge.current_policy() == before                      # nothing changes until the click
    adoption = judge.adopt(cert["hash"][:16])
    assert judge.current_policy()["listings"] == [GOOD_LISTING] and adoption["certificate"] == cert["hash"]
    kinds = [e["kind"] for e in ledger.entries()]
    assert kinds == ["policy", "adoption"]


def test_refused_or_stale_certificates_cannot_be_adopted(sandbox):
    _, h = sandbox
    before = judge.current_policy()
    bad = judge.propose_policy(_after(before, listings=[{**GOOD_LISTING, "chainlink_feed": None}]), "model", "stock_tokens.count >= 150", CARD, h)
    assert bad["verdict"] == "refused" and "Chainlink" in bad["why"]
    with pytest.raises(ValueError):
        judge.adopt(bad["hash"][:16])
    good = judge.propose_policy(_after(before, listings=[GOOD_LISTING]), "human", "stock_tokens.count >= 150", CARD, h)
    settings.POLICY_PATH.write_text(json.dumps({**before, "version": 7}))          # policy moved on underneath
    with pytest.raises(ValueError):
        judge.adopt(good["hash"][:16])


def test_rails_cover_oracle_findings_and_loosening(sandbox):
    before = judge.current_policy()
    assert any("oracle" in p for p in judge.check_policy_rails(before, _after(before, listings=[{**GOOD_LISTING, "oracle_finding": "fixed_with_delayed_backup"}])))
    assert any("oracle" in p for p in judge.check_policy_rails(before, _after(before, listings=[{k: v for k, v in GOOD_LISTING.items() if k != "oracle_finding"}])))
    assert any("loosened" in p for p in judge.check_policy_rails(before, _after(before, max_cap_increase_24h=3.0)))
    assert judge.check_policy_rails(before, _after(before, listings=[GOOD_LISTING])) == []


def test_replay_rederives_rung_and_policy_certificates(sandbox):
    _, h = sandbox
    before = judge.current_policy()
    cert = judge.propose_policy(_after(before, listings=[GOOD_LISTING]), "human", "stock_tokens.count >= 150", CARD, h)
    rung = {"kind": "rung", "rung": "r1", "witness": "morpho.markets >= 10", "scorecard": h, "verdict": "refused"}
    rung["hash"] = ledger.append_entry(rung)
    assert judge.replay(cert["hash"][:16]) is True and judge.replay(rung["hash"][:16]) is True
    tampered = {**cert, "after": {**cert["after"], "listings": []}}       # entry whose body no longer matches its hashes
    assert judge.replay_entry(tampered)[0] is None


def test_held_out_budget_is_public_and_finite(sandbox, monkeypatch):
    monkeypatch.setattr(judge, "HELD_OUT_BUDGET", 3)
    for i in range(3):
        assert judge.read_slice("holdout:gaps-2026H2", "human", "check calibration") is True
    assert judge.read_slice("holdout:gaps-2026H2", "human", "one more look") is False
    assert judge.slice_reads("holdout:gaps-2026H2") == 3                     # the refused read is not recorded
    assert judge.figure("holdout:gaps-2026H2", 0.42)["refused"] is False      # at the budget: still reportable
    monkeypatch.setattr(judge, "HELD_OUT_BUDGET", 2)
    assert judge.figure("holdout:gaps-2026H2", 0.42) == {"slice": "holdout:gaps-2026H2", "value": None, "reads": 3, "budget": 2, "refused": True,
                                                         "why": "held-out slice read more than its budget; figure withheld"}


def test_booleans_are_not_numbers():
    assert judge.eval_witness("gap.ok >= 1", {"gap": {"ok": True}})[0] is None
