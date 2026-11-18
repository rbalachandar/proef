"""Phase 4A of docs/LLM-ORCHESTRATION.md: a model's answers are sealed in the ledger, and an agent campaign replays from them. The
model is never asked, re-plans and follow-ups reproduce, and any deviation from the original is reported."""
import json

import pytest

from proef.adapters.lab.simulator import SimulatorLab
from proef.adapters.store.sqlite import SqliteProvenanceStore
from proef.autoloop import CampaignConfig, run_campaign
from proef.core.contracts import Envelope, ToolInvocation
from proef.flywheel import Flywheel
from proef.model_guard import RecordedModel, ReplayDivergence
from proef.reproduce import reproduce

import tests.test_followups as F


def _call(rm, prompt):
    return rm.generate(ToolInvocation(envelope=Envelope(experiment_branch_id="x"), tool="c.fm.generate_molecules"), prompt)


def _resp(n, prompt, text="{}", role="model", **kw):
    import hashlib
    return {"n": n, "role": role, "prompt_hash": hashlib.sha256(prompt.encode()).hexdigest()[:12], "response": text,
            "truncated": False, "status": "succeeded", "model": "m", "model_version": "v", **kw}


# ---- the recorded model ----

def test_a_recorded_model_plays_back_in_order_and_checks_every_prompt():
    rm = RecordedModel([_resp(2, "second", "B"), _resp(1, "first", "A")])
    assert _call(rm, "first").output["text"] == "A" and _call(rm, "second").output["text"] == "B" and rm.remaining() == 0


def test_a_different_prompt_or_an_extra_call_is_a_divergence_that_is_remembered():
    rm = RecordedModel([_resp(1, "first")])
    with pytest.raises(ReplayDivergence, match="prompt differs"):
        _call(rm, "something else")
    rm2 = RecordedModel([])
    with pytest.raises(ReplayDivergence, match="did not"):
        _call(rm2, "anything")
    assert rm.divergences and rm2.divergences, "kept even if a caller swallows the exception"


def test_a_response_too_large_to_seal_is_reported_not_guessed():
    rm = RecordedModel([_resp(1, "p", truncated=True)])
    with pytest.raises(ReplayDivergence, match="too large"):
        _call(rm, "p")


# ---- sealed in the ledger ----

def test_every_model_answer_is_sealed_with_its_call_and_prompt(tmp_path):
    report, store, recs, model, _ = F._run(tmp_path)
    calls = [r.decision for r in recs if (r.decision or {}).get("kind") == "model_call"]
    resp = [r.decision for r in recs if (r.decision or {}).get("kind") == "model_response"]
    assert len(calls) == len(resp) == len(model.prompts) and calls
    assert [c["n"] for c in calls] == [r["n"] for r in resp] and [c["prompt_hash"] for c in calls] == [r["prompt_hash"] for r in resp]
    assert all(r["response"] and not r["truncated"] for r in resp)


def test_the_campaign_settings_a_replay_needs_are_in_the_recorded_environment(tmp_path):
    report, store, recs, _, _ = F._run(tmp_path)
    env = next(r.decision for r in recs if r.event_type.value == "problem_defined")["environment"]
    assert env["hypothesis_source"] == "agent" and env["followups"] is True and env["max_families"] == 8
    assert {"replan", "modality", "prediction_tools", "critic", "plan_review", "budget"} <= set(env)


# ---- replay ----

def test_a_campaign_with_follow_ups_replays_from_the_ledger_and_matches(tmp_path):
    report, store, recs, model, _ = F._run(tmp_path)
    assert [r for r in recs if (r.decision or {}).get("followup_of")], "the original ran a follow-up"
    res = reproduce(f"problem/{report.problem_id}", db_path=str(tmp_path / "prov.db"))
    assert res["status"] == "match", res
    assert res["model_pinned"] is True and res["model_calls_replayed"] == len(model.prompts)


def test_a_campaign_with_a_critic_and_a_replan_replays_too(tmp_path):
    from tests.test_model_guard import Scripted, _campaign
    cfg, store = _campaign(tmp_path, critic=Scripted(), replan=True, rounds=2)
    report = run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    res = reproduce(f"problem/{report.problem_id}", db_path=str(tmp_path / "prov.db"))
    assert res["status"] == "match" and res["model_calls_replayed"] >= 3, res


def test_a_replay_that_makes_different_model_calls_is_a_difference(tmp_path, monkeypatch):
    """The replay is rebuilt with follow-ups switched off: it asks the model one question fewer than the original."""
    import proef.reproduce as rp
    report, store, recs, _, _ = F._run(tmp_path)
    real = rp._agent_cfg

    def without_followups(cfg, *a, **kw):
        from dataclasses import replace
        c, rm = real(cfg, *a, **kw)
        return replace(c, followups=False), rm
    monkeypatch.setattr(rp, "_agent_cfg", without_followups)
    res = reproduce(f"problem/{report.problem_id}", db_path=str(tmp_path / "prov.db"))
    # the next question it asks (the reviewer's) is not the follow-up question the original asked at that point
    assert res["status"] == "differs" and any(d.get("call") and "prompt differs" in str(d["replayed"]) for d in res["diffs"]), res


def test_a_divergence_the_campaign_swallowed_still_shows(tmp_path, monkeypatch):
    """The critic is advisory and its failures are swallowed on purpose. A replay that diverges there must still be reported."""
    import proef.reproduce as rp
    from tests.test_model_guard import Scripted, _campaign
    cfg, store = _campaign(tmp_path, critic=Scripted())
    report = run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    real = rp._agent_cfg

    def tamper(c, *a, **kw):
        out, rm = real(c, *a, **kw)
        crit = next(x for x in rm._r if x["role"] == "critic")
        crit["prompt_hash"] = "000000000000"
        return out, rm
    monkeypatch.setattr(rp, "_agent_cfg", tamper)
    res = reproduce(f"problem/{report.problem_id}", db_path=str(tmp_path / "prov.db"))
    assert res["status"] == "differs" and any("prompt differs" in str(d["replayed"]) for d in res["diffs"]), res


def test_a_replay_reproduces_the_prompt_the_flywheel_shaped(tmp_path):
    """The original's prompt carried ruled-out candidates from the flywheel. A replay has no flywheel, so the context is sealed too."""
    fw = Flywheel(":memory:")
    fw.submit("RULEDOUT", "old-family", "binding", "invalidated", 80.0, (60.0, 100.0), problem_id="earlier", threshold=20.0, evidence_run="r0")
    store = SqliteProvenanceStore(str(tmp_path / "prov.db"))
    model = F.Model(F._answer)
    cfg = CampaignConfig(problem=F.ProblemStatement(objective="o", constraints=[F.Constraint(name="potency", description="IC50 <= 20 uM", kind="hard", limit=20.0, unit="uM")]),
                         hypothesis_source="agent", model=model, autonomy="act", k=8, max_families=8, rounds=1, seed=1,
                         inbox=str(tmp_path / "inbox"), poll_secs=0.05, flywheel=fw)
    report = run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    assert "RULEDOUT" in model.prompts[0], "the scenario: the flywheel shaped the prompt"
    assert reproduce(f"problem/{report.problem_id}", db_path=str(tmp_path / "prov.db"))["status"] == "match"
