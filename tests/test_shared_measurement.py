"""One compound proposed under two families is measured ONCE, and every hypothesis on it is judged from that plate.
Before: each hypothesis submitted its own run (same sample name), the first plate judged both, and the second run was
paid for and never read."""
import json
import pytest

from proef.adapters.lab.simulator import SimulatorLab
from proef.adapters.store.sqlite import SqliteProvenanceStore
from proef.autoloop import CampaignConfig, run_campaign
from proef.core.contracts import EventType, ToolResult
from proef.core.hypothesis import Constraint, ProblemStatement
from proef.flywheel import Flywheel


def _variant(c, stmt):
    return {"statement": stmt, "candidate": c, "assay_type": "binding", "predicted": "<=20",
            "predicted_value": 20.0, "rationale": "r"}


PLAN = {"families": [
    {"family": "hinge", "parent_statement": "hinge binders", "kind": "mechanistic", "mechanism": "m",
     "refutation_condition": "r", "confidence": 0.8, "variants": [_variant("H1", "H1 binds (hinge)"), _variant("H2", "H2 binds")]},
    {"family": "covalent", "parent_statement": "covalent binders", "kind": "mechanistic", "mechanism": "m",
     "refutation_condition": "r", "confidence": 0.5, "variants": [_variant("H1", "H1 binds (covalent)")]}]}


class Scripted:
    name, model_version, model = "scripted", "s-1", "scripted-1"

    def generate(self, inv, prompt, **kw):
        return ToolResult(envelope=inv.envelope.model_copy(), invocation_id=inv.invocation_id, status="succeeded",
                          output={"text": json.dumps(PLAN)}, model_version=self.model_version)

    def predict(self, inv, kind, payload):
        raise NotImplementedError


def _run(tmp_path):
    store = SqliteProvenanceStore(str(tmp_path / "prov.db"))
    cfg = CampaignConfig(
        problem=ProblemStatement(objective="find IC50<=20", constraints=[
            Constraint(name="potency", description="IC50 <= 20 uM", kind="hard", limit=20.0, unit="uM")]),
        hypothesis_source="agent", model=Scripted(), autonomy="act", k=4, rounds=1, seed=1,
        inbox=str(tmp_path / "inbox"), poll_secs=0.05, flywheel=Flywheel(":memory:"))
    msgs = []
    report = run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=msgs.append)
    return report, store, store.branch_records(f"problem/{report.problem_id}"), msgs


class _Shared:
    def __init__(self, dir, result):
        self.dir, self.result = dir, result


@pytest.fixture(scope="module")
def shared_run_0(tmp_path_factory):
    """One run of this scenario, shared by the tests that only read its result (3 tests)."""
    d = tmp_path_factory.mktemp("shared_run_0")
    return _Shared(d, _run(d))


def test_a_compound_proposed_twice_gets_one_lab_run(shared_run_0):
    (report, store, recs, msgs) = shared_run_0.result
    runs = [r for r in recs if r.event_type == EventType.lab_run]
    titles = [(r.decision or {}).get("title", "") for r in runs]
    assert sum(t == "test-H1" for t in titles) == 1, titles     # the test of H1 ran once, not twice
    assert "auto-confirmation-H1" in titles, "the orthogonal confirmation is a separate, deliberate run"
    assert any("sharing run" in m for m in msgs)


def test_every_hypothesis_on_the_shared_compound_is_judged_from_that_one_run(shared_run_0):
    (report, store, recs, _) = shared_run_0.result
    test_runs = {(r.decision or {}).get("run_id") for r in recs
                 if r.event_type == EventType.lab_run and (r.decision or {}).get("title") == "test-H1"}
    shared = [r for r in recs if (r.decision or {}).get("kind") == "shared_measurement"]
    assert len(shared) == 1 and len(shared[0].decision["hypotheses"]) == 2
    assert test_runs == {shared[0].decision["run_id"]}, "both H1 hypotheses point at the one run that was paid for"
    h1_verdicts = [r for r in recs if r.event_type in (
        EventType.hypothesis_validated, EventType.hypothesis_invalidated, EventType.hypothesis_inconclusive)
        and (r.decision or {}).get("evidence_run_id") == shared[0].decision["run_id"]]
    assert len(h1_verdicts) >= 2, "each hypothesis on the compound got its own verdict record from that run"


def test_the_run_budget_is_not_spent_on_the_duplicate(shared_run_0):
    from proef.autoloop import ResourceBudget
    (_, store, recs, _) = shared_run_0.result
    used = ResourceBudget().lab_runs_used(store, recs[0].envelope.experiment_branch_id.split("/", 1)[-1])
    names = {(r.decision or {}).get("title", "") for r in recs if r.event_type == EventType.lab_run}
    assert used == len([r for r in recs if r.event_type == EventType.lab_run])
    assert len(names) == len([r for r in recs if r.event_type == EventType.lab_run]), "no repeated run title"


def test_a_variant_that_tunes_the_test_differently_is_not_judged_from_the_first_ones_run(tmp_path):
    """Found by the independent review: sharing dropped the later variant's own designed ladder and judged it from a run made
    under different conditions. Now it waits, recorded, and is never judged from the wrong run."""
    import tests.test_shared_measurement as me
    plan = json.loads(json.dumps(me.PLAN))
    a, b = plan["families"][0]["variants"][0], plan["families"][1]["variants"][0]       # H1 under two families
    a["protocol"] = {"concentrations_uM": [0.5, 2, 10, 40], "why": "a"}
    b["protocol"] = {"concentrations_uM": [0.1, 1, 5, 20, 80], "why": "b"}
    saved = me.PLAN
    me.PLAN = plan
    try:
        report, store, recs, msgs = me._run(tmp_path)
    finally:
        me.PLAN = saved
    declined = [r for r in recs if (r.decision or {}).get("kind") == "shared_measurement_declined"]
    assert len(declined) == 1 and declined[0].decision["candidate"] == "H1"
    assert not [r for r in recs if (r.decision or {}).get("kind") == "shared_measurement"], "not shared: the conditions differ"
    assert sum((r.decision or {}).get("title") == "test-H1" for r in recs if r.event_type == EventType.lab_run) == 1
    assert any("waits for its own run" in m for m in msgs)
