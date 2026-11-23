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

def _own_copy(shared, tmp_path):
    """A private copy of the shared campaign's ledger. A replay writes its own campaign into the database it reads from AND its instrument files into an inbox keyed by
    the campaign's id, so tests that replay a shared run must each give it their own database copy and their own inbox (`inbox=`): the shared ORIGINAL run is safe to
    read, the places a replay writes are not. (Found when the reviewer test failed only after another replay had run.)"""
    import sqlite3
    dst = tmp_path / "prov.db"
    src, out = sqlite3.connect(str(shared.dir / "prov.db")), sqlite3.connect(str(dst))
    src.backup(out)
    src.close()
    out.close()
    return dst


class _Shared:
    def __init__(self, dir, result):
        self.dir, self.result = dir, result


@pytest.fixture(scope="module")
def shared_F__run_0(tmp_path_factory):
    """One run of this scenario, shared by the tests that only read its result (5 tests)."""
    d = tmp_path_factory.mktemp("shared_F__run_0")
    return _Shared(d, F._run(d))


def test_every_model_answer_is_sealed_with_its_call_and_prompt(shared_F__run_0):
    (report, store, recs, model, _) = shared_F__run_0.result
    calls = [r.decision for r in recs if (r.decision or {}).get("kind") == "model_call"]
    resp = [r.decision for r in recs if (r.decision or {}).get("kind") == "model_response"]
    assert len(calls) == len(resp) == len(model.prompts) and calls
    assert [c["n"] for c in calls] == [r["n"] for r in resp] and [c["prompt_hash"] for c in calls] == [r["prompt_hash"] for r in resp]
    sealed = [r for r in resp if r["sealed"]]
    assert sealed and all(r["response"] and not r["truncated"] for r in sealed)
    assert all(r["role"] == "reviewer" for r in resp if not r["sealed"]), "only the advisory reviewer's text is left out"


def test_the_campaign_settings_a_replay_needs_are_in_the_recorded_environment(shared_F__run_0):
    (report, store, recs, _, _) = shared_F__run_0.result
    env = next(r.decision for r in recs if r.event_type.value == "problem_defined")["environment"]
    assert env["hypothesis_source"] == "agent" and env["followups"] is True and env["max_families"] == 8
    assert {"replan", "modality", "prediction_tools", "critic", "plan_review", "budget"} <= set(env)


# ---- replay ----

def test_a_campaign_with_follow_ups_replays_from_the_ledger_and_matches(shared_F__run_0, tmp_path):
    (report, store, recs, model, _) = shared_F__run_0.result
    assert [r for r in recs if (r.decision or {}).get("followup_of")], "the original ran a follow-up"
    res = reproduce(f"problem/{report.problem_id}", db_path=str(_own_copy(shared_F__run_0, tmp_path)), inbox=str(tmp_path / "rin"))
    assert res["status"] == "match", res
    assert res["model_pinned"] is True and res["model_calls_replayed"] == len(model.prompts)


def test_a_campaign_with_a_critic_and_a_replan_replays_too(tmp_path):
    from tests.test_model_guard import Scripted, _campaign
    cfg, store = _campaign(tmp_path, critic=Scripted(), replan=True, rounds=2)
    report = run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    res = reproduce(f"problem/{report.problem_id}", db_path=str(tmp_path / "prov.db"))
    assert res["status"] == "match" and res["model_calls_replayed"] >= 3, res


def test_a_replay_that_makes_different_model_calls_is_a_difference(shared_F__run_0, tmp_path, monkeypatch):
    """The replay is rebuilt with follow-ups switched off: it asks the model one question fewer than the original."""
    import proef.reproduce as rp
    (report, store, recs, _, _) = shared_F__run_0.result
    real = rp._agent_cfg

    def without_followups(cfg, *a, **kw):
        from dataclasses import replace
        c, rm = real(cfg, *a, **kw)
        return replace(c, followups=False), rm
    monkeypatch.setattr(rp, "_agent_cfg", without_followups)
    res = reproduce(f"problem/{report.problem_id}", db_path=str(_own_copy(shared_F__run_0, tmp_path)), inbox=str(tmp_path / "rin"))
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
    model.n = 3          # a replay must match whatever the portfolio size; three compounds are enough
    cfg = CampaignConfig(problem=F.ProblemStatement(objective="o", constraints=[F.Constraint(name="potency", description="IC50 <= 20 uM", kind="hard", limit=20.0, unit="uM")]),
                         hypothesis_source="agent", model=model, autonomy="act", k=3, max_families=3, rounds=1, seed=1,
                         inbox=str(tmp_path / "inbox"), poll_secs=0.05, flywheel=fw)
    report = run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    assert "RULEDOUT" in model.prompts[0], "the scenario: the flywheel shaped the prompt"
    assert reproduce(f"problem/{report.problem_id}", db_path=str(tmp_path / "prov.db"))["status"] == "match"


# ---- found by the independent review of Phase 4 ----

def test_a_call_that_failed_in_the_original_fails_the_same_way_in_the_replay(tmp_path):
    """The critic is advisory, so its failure is swallowed. The original sealed the failure; a replay that skipped it would hand
    the critic's slot the next recorded answer and shift every later call by one."""
    from tests.test_model_guard import Scripted, _campaign

    class FlakyCritic(Scripted):
        def generate(self, inv, prompt, **kw):
            raise RuntimeError("critic unavailable")
    cfg, store = _campaign(tmp_path, critic=FlakyCritic())
    report = run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    recs = store.branch_records(f"problem/{report.problem_id}")
    assert [r for r in recs if (r.decision or {}).get("kind") == "model_error"], "the failure is sealed"
    res = reproduce(f"problem/{report.problem_id}", db_path=str(tmp_path / "prov.db"))
    assert res["status"] == "match" and res["model_calls_replayed"] >= 3, res


def test_a_replay_measures_with_the_same_lab_not_only_the_same_seed(tmp_path):
    lab = SimulatorLab(seed=3, replicates=5, interference_rate=0.0)
    report, store, recs, _, _ = F._run(tmp_path, size=3, lab=lab)
    env = next(r.decision for r in recs if r.event_type.value == "problem_defined")["environment"]
    assert env["lab_config"]["replicates"] == 5 and env["lab_config"]["interference_rate"] == 0.0
    assert reproduce(f"problem/{report.problem_id}", db_path=str(tmp_path / "prov.db"))["status"] == "match"


def test_a_generation_that_failed_still_leaves_its_flywheel_context_so_the_replay_stays_aligned(tmp_path):
    """The re-plan's generation fails in the original and writes no hypothesis_generation record. A replay that took one context per
    SUCCESSFUL generation would give the failed attempt's slot to the wrong prompt."""
    fw = Flywheel(":memory:")
    fw.submit("RULEDOUT", "old-family", "binding", "invalidated", 80.0, (60.0, 100.0), problem_id="earlier", threshold=20.0, evidence_run="r0")

    class FailsTheReplan(F.Model):
        n = 3

        def generate(self, inv, prompt, **kw):
            if "RESULTS SO FAR" in prompt:
                self.prompts.append(prompt)
                raise RuntimeError("model down")
            return super().generate(inv, prompt, **kw)
    store = SqliteProvenanceStore(str(tmp_path / "prov.db"))
    cfg = CampaignConfig(problem=F.ProblemStatement(objective="o", constraints=[F.Constraint(name="potency", description="IC50 <= 20 uM", kind="hard", limit=20.0, unit="uM")]),
                         hypothesis_source="agent", model=FailsTheReplan(F._answer), autonomy="act", k=3, max_families=3, rounds=2, seed=1,
                         replan=True, inbox=str(tmp_path / "inbox"), poll_secs=0.05, flywheel=fw)
    report = run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    recs = store.branch_records(f"problem/{report.problem_id}")
    assert len([r for r in recs if (r.decision or {}).get("kind") == "generation_attempt"]) == 2
    assert len([r for r in recs if (r.decision or {}).get("kind") == "hypothesis_generation"]) == 1, "only one generation succeeded"
    assert reproduce(f"problem/{report.problem_id}", db_path=str(tmp_path / "prov.db"))["status"] == "match"


def test_the_advisory_reviewers_answer_is_not_sealed_and_the_replay_still_matches(shared_F__run_0, tmp_path):
    (report, store, recs, _, _) = shared_F__run_0.result
    rev = [r.decision for r in recs if (r.decision or {}).get("kind") == "model_response" and r.decision["role"] == "reviewer"]
    assert rev and all(r["sealed"] is False and r["response"] == "" and r["response_chars"] > 0 for r in rev)
    assert reproduce(f"problem/{report.problem_id}", db_path=str(_own_copy(shared_F__run_0, tmp_path)), inbox=str(tmp_path / "rin"))["status"] == "match"


def test_the_workbench_serves_the_size_of_a_sealed_answer_not_its_text():
    from proef.api.v1.campaigns import _served_detail
    d = {"kind": "model_response", "n": 1, "response": "SECRET STUDY DATA" * 100, "response_chars": 1700}
    out = _served_detail(d)
    assert "SECRET" not in str(out) and "1700 characters" in out["response"] and d["response"].startswith("SECRET"), "served masked, ledger untouched"
    assert _served_detail({"kind": "lab_run", "x": 1}) == {"kind": "lab_run", "x": 1}


def test_a_campaigns_model_is_called_with_generate_only():
    """predict() was counted against the budget but never sealed, so it could not be replayed. It is refused."""
    from proef.model_guard import GovernedModel, ModelGuard
    from proef.core.interfaces import system_actor
    guard = ModelGuard(SqliteProvenanceStore(), system_actor(), "p", {}, 10)
    with pytest.raises(NotImplementedError, match="generate"):
        GovernedModel(F.Model(F._answer), "model", guard).predict(None, "k", {})
    assert guard.used() == 0, "nothing was counted"


def test_the_replays_prediction_stand_in_keeps_the_same_tool_cap_as_the_real_registry():
    from proef.predictors import MAX_TOOLS_PER_VARIANT
    from proef.reproduce import _RecordedPredictors
    rm = RecordedModel([])
    recorded = [{"tool": f"t{i}", "input": "CCO", "status": "ok", "kind": "prediction", "evidence": False} for i in range(30)]
    out = _RecordedPredictors(recorded, rm).run([f"t{i}" for i in range(30)], {"smiles": "CCO"}, None, None)
    assert len(out) == MAX_TOOLS_PER_VARIANT and not rm.divergences, "the extras were never run by the original, so they are not looked up"
