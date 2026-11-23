"""A campaign whose model calls failed says so (the engine carries on with fallbacks, so it did not run as configured)."""
import json

import pytest

import tests.test_followups as F
from proef.adapters.lab.simulator import SimulatorLab
from proef.adapters.store.sqlite import SqliteProvenanceStore
from proef.audit import audit_campaign
from proef.autoloop import CampaignConfig, ResourceBudget, run_campaign
from proef.core.contracts import ToolResult
from proef.core.hypothesis import Constraint, ProblemStatement
from proef.flywheel import Flywheel


class FailsOn(F.Model):
    """The follow-up model, except it raises when the prompt contains `marker` (the first `times` calls)."""

    def __init__(self, answer, marker, times=1):
        super().__init__(answer)
        self.marker, self.times = marker, times

    def generate(self, inv, prompt, **kw):
        if self.marker in prompt and self.times > 0:
            self.times -= 1
            raise ValueError("garbled answer from the provider")
        return super().generate(inv, prompt, **kw)


def _run(tmp_path, model, **kw):
    store = SqliteProvenanceStore(str(tmp_path / "p.db"))
    cfg = CampaignConfig(problem=ProblemStatement(objective="o", constraints=[Constraint(name="potency", description="IC50 <= 20 uM", kind="hard", limit=20.0, unit="uM")]),
                         hypothesis_source="agent", model=model, autonomy="act", k=8, max_families=8, rounds=2, seed=1, followups=True, inbox=str(tmp_path / "in"),
                         poll_secs=0.05, flywheel=Flywheel(":memory:"), budget=ResourceBudget(max_total_runs=50, max_hypotheses_total=30), **kw)
    said = []
    rep = run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=said.append)
    return rep, store.branch_records(f"problem/{rep.problem_id}"), said


class _Shared:
    def __init__(self, dir, result):
        self.dir, self.result = dir, result


@pytest.fixture(scope="module")
def shared_run_0(tmp_path_factory):
    """One run of this scenario, shared by the tests that only read its result (2 tests)."""
    d = tmp_path_factory.mktemp("shared_run_0")
    return _Shared(d, _run(d, FailsOn(F._answer, 'ABSTAINED HYPOTHESES')))



@pytest.fixture(scope="module")
def shared_run_1(tmp_path_factory):
    """One run of this scenario, shared by the tests that only read its result (2 tests)."""
    d = tmp_path_factory.mktemp("shared_run_1")
    return _Shared(d, _run(d, FailsOn(F._answer, 'Review this campaign analysis summary')))



@pytest.fixture(scope="module")
def shared_run_2(tmp_path_factory):
    """One run of this scenario, shared by the tests that only read its result (2 tests)."""
    d = tmp_path_factory.mktemp("shared_run_2")
    return _Shared(d, _run(d, F.Model(F._answer)))


def test_a_campaign_with_a_failed_model_call_reports_itself_degraded_on_the_ledger_and_to_the_caller(shared_run_0):
    (rep, recs, said) = shared_run_0.result
    assert rep.degraded and rep.model_failures == 1
    d = next(r.decision for r in recs if (r.decision or {}).get("kind") == "campaign_degraded")
    assert d["model_failures"] == 1 and sum(d["by_role"].values()) == 1 and "garbled" in d["first_errors"][0] and "did not run as configured" in d["meaning"]
    assert any("ran DEGRADED" in m for m in said)
    assert audit_campaign(recs) == [], "reporting it is what the audit asks for"


def test_a_failed_call_the_campaign_depends_on_is_degraded_but_a_failed_advisory_call_is_only_a_note(shared_run_1):
    """A failed reviewer only means an advisory output is missing: the goal and the verdicts are unaffected, so it is a note, not an alarm."""
    (rep, recs, said) = shared_run_1.result
    assert not rep.degraded and rep.model_failures == 1 and rep.advisory_failures == 1
    assert not [r for r in recs if (r.decision or {}).get("kind") == "campaign_degraded"]
    note = next(r.decision for r in recs if (r.decision or {}).get("kind") == "model_advisory_failures")
    assert note["count"] == 1 and note["by_purpose"] == {"reviewer": 1} and "unaffected" in note["meaning"]
    assert any("advisory model call(s) failed" in m for m in said) and not any("DEGRADED" in m for m in said)
    assert audit_campaign(recs) == []


def test_the_purpose_of_a_call_is_recorded_so_a_failure_can_be_classified(shared_run_2):
    (rep, recs, _) = shared_run_2.result
    purposes = {r.decision["purpose"] for r in recs if (r.decision or {}).get("kind") == "model_call"}
    assert {"proposal", "followup"} <= purposes and all(p for p in purposes)


def test_which_failures_matter_depends_on_what_the_campaign_was_configured_to_use():
    from proef.model_guard import failure_effect, purpose_of
    full = {"orchestrator": "model", "followups": True, "replan": True, "grounding_tools": ["pubmed"]}
    bare = {"orchestrator": "fixed", "followups": False, "replan": False, "grounding_tools": []}
    for purpose in ("proposal", "orchestrator", "followup", "replan", "grounding"):
        assert failure_effect(purpose, full) == "dependency", purpose
    assert [failure_effect(p, bare) for p in ("orchestrator", "followup", "replan", "grounding")] == ["advisory"] * 4, "a feature that was not configured is not a dependency"
    assert failure_effect("proposal", bare) == "dependency" and failure_effect("something-new", bare) == "dependency", "the conservative answer for what it cannot classify"
    assert failure_effect("critic", full) == "advisory" == failure_effect("reviewer", full)
    assert purpose_of("model", "... TOOLS OFFERED IN THIS STEP ...") == "grounding" and purpose_of("model", "x RESULTS SO FAR (re-plan from these) hypothesis lead") == "replan"
    assert purpose_of("model", "a hypothesis lead prompt") == "proposal" and purpose_of("orchestrator", "anything") == "orchestrator"


def test_a_clean_campaign_is_not_marked_degraded(shared_run_2):
    (rep, recs, said) = shared_run_2.result
    assert not rep.degraded and rep.model_failures == 0
    assert not [r for r in recs if (r.decision or {}).get("kind") == "campaign_degraded"] and not any("DEGRADED" in m for m in said)


def test_the_audit_flags_a_campaign_that_had_model_errors_and_never_said_so(shared_run_0):
    (rep, recs, _) = shared_run_0.result
    without = [r for r in recs if (r.decision or {}).get("kind") != "campaign_degraded"]
    assert [x.rule for x in audit_campaign(without)] == ["degradation_reported"]


def test_the_audit_also_asks_an_advisory_failure_to_be_noted(shared_run_1):
    (rep, recs, _) = shared_run_1.result
    without = [r for r in recs if (r.decision or {}).get("kind") != "model_advisory_failures"]
    assert [x.rule for x in audit_campaign(without)] == ["degradation_reported"]
