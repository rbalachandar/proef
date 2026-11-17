"""Phase 0 of docs/LLM-ORCHESTRATION.md: a model driving the toolkit cannot skip approval, move the gate or write a verdict.
Each test is an attack. A test that passes without its guard is not a test, so each asserts on what was RECORDED."""
from proef.adapters.lab.simulator import SimulatorLab
from proef.adapters.store.sqlite import SqliteProvenanceStore
from proef.core.contracts import EventType
from proef.core.hypothesis import SuccessCriteria
from proef.core.interfaces import system_actor
from proef.core.safety import LabGateway, SafetyEngine
from proef.toolkit import AgentToolkit

from pathlib import Path

_NO_HALT = Path("/nonexistent-proef-test/HALT")   # never exists: no halt engaged
GATE = SuccessCriteria(metric="ic50_um", direction="min", threshold=20.0)


def _tk(gate=GATE, approved=(), halt=None, governed=True):
    from proef.governance import AuthorizationMatrix, HaltSwitch
    store = SqliteProvenanceStore()
    gov = {"matrix": AuthorizationMatrix(), "halt": halt or HaltSwitch(path=_NO_HALT)} if governed else None
    gw = LabGateway(SimulatorLab(seed=1), SafetyEngine(), store, system_actor(), governance=gov,
                    approver=(lambda t: True) if gov else None)
    tk = AgentToolkit(gw, store, system_actor(), branch_id="t", gate=gate, approved=approved)
    return tk, tk.executors(), store


def _records(store, kind):
    return [r for r in store.branch_records("t") if (r.decision or {}).get("kind") == kind]


def test_there_is_no_tool_that_writes_a_verdict():
    names = {s["name"] for s in AgentToolkit.schemas()}
    assert "record_verdict" not in names
    _, ex, _ = _tk()
    assert "record_verdict" not in ex


def test_a_hypothesis_is_judged_by_the_programs_gate_not_the_models_number():
    tk, ex, store = _tk()
    out = ex["propose_hypothesis"](statement="X inhibits", candidate="proposed: X", assay_type="binding",
                                   predicted="IC50 = 0.001 uM")
    assert out["gate"] == {"metric": "ic50_um", "threshold": 20.0}, out
    rec = next(r for r in store.branch_records("t") if r.event_type == EventType.hypothesis_proposed)
    assert rec.decision["criteria"]["threshold"] == 20.0
    assert tk._hyps["X"].candidate == "X" and out["candidate"] == "X", "the model's 'proposed:' prefix is cleaned"


def test_no_gate_means_no_hypothesis_is_recorded():
    _, ex, store = _tk(gate=None)
    out = ex["propose_hypothesis"](statement="s", candidate="X", assay_type="binding", predicted="p")
    assert out["status"] == "refused"
    assert not [r for r in store.branch_records("t") if r.event_type == EventType.hypothesis_proposed]


def test_submit_is_refused_for_a_candidate_no_plan_approved():
    _, ex, store = _tk(approved={"A"})
    out = ex["submit_assay"](compounds=["A", "B"], assay_type="binding")
    assert out["status"] == "refused" and out["candidates"] == ["B"]
    assert not [r for r in store.branch_records("t") if r.event_type == EventType.lab_run], "nothing reached the lab"
    assert _records(store, "tool_refused")


def test_submit_with_no_approval_at_all_is_refused():
    _, ex, store = _tk()
    assert ex["submit_assay"](compounds=["A"], assay_type="binding")["status"] == "refused"


def test_an_approved_candidate_runs_and_is_recorded_as_covered_by_the_plan():
    _, ex, store = _tk(approved={"A"})
    out = ex["submit_assay"](compounds=["A"], assay_type="binding", concentrations=["10uM", "99999uM"])
    assert out["status"] == "submitted"
    assert _records(store, "covered_by_plan")


def test_an_absurd_ladder_is_bounded():
    tk, ex, store = _tk(approved={"A"})
    run = ex["submit_assay"](compounds=["A"], assay_type="binding", concentrations=["99999uM", "-5uM"])["run_id"]
    doses = {sp.concentration for sp in tk._runs[run].samples}
    assert not {"99999uM", "-5uM"} & doses, "out-of-range doses never reach the lab"


def test_the_engine_judges_and_the_model_cannot_choose_the_outcome():
    _, ex, store = _tk(approved={"A"})
    ex["propose_hypothesis"](statement="s", candidate="A", assay_type="binding", predicted="whatever")
    run = ex["submit_assay"](compounds=["A"], assay_type="binding")["run_id"]
    out = ex["request_analysis"](candidate="A", run_id=run)
    assert out["status"] == "judged" and out["recorded_by"] == "engine"
    verdicts = [r for r in store.branch_records("t") if r.event_type in (
        EventType.hypothesis_validated, EventType.hypothesis_invalidated, EventType.hypothesis_inconclusive)]
    assert len(verdicts) == 1
    assert verdicts[0].decision["criteria_preregistered"] is True
    assert verdicts[0].decision["threshold"] == 20.0


def test_analysis_is_refused_without_a_gate_or_for_someone_elses_run():
    _, ex, _ = _tk(approved={"A", "B"})
    ex["propose_hypothesis"](statement="s", candidate="A", assay_type="binding", predicted="p")
    run_b = ex["submit_assay"](compounds=["B"], assay_type="binding")["run_id"]
    assert ex["request_analysis"](candidate="A", run_id=run_b)["status"] == "refused"
    assert ex["request_analysis"](candidate="Z", run_id=run_b)["status"] == "refused"


def test_every_tool_call_is_a_ledger_record_including_refusals():
    _, ex, store = _tk()
    ex["submit_assay"](compounds=["A"], assay_type="binding")
    calls = _records(store, "tool_call")
    assert len(calls) == 1 and calls[0].decision["tool"] == "submit_assay" and calls[0].decision["refused"] is True
    assert len(calls[0].decision["args_hash"]) == 16


# ---- the MCP surface (external clients such as Claude Desktop) ----

def _mcp(gate=None, approved=None):
    from proef.mcp_server import McpSession
    s = McpSession(gate=gate, approved=approved)

    def call(name, args):
        import json
        out = s.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
        return json.loads(out["result"]["content"][0]["text"])
    return s, call


def test_mcp_refuses_a_hypothesis_when_no_gate_is_registered(monkeypatch):
    monkeypatch.delenv("PROEF_MCP_GATE", raising=False)
    s, call = _mcp()
    out = call("propose_hypothesis", {"statement": "s", "candidate": "X", "assay_type": "binding", "predicted": "IC50 0.01"})
    assert out["status"] == "refused"
    assert not [r for r in s.store.branch_records("mcp-session") if r.event_type == EventType.hypothesis_proposed]


def test_mcp_hypothesis_carries_the_programs_gate_not_the_clients_number():
    s, call = _mcp(GATE)
    call("propose_hypothesis", {"statement": "s", "candidate": "X", "assay_type": "binding", "predicted": "IC50 0.01"})
    rec = next(r for r in s.store.branch_records("mcp-session") if r.event_type == EventType.hypothesis_proposed)
    assert rec.decision["criteria"]["threshold"] == 20.0


def test_mcp_gate_can_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("PROEF_MCP_GATE", "ic50_um<=12.5")
    s, _ = _mcp()
    assert (s.gate.metric, s.gate.direction, s.gate.threshold) == ("ic50_um", "min", 12.5)
    monkeypatch.setenv("PROEF_MCP_GATE", "nonsense")
    assert _mcp()[0].gate is None


def test_mcp_calls_are_on_the_ledger_and_the_lab_is_labelled_simulated():
    s, call = _mcp(GATE, approved={"CCO"})
    out = call("run_assay", {"compounds": ["CCO"], "assay": "binding"})
    assert "simulated" in out["lab"]
    calls = [r for r in s.store.branch_records("mcp-session") if (r.decision or {}).get("kind") == "tool_call"]
    assert [c.decision["tool"] for c in calls] == ["run_assay"]


# ---- governance applies to the toolkit's lab path ----

def test_a_halt_stops_an_approved_submission(tmp_path):
    from proef.governance import HaltSwitch
    halt = HaltSwitch(path=tmp_path / "HALT", history_path=tmp_path / "H")
    halt.engage("spill in cabinet 2")
    _, ex, store = _tk(approved={"A"}, halt=halt)
    out = ex["submit_assay"](compounds=["A"], assay_type="binding")
    assert out["status"] == "rejected" and "halt" in out["reason"].lower()
    assert not [r for r in store.branch_records("t") if r.event_type == EventType.lab_run]


def test_a_gateway_without_governance_is_refused():
    _, ex, store = _tk(approved={"A"}, governed=False)
    out = ex["submit_assay"](compounds=["A"], assay_type="binding")
    assert out["status"] == "refused" and "governance" in out["reason"]
    assert not [r for r in store.branch_records("t") if r.event_type == EventType.lab_run]


# ---- the tracker itself: no invented thresholds ----

def test_a_placeholder_criterion_abstains_it_never_refutes_against_zero():
    """With no gate and no prediction the old default registered `ic50 <= 0.0`, so any measurement was a refutation."""
    from proef.core.contracts import Envelope
    from proef.core.hypothesis import HypothesisTracker
    tk, ex, store = _tk(approved={"A"})
    t = HypothesisTracker(store, system_actor())
    h = t.propose("s", "A", "binding", "p", branch_id="t")
    rec = next(r for r in store.branch_records("t") if r.event_type == EventType.hypothesis_proposed)
    assert rec.decision["criteria_source"] == "placeholder"
    run = ex["submit_assay"](compounds=["A"], assay_type="binding")["run_id"]
    t.verdict(h, tk.gateway.results(Envelope(experiment_branch_id="t"), run))
    assert h.status == "inconclusive"
    v = [r for r in store.branch_records("t") if r.event_type in (
        EventType.hypothesis_validated, EventType.hypothesis_invalidated, EventType.hypothesis_inconclusive)]
    assert v and v[-1].decision["criteria_preregistered"] is False


def test_the_programs_gate_replaces_a_placeholder_before_judging():
    from proef.autoloop import _assign_criteria
    from proef.core.hypothesis import HypothesisTracker
    t = HypothesisTracker(SqliteProvenanceStore(), system_actor())
    h = t.propose("s", "A", "binding", "p", branch_id="b")
    _assign_criteria([h], GATE)
    assert h.criteria.threshold == 20.0 and h.metadata["criteria_source"] == "gate"


def test_the_record_says_whether_the_criterion_came_from_a_gate_or_a_prediction():
    from proef.core.hypothesis import HypothesisTracker
    store = SqliteProvenanceStore()
    t = HypothesisTracker(store, system_actor())
    t.propose("s", "A", "binding", "p", criteria=GATE, branch_id="b")
    t.propose("s", "B", "binding", "p", predicted_value=3.0, branch_id="b")
    src = {r.decision["criteria_source"] for r in store.branch_records("b") if r.event_type == EventType.hypothesis_proposed}
    assert src == {"explicit", "prediction"}   # a bare proposal is "placeholder": see the test above


def test_protein_design_hypotheses_are_registered_against_the_iptm_gate():
    """Found in review: the adapter built an ipTM criterion and never passed it, so binders were registered against a
    threshold derived from the model's pLDDT, in an IC50 metric."""
    from proef.adapters.lab.protein_design import ProteinDesignLabAdapter
    from tests.test_protein_design import _make_tracker
    tr, prob = _make_tracker()
    ProteinDesignLabAdapter(mock=True, seed=7, min_plddt=0.5).run_design_cycle(
        tr, prob, target_sequence="M" * 120, n_attempts=30, screening_budget=5, iptm_gate=0.5)
    recs = [r for r in tr.store.branch_records(f"problem/{prob.problem_id}") if r.event_type == EventType.hypothesis_proposed]
    assert recs and all(r.decision["criteria"]["metric"] == "iptm" and r.decision["criteria"]["threshold"] == 0.5
                        for r in recs)


# ---- the ledger shows tool calls ----

def test_tool_calls_show_in_the_activity_feed_and_the_diary():
    from proef.api.v1.campaigns import activity_items
    from proef.diary import build_diary_html
    _, ex, store = _tk()
    ex["submit_assay"](compounds=["A"], assay_type="binding")
    recs = store.branch_records("t")
    items = activity_items(recs)
    assert any(i.kind == "tool" and "submit_assay" in i.text and "refused" in i.text for i in items)
    html = build_diary_html(recs, "t")
    assert "tool call: submit_assay" in html and "REFUSED" in html


def test_mcp_will_not_run_an_assay_on_a_candidate_no_person_approved(monkeypatch):
    monkeypatch.delenv("PROEF_MCP_APPROVED", raising=False)
    s, call = _mcp(GATE)
    out = call("run_assay", {"compounds": ["CCO"], "assay": "binding"})
    assert out["status"] == "refused" and out["candidates"] == ["CCO"]
    assert not [r for r in s.store.branch_records("mcp-session") if r.event_type == EventType.lab_run]
    s2, call2 = _mcp(GATE, approved={"A"})
    assert call2("run_assay", {"compounds": ["A", "B"], "assay": "binding"})["candidates"] == ["B"]


def test_mcp_approval_can_come_from_the_environment_and_ladders_are_bounded(monkeypatch):
    monkeypatch.setenv("PROEF_MCP_APPROVED", "A, B")
    s, call = _mcp(GATE)
    assert s.approved == {"A", "B"}
    out = call("run_assay", {"compounds": ["A"], "assay": "binding", "concentrations": ["99999uM", "5uM"]})
    pts = {p[0] for p in out["results"]["A"]["points"]}
    assert 99999 not in pts


# ---- the modality reaches every way a hypothesis can be created ----

def test_the_toolkit_and_mcp_judge_a_materials_hypothesis_by_the_programs_metric_not_an_ic50():
    """A model that names a chemistry assay for a materials program must not turn the gate into an IC50."""
    gate = SuccessCriteria(metric="conductivity", direction="max", threshold=1.0)
    for modality, expect in (("material", "conductivity"), (None, "ic50_um")):
        tk = AgentToolkit(None, SqliteProvenanceStore(), system_actor(), branch_id="t", gate=gate, modality=modality)
        out = tk.executors()["propose_hypothesis"](statement="s", candidate="LLZO", assay_type="binding", predicted="p")
        assert out["gate"]["metric"] == expect, (modality, out)
        s, call = _mcp(gate)
        s.modality = modality
        assert call("propose_hypothesis", {"statement": "s", "candidate": "LLZO", "assay_type": "binding", "predicted": "p"})["gate"]["metric"] == expect


# ---- found by the independent review: request_analysis ----

def _judged(approved, concentrations=None):
    tk, ex, store = _tk(approved=approved)
    ex["propose_hypothesis"](statement="s", candidate="A", assay_type="binding", predicted="p")
    kw = {"concentrations": concentrations} if concentrations else {}
    run = ex["submit_assay"](compounds=["A"], assay_type="binding", **kw)["run_id"]
    return tk, ex, store, run


def test_a_dose_response_verdict_carries_an_interval_not_a_point_estimate():
    tk, ex, store, run = _judged({"A"}, ["0.5uM", "2uM", "10uM", "40uM", "100uM"])
    out = ex["request_analysis"](candidate="A", run_id=run)
    assert out["status"] == "judged" and out["interval_source"] == "hill fit with bootstrap interval"
    assert out["ci"] and len(out["ci"]) == 2, "the rule saw an interval, so it could abstain"


def test_a_run_that_cannot_give_an_interval_abstains_instead_of_judging_a_point_value():
    """Two doses cannot be fitted. Before: the simulator's apparent IC50 on the well was classified as if certain."""
    from proef.core.contracts import Envelope
    tk, ex, store, run = _judged({"A"})
    full = tk.gateway.results(Envelope(experiment_branch_id="t"), run)
    two_doses = full.model_copy(update={"measurements": [m for m in full.measurements
                                                         if m.derived_metrics.get("conc_um") in (0.5, 2.0) or "control" in [f.value for f in m.flags]]})
    tk.gateway.results = lambda env, rid: two_doses
    out = ex["request_analysis"](candidate="A", run_id=run)
    assert (out["verdict"], out["interval_source"]) == ("inconclusive", "none")


def test_a_hypothesis_is_judged_once_and_cannot_be_re_judged_by_resubmitting():
    tk, ex, store, run = _judged({"A"}, ["0.5uM", "2uM", "10uM", "40uM", "100uM"])
    assert ex["request_analysis"](candidate="A", run_id=run)["status"] == "judged"
    run2 = ex["submit_assay"](compounds=["A"], assay_type="binding")["run_id"]
    again = ex["request_analysis"](candidate="A", run_id=run2)
    assert again["status"] == "refused" and "judged once" in again["reason"]
    verdicts = [r for r in store.branch_records("t") if r.event_type in (
        EventType.hypothesis_validated, EventType.hypothesis_invalidated, EventType.hypothesis_inconclusive)]
    assert len(verdicts) == 1


def test_a_run_that_is_not_complete_is_not_judged():
    from proef.core.contracts import RunStatus
    tk, ex, store, run = _judged({"A"})
    real = tk.gateway.results
    tk.gateway.results = lambda env, rid: real(env, rid).model_copy(update={"status": RunStatus.partial})
    out = ex["request_analysis"](candidate="A", run_id=run)
    assert out["status"] == "refused" and "not complete" in out["reason"]
    assert not [r for r in store.branch_records("t") if r.event_type in (
        EventType.hypothesis_validated, EventType.hypothesis_invalidated, EventType.hypothesis_inconclusive)]


# ---- found by the independent review: a rebuild from the ledger must see the gate ----

def test_a_rebuild_from_the_ledger_judges_against_the_gate_not_the_placeholder(tmp_path):
    """A proposal with no criteria and no prediction is recorded as a placeholder. The program's gate replaces it in memory.
    A plate submitted later rebuilds the tracker from the ledger, and used to get the placeholder back and abstain."""
    from proef.adapters.lab.simulator import SimulatorLab as Lab
    from proef.autoloop import CampaignConfig, run_campaign
    from proef.core.hypothesis import Constraint, ProblemStatement
    from proef.flywheel import Flywheel
    store = SqliteProvenanceStore(str(tmp_path / "prov.db"))
    problem = ProblemStatement(objective="o", constraints=[Constraint(name="potency", description="IC50 <= 20 uM", kind="hard", limit=20.0, unit="uM")])
    cfg = CampaignConfig(problem=problem, hypotheses=[{"statement": "A binds", "candidate": "A", "assay_type": "binding", "predicted": "p"}],
                         autonomy="act", k=1, rounds=1, seed=1, inbox=str(tmp_path / "inbox"), poll_secs=0.05, flywheel=Flywheel(":memory:"))
    report = run_campaign(cfg, Lab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    branch = f"problem/{report.problem_id}"
    proposal = next(r for r in store.branch_records(branch) if r.event_type == EventType.hypothesis_proposed and r.decision.get("criteria"))
    assert proposal.decision["criteria_source"] == "placeholder", "the scenario: the proposal itself carried no gate"
    from proef.core.hypothesis import HypothesisTracker
    t = HypothesisTracker.resume(store, system_actor(), branch)
    h = next(iter(t.hypotheses.values()))
    assert (h.criteria.threshold, h.metadata["criteria_source"]) == (20.0, "gate")


def test_a_malformed_mcp_gate_is_treated_as_unset_not_a_crash(monkeypatch):
    for bad in ("ic50_um<=1e", "ic50_um<=1.2.3", "ic50_um==5", "<=3", "nonsense"):
        monkeypatch.setenv("PROEF_MCP_GATE", bad)
        assert _mcp()[0].gate is None, bad
    monkeypatch.setenv("PROEF_MCP_GATE", "ic50_um<=1e2")
    assert _mcp()[0].gate.threshold == 100.0


def test_an_mcp_call_that_raises_still_leaves_a_ledger_record():
    import pytest
    s, call = _mcp(GATE, approved={"A"})
    with pytest.raises(Exception):
        s._call_tool("run_assay", {"assay": "binding"})            # missing `compounds`
    recs = [r for r in s.store.branch_records("mcp-session") if (r.decision or {}).get("kind") == "tool_call"]
    assert [r.decision["tool"] for r in recs] == ["run_assay"] and recs[0].decision["status"] == "error"


# ---- autonomy levels apply to the tools a model is offered ----

def test_under_observe_the_lab_tools_are_not_offered_and_cannot_be_called():
    offered = {t["name"] for t in AgentToolkit.schemas("observe")}
    assert not offered & AgentToolkit.LAB_TOOLS and "propose_hypothesis" in offered and "predict_admet" in offered
    assert {"submit_assay", "request_analysis"} <= {t["name"] for t in AgentToolkit.schemas("gate")} == {t["name"] for t in AgentToolkit.schemas("act")}
    tk = AgentToolkit(None, SqliteProvenanceStore(), system_actor(), branch_id="t", gate=GATE, approved={"A"}, autonomy="observe")
    ex = tk.executors()
    assert "submit_assay" not in ex and "request_analysis" not in ex and "propose_hypothesis" in ex
    with __import__("pytest").raises(KeyError):
        ex["submit_assay"](compounds=["A"], assay_type="binding")
