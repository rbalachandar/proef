from proef.evals.toolcalls import _select_run, _t_tool_selection


def test_the_tool_selection_eval_separates_a_good_policy_from_bad_ones_and_none_breaks_the_audit():
    r = _t_tool_selection()
    assert r.passed and r.score == 1.0 and "scripted policies, not a real model" in r.metrics["note"]
    p = r.metrics["policies"]
    assert p["state-following"]["invalid_rate"] == 0 and p["state-following"]["wasted_rate"] == 0 and p["state-following"]["verdicts"] == 1
    assert p["random-any"]["invalid_rate"] > 0.3 > p["random-offered"]["invalid_rate"], "tools that are not offered are refused, and counted"
    assert p["repeat-one"]["wasted_rate"] > 0.5
    assert all(x["audit_clean"] for x in p.values())


def test_the_selection_run_is_deterministic_for_a_seed():
    assert _select_run("random-any", seed=3) == _select_run("random-any", seed=3)
    assert _select_run("random-any", seed=3) != _select_run("random-any", seed=4)
