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


def test_a_campaign_with_a_failed_model_call_reports_itself_degraded_on_the_ledger_and_to_the_caller(tmp_path):
    rep, recs, said = _run(tmp_path, FailsOn(F._answer, "ABSTAINED HYPOTHESES"))
    assert rep.degraded and rep.model_failures == 1
    d = next(r.decision for r in recs if (r.decision or {}).get("kind") == "campaign_degraded")
    assert d["model_failures"] == 1 and sum(d["by_role"].values()) == 1 and "garbled" in d["first_errors"][0] and "did not run as configured" in d["meaning"]
    assert any("ran DEGRADED" in m for m in said)
    assert audit_campaign(recs) == [], "reporting it is what the audit asks for"


def test_a_clean_campaign_is_not_marked_degraded(tmp_path):
    rep, recs, said = _run(tmp_path, F.Model(F._answer))
    assert not rep.degraded and rep.model_failures == 0
    assert not [r for r in recs if (r.decision or {}).get("kind") == "campaign_degraded"] and not any("DEGRADED" in m for m in said)


def test_the_audit_flags_a_campaign_that_had_model_errors_and_never_said_so(tmp_path):
    rep, recs, _ = _run(tmp_path, FailsOn(F._answer, "ABSTAINED HYPOTHESES"))
    without = [r for r in recs if (r.decision or {}).get("kind") != "campaign_degraded"]
    assert [x.rule for x in audit_campaign(without)] == ["degradation_reported"]
