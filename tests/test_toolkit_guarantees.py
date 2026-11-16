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

def _mcp(gate=None):
    from proef.mcp_server import McpSession
    s = McpSession(gate=gate)

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
    s, call = _mcp(GATE)
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
