"""Compute tools in the `propose` loop (5I): a model can run a registered predictor or the synthesis check BEFORE it proposes. What comes
back is a prior, never evidence; the tools exist only at `propose`; and a campaign that used them replays."""
import json

from proef.adapters.lab.simulator import SimulatorLab
from proef.adapters.store.sqlite import SqliteProvenanceStore
from proef.audit import audit_campaign
from proef.autoloop import CampaignConfig, run_campaign
from proef.core.contracts import ToolResult
from proef.core.hypothesis import Constraint, ProblemStatement
from proef.core.interfaces import system_actor
from proef.flywheel import Flywheel
from proef.predictors import DEFAULT, PredictorRegistry, ToolSpec
from proef.tools import ToolCatalog, ToolContext, predict_tool, synthesis_tool

ASPIRIN = "CC(=O)Oc1ccccc1C(=O)O"
PLAN = {"families": [{"family": "f", "parent_statement": "claim", "kind": "mechanistic", "mechanism": "m", "refutation_condition": "r",
                      "confidence": 0.6, "variants": [{"statement": "A binds", "candidate": "A", "assay_type": "binding", "predicted": "p",
                                                       "predicted_value": 20.0, "rationale": "x"}]}]}


def _cat(state="propose", registry=None, enabled=("admet-physchem",)):
    cat = ToolCatalog()
    cat.register(predict_tool(registry or DEFAULT, list(enabled)))
    cat.register(synthesis_tool())
    return cat, ToolContext(state=state, autonomy="act", store=SqliteProvenanceStore(":memory:"), actor=system_actor("t"), branch="t")


# ---- the tools ----

def test_a_prediction_is_a_prior_with_its_version_and_never_evidence():
    cat, c = _cat()
    r = cat.call(c, "predict", {"tool": "admet-physchem", "smiles": ASPIRIN})
    body = r["result"]
    assert r["status"] == "ok" and body["status"] == "ok" and body["evidence"] is False and body["kind"] == "prediction"
    assert body["version"] == "1" and "mw_da" in body["values"]


def test_an_unparseable_structure_is_reported_not_run_and_an_unlisted_tool_is_refused():
    cat, c = _cat()
    assert cat.call(c, "predict", {"tool": "admet-physchem", "smiles": "not a smiles"})["result"]["status"] == "invalid_input"
    r = cat.call(c, "predict", {"tool": "made-up-model", "smiles": ASPIRIN})
    assert r["status"] == "refused" and r["code"] == "bad_arguments", "the tool name must be one of the enabled ones"


def test_a_tool_the_operator_did_not_enable_is_not_on_the_menu():
    cat, c = _cat(enabled=())
    assert cat.call(c, "predict", {"tool": "admet-physchem", "smiles": ASPIRIN})["status"] == "refused"
    assert "none enabled" in cat.menu_text(c) or "predict(" in cat.menu_text(c)


def test_a_third_party_predictor_is_still_refused_in_process():
    reg = PredictorRegistry()
    reg.register(ToolSpec(name="x", version="1", trusted=False, endpoints={"v": ""}, input_kind="smiles", modalities=("any",), uncertainty="none",
                          description="d", fn=lambda s: {"values": {"v": 1.0}}, check_input=lambda s: None))
    cat, c = _cat(registry=reg, enabled=("x",))
    assert cat.call(c, "predict", {"tool": "x", "smiles": ASPIRIN})["result"]["status"] == "untrusted_tool"


def test_the_synthesis_check_answers_for_a_valid_structure_and_refuses_a_bad_one():
    cat, c = _cat()
    ok = cat.call(c, "check_synthesis", {"smiles": ASPIRIN})["result"]
    assert "makeable" in ok and "answer" in ok and ok["data_origin"]
    bad = cat.call(c, "check_synthesis", {"smiles": "???"})
    assert bad["status"] == "refused" and bad["code"] == "invalid_input"


def test_compute_tools_exist_only_at_propose():
    for state in ("run", "judge", "decide"):
        cat, c = _cat(state=state)
        assert cat.call(c, "predict", {"tool": "admet-physchem", "smiles": ASPIRIN})["code"] == "not_offered"
        assert cat.call(c, "check_synthesis", {"smiles": ASPIRIN})["code"] == "not_offered"


def test_toolkit_and_catalog_give_the_same_synthesis_answer():
    from proef.adapters.retrosynthesis import get_retrosynthesis_adapter
    from proef.toolkit import synthesis_answer
    cat, c = _cat()
    assert cat.call(c, "check_synthesis", {"smiles": ASPIRIN})["result"] == synthesis_answer(get_retrosynthesis_adapter("rule-based"), ASPIRIN)


# ---- in a campaign ----

class Model:
    name, model_version, model = "scripted", "s-1", "scripted-1"

    def __init__(self):
        self.prompts = []

    def generate(self, inv, prompt, **kw):
        self.prompts.append(prompt)
        if "TOOLS OFFERED IN THIS STEP" in prompt:
            n = prompt.count("\nRESULT {")
            text = [{"tool": "predict", "args": {"tool": "admet-physchem", "smiles": ASPIRIN}},
                    {"tool": "check_synthesis", "args": {"smiles": ASPIRIN}},
                    {"done": "aspirin: Lipinski pass, modelled as makeable"}][min(n, 2)]
            text = json.dumps(text)
        elif "hypothesis lead" in prompt:
            text = json.dumps(PLAN)
        else:
            text = json.dumps({"notes": []})
        return ToolResult(envelope=inv.envelope.model_copy(), invocation_id=inv.invocation_id, status="succeeded", output={"text": text},
                          model_version=self.model_version)

    def predict(self, inv, kind, payload):
        raise NotImplementedError


def _run(tmp_path, compute=("predict", "synthesis")):
    store = SqliteProvenanceStore(str(tmp_path / "p.db"))
    model = Model()
    cfg = CampaignConfig(problem=ProblemStatement(objective="o", constraints=[Constraint(name="potency", description="IC50 <= 20 uM", kind="hard", limit=20.0, unit="uM")]),
                         hypothesis_source="agent", model=model, autonomy="act", k=2, seed=1, inbox=str(tmp_path / "in"), poll_secs=0.05,
                         flywheel=Flywheel(":memory:"), loop_compute=compute, prediction_tools=["admet-physchem"])
    rep = run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    return rep, store, store.branch_records(f"problem/{rep.problem_id}"), model


def test_the_model_runs_the_predictor_and_the_synthesis_check_before_it_proposes(tmp_path):
    rep, store, recs, model = _run(tmp_path)
    calls = [r.decision for r in recs if (r.decision or {}).get("kind") == "tool_call"]
    assert [(c["tool"], c["class"]) for c in calls] == [("predict", "compute"), ("check_synthesis", "compute")]
    assert [r.decision["tool"] for r in recs if (r.decision or {}).get("kind") == "tool_result"] == ["predict", "check_synthesis"], "sealed for replay"
    proposal = next(p for p in model.prompts if "hypothesis lead" in p)
    assert "aspirin: Lipinski pass, modelled as makeable" in proposal and "BACKGROUND READ BEFORE PROPOSING" in proposal
    assert "never evidence" in next(p for p in model.prompts if "TOOLS OFFERED IN THIS STEP" in p), "the loop prompt says what a prior is"
    menu = next(p for p in model.prompts if "TOOLS OFFERED IN THIS STEP" in p)
    assert "predict(tool: string, smiles: string)" in menu and "check_synthesis(smiles: string)" in menu
    assert audit_campaign(recs) == []


def test_a_prior_changes_no_verdict(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    assert _run(tmp_path / "a")[0].verdicts == _run(tmp_path / "b", compute=())[0].verdicts


def test_the_envelope_records_the_compute_tools_so_a_replay_offers_the_same_menu(tmp_path):
    _, _, recs, _ = _run(tmp_path)
    env = next((r.decision["environment"] for r in recs if (r.decision or {}).get("environment")), {})
    assert env["loop_compute"] == ["predict", "synthesis"]


def test_a_campaign_that_used_compute_tools_replays_from_the_ledger(tmp_path):
    from proef.reproduce import reproduce
    rep, _, _, _ = _run(tmp_path)
    out = reproduce(f"problem/{rep.problem_id}", db_path=str(tmp_path / "p.db"), inbox=str(tmp_path / "rin"))
    assert out.get("status") == "match", out
