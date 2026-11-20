"""One implementation of the guards (item 4): the toolkit holds them; the in-process tool catalog and the MCP server are doors onto it.
Change a guard once and every door follows."""
import json

from proef.core.hypothesis import SuccessCriteria
from proef.mcp_server import TOOLS, McpSession
from proef.toolkit import TOOL_SCHEMAS

GATE = SuccessCriteria(metric="ic50_um", direction="min", threshold=20.0)


def _mcp(**kw):
    s = McpSession(**kw)

    def call(name, args):
        r = s.handle({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": name, "arguments": args}})
        return json.loads(r["result"]["content"][0]["text"])
    return s, call


def test_mcp_has_no_copy_of_the_assay_or_hypothesis_guards_it_asks_the_toolkit():
    s, call = _mcp(gate=GATE, approved={"A"})
    calls = []
    real = s.toolkit._raw_executors

    def spy():
        ex = real()
        return {k: (lambda *a, _f=f, _k=k, **kw: (calls.append(_k), _f(*a, **kw))[1]) for k, f in ex.items()}
    s.toolkit._raw_executors = spy
    call("run_assay", {"compounds": ["A"], "assay": "binding"})
    call("propose_hypothesis", {"statement": "s", "candidate": "A", "assay_type": "binding", "predicted": "p"})
    assert calls == ["submit_assay", "propose_hypothesis"]


def test_a_guard_changed_in_the_toolkit_changes_what_mcp_does():
    """If MCP kept its own copy, this would not reach it."""
    from proef.toolkit import AgentToolkit
    s, call = _mcp(gate=GATE, approved={"A"})
    assert call("run_assay", {"compounds": ["A"], "assay": "binding"}).get("run_id")
    orig = AgentToolkit._tool_submit
    AgentToolkit._tool_submit = lambda self, *a, **k: self._refuse("submit_assay", "refused by a guard added in one place")
    try:
        out = call("run_assay", {"compounds": ["A"], "assay": "binding"})
    finally:
        AgentToolkit._tool_submit = orig
    assert out["status"] == "refused" and "one place" in out["reason"]


def test_the_same_input_is_refused_the_same_way_by_mcp_and_by_the_toolkit():
    from proef.adapters.lab.simulator import SimulatorLab
    from proef.adapters.store.sqlite import SqliteProvenanceStore
    from proef.core.interfaces import system_actor
    from proef.core.safety import LabGateway, SafetyEngine
    from proef.governance import execution_governance
    from proef.toolkit import AgentToolkit
    store, actor = SqliteProvenanceStore(), system_actor("t")
    tk = AgentToolkit(LabGateway(SimulatorLab(seed=1), SafetyEngine(), store, actor, governance=execution_governance()), store, actor,
                      gate=GATE, approved={"A"}, autonomy="act").executors()
    s, call = _mcp(gate=GATE, approved={"A"})
    for compounds in (["B"], ["A", "B"], []):
        direct = tk["submit_assay"](compounds=compounds, assay_type="binding")
        via = call("run_assay", {"compounds": compounds, "assay": "binding"})
        assert direct["status"] == via["status"] == "refused" and direct["candidates"] == via["candidates"], compounds
    nogate = _mcp(approved={"A"})[1]("propose_hypothesis", {"statement": "s", "candidate": "A", "assay_type": "binding", "predicted": "p"})
    assert nogate["status"] == "refused" and "no program gate" in nogate["reason"]


def test_the_catalog_door_and_the_mcp_door_judge_by_the_same_gate():
    from proef.tools import ToolContext, default_catalog
    from proef.core.interfaces import system_actor
    from proef.toolkit import AgentToolkit
    from proef.adapters.lab.simulator import SimulatorLab
    from proef.adapters.store.sqlite import SqliteProvenanceStore
    from proef.core.safety import LabGateway, SafetyEngine
    from proef.governance import execution_governance
    store, actor = SqliteProvenanceStore(), system_actor("t")
    tk = AgentToolkit(LabGateway(SimulatorLab(seed=1), SafetyEngine(), store, actor, governance=execution_governance()), store, actor,
                      gate=GATE, approved=set(), autonomy="act")
    r = default_catalog().call(ToolContext(state="propose", autonomy="act", store=store, actor=actor, toolkit=tk), "propose_hypothesis",
                               {"statement": "s", "candidate": "A", "assay_type": "binding", "predicted": "p"})
    m = _mcp(gate=GATE)[1]("propose_hypothesis", {"statement": "s", "candidate": "A", "assay_type": "binding", "predicted": "p"})
    assert r["result"]["gate"] == m["gate"] == {"metric": "ic50_um", "threshold": 20.0}


def test_mcp_and_the_toolkit_describe_propose_hypothesis_with_the_same_arguments():
    mcp = next(t for t in TOOLS if t["name"] == "propose_hypothesis")["inputSchema"]
    tk = next(t for t in TOOL_SCHEMAS if t["name"] == "propose_hypothesis")["input_schema"]
    assert set(mcp["properties"]) == set(tk["properties"]) and set(mcp["required"]) == set(tk["required"])


def test_the_session_attributes_stay_the_source_of_truth_for_the_guards():
    s, call = _mcp(gate=GATE, approved={"A"})
    assert call("run_assay", {"compounds": ["B"], "assay": "binding"})["status"] == "refused"
    s.approved.add("B")
    assert call("run_assay", {"compounds": ["B"], "assay": "binding"}).get("run_id"), "a person approving B on the session is seen by the guard"
