"""Phase 2B of docs/LLM-ORCHESTRATION.md: prediction tools are registered with a contract, enabled by the operator, proposed by
the model and validated and RUN by code. A prediction is a prior: it never becomes a criterion, a verdict or evidence."""
import json
import math

from proef.adapters.store.sqlite import SqliteProvenanceStore
from proef.core.contracts import EventType, ToolResult
from proef.core.hypothesis import Constraint, HypothesisTracker, ProblemStatement
from proef.core.interfaces import system_actor
from proef.hypothesis_agent import propose_families_with_agent
from proef.plan import build_plan
from proef.predictors import DEFAULT, PredictorRegistry, ToolSpec

ETHANOL, ASPIRIN = "CCO", "CC(=O)Oc1ccccc1C(=O)O"


def _spec(name="t1", fn=None, endpoints=None, uncertainty="none", modalities=("small molecule",), check=None, trusted=True,
          timeout_s=10.0):
    return ToolSpec(name=name, version="2", endpoints=endpoints or {"x": "uM"}, input_kind="smiles", modalities=modalities,
                    trusted=trusted, timeout_s=timeout_s, uncertainty=uncertainty, description="d", fn=fn or (lambda s: {"values": {"x": 1.5}, "in_domain": True}),
                    check_input=check or (lambda s: None))


def _reg(*specs):
    r = PredictorRegistry()
    for s in specs:
        r.register(s)
    return r


# ---- the contract ----

def test_a_tool_cannot_be_registered_twice():
    import pytest
    r = _reg(_spec())
    with pytest.raises(ValueError, match="already registered"):
        r.register(_spec())


def test_the_menu_is_what_the_operator_enabled_and_the_modality_allows():
    r = _reg(_spec("a"), _spec("b", modalities=("material",)), _spec("c"))
    assert [s.name for s in r.menu(["a", "b"], "small molecule")] == ["a"]
    assert [s.name for s in r.menu(["a", "b", "c"], "material")] == ["b"]
    assert r.menu(None, "small molecule") == [] and r.menu([], "small molecule") == []


def test_every_failure_mode_is_a_status_not_a_crash():
    boom = _spec("boom", fn=lambda s: 1 / 0)
    nan = _spec("nan", fn=lambda s: {"values": {"x": math.nan}})
    bad = _spec("bad", check=lambda s: "not a SMILES string")
    r = _reg(_spec("ok"), boom, nan, bad, _spec("mat", modalities=("material",)))
    en = ["ok", "boom", "nan", "bad", "mat"]
    res = {x["tool"]: x for x in r.run(["ok", "boom", "nan", "bad", "mat", "ghost", "ok"], {"smiles": "CCO"}, en, "small molecule")}
    assert [res[k]["status"] for k in ("ok", "boom", "nan", "bad", "mat", "ghost")] == \
        ["ok", "error", "invalid_output", "invalid_input", "wrong_modality", "unknown_tool"]
    assert r.run(["ok"], {}, en, "small molecule")[0]["status"] == "no_input"


def test_a_tool_the_operator_did_not_enable_cannot_run_even_if_registered():
    r = _reg(_spec("a"))
    assert r.run(["a"], {"smiles": "CCO"}, [], "small molecule")[0]["status"] == "unknown_tool"


def test_only_declared_endpoints_with_declared_units_come_back():
    r = _reg(_spec("a", endpoints={"x": "uM"}, fn=lambda s: {"values": {"x": 2.0, "secret": 9.0, "flag": True}}))
    out = r.run(["a"], {"smiles": "CCO"}, ["a"], "small molecule")[0]
    assert out["values"] == {"x": 2.0} and out["units"] == {"x": "uM"}


def test_every_result_says_it_is_a_prior_and_carries_the_version():
    out = _reg(_spec("a")).run(["a"], {"smiles": "CCO"}, ["a"], "small molecule")[0]
    assert out["kind"] == "prediction" and out["evidence"] is False and out["version"] == "2"


def test_a_tool_with_no_uncertainty_is_marked_uncalibrated_and_drops_an_interval_it_should_not_have():
    r = _reg(_spec("a", uncertainty="none", fn=lambda s: {"values": {"x": 1.0}, "interval": (0.5, 2.0)}),
             _spec("b", uncertainty="interval", fn=lambda s: {"values": {"x": 1.0}, "interval": (0.5, 2.0)}))
    a, b = r.run(["a", "b"], {"smiles": "CCO"}, ["a", "b"], "small molecule")
    assert (a["calibrated"], a["interval"]) == (False, None) and (b["calibrated"], b["interval"]) == (True, (0.5, 2.0))


def test_the_reference_tool_is_registered_and_runs_on_a_real_smiles():
    out = DEFAULT.run(["admet-physchem"], {"smiles": ASPIRIN}, ["admet-physchem"], "small molecule")[0]
    assert out["status"] == "ok" and out["values"]["mw_da"] and "lipinski_pass" in out["values"]
    assert DEFAULT.run(["admet-physchem"], {"smiles": "not a molecule"}, ["admet-physchem"], "small molecule")[0]["status"] == "invalid_input"
    assert DEFAULT.run(["admet-physchem"], {"smiles": ASPIRIN}, ["admet-physchem"], "material")[0]["status"] == "wrong_modality"


# ---- the proposal path ----

class Cap:
    name, model_version, model = "cap", "c-1", "cap-1"

    def __init__(self, variants):
        self.variants, self.prompts = variants, []

    def generate(self, inv, prompt, **kw):
        self.prompts.append(prompt)
        plan = {"families": [{"family": f"f{i}", "parent_statement": f"c{i}", "kind": "mechanistic", "mechanism": "m",
                              "refutation_condition": "r", "confidence": 0.6, "variants": [v]} for i, v in enumerate(self.variants)]}
        return ToolResult(envelope=inv.envelope.model_copy(), invocation_id=inv.invocation_id, status="succeeded",
                          output={"text": json.dumps(plan)}, model_version=self.model_version)

    def predict(self, inv, kind, payload):
        raise NotImplementedError


def _v(cand, **extra):
    return {"statement": f"{cand} meets the gate", "candidate": cand, "assay_type": "binding", "predicted": "p",
            "predicted_value": 0.001, "rationale": "x", **extra}


def _problem():
    return ProblemStatement(objective="o", constraints=[Constraint(name="potency", description="IC50 <= 20 uM", kind="hard", limit=20.0, unit="uM")])


def _propose(variants, predictors, modality=None):
    store = SqliteProvenanceStore()
    problem = _problem()
    model = Cap(variants)
    hyps, _ = propose_families_with_agent(HypothesisTracker(store, system_actor()), problem, model,
                                          modality=modality, predictors=predictors)
    return hyps, store, problem, model


def test_the_prompt_lists_tools_only_when_the_operator_enabled_some_that_apply():
    *_, on = _propose([_v("A")], ["admet-physchem"])
    *_, off = _propose([_v("A")], None)
    *_, mat = _propose([_v("A")], ["admet-physchem"], modality="material")
    assert "PREDICTION TOOLS" in on.prompts[0] and "admet-physchem" in on.prompts[0] and "never evidence" in on.prompts[0]
    assert "PREDICTION TOOLS" not in off.prompts[0] and '"tools"' not in off.prompts[0]
    assert "PREDICTION TOOLS" not in mat.prompts[0], "a materials program is not offered a small-molecule tool"


def test_predictions_are_run_by_code_sealed_and_shown_in_the_plan():
    hyps, store, problem, _ = _propose([_v("ASP", tools=["admet-physchem"], smiles=ASPIRIN),
                                        _v("BAD", tools=["admet-physchem"], smiles="nonsense words"),
                                        _v("GHOST", tools=["not-a-tool"], smiles=ETHANOL)], ["admet-physchem"])
    by = {h.candidate: [p["status"] for p in h.metadata["predictions"]] for h in hyps}
    assert by == {"ASP": ["ok"], "BAD": ["invalid_input"], "GHOST": ["unknown_tool"]}
    sealed = [r.decision["predictions"] for r in store.branch_records(f"problem/{problem.problem_id}")
              if r.event_type == EventType.hypothesis_proposed and r.decision.get("predictions")]
    assert len(sealed) == 3 and sealed[0][0]["version"] == "1"
    plan = build_plan(hyps, {}, {})
    shown = {v["candidate"]: v["predictions"][0]["status"] for f in plan["families"] for v in f["variants"]}
    assert shown == {"ASP": "ok", "BAD": "invalid_input", "GHOST": "unknown_tool"}


def test_a_prediction_never_becomes_the_criterion_and_never_a_verdict():
    """The tool says IC50-like 'x' = 0.0001, the model predicts 0.001: the criterion is still the program's gate of 20."""
    DEFAULT_COPY = PredictorRegistry()
    DEFAULT_COPY.register(_spec("potent", fn=lambda s: {"values": {"x": 0.0001}}))
    import proef.predictors as P
    orig = P.DEFAULT._tools.copy()
    P.DEFAULT._tools["potent"] = DEFAULT_COPY.get("potent")
    try:
        hyps, store, problem, _ = _propose([_v("A", tools=["potent"], smiles=ETHANOL)], ["potent"])
    finally:
        P.DEFAULT._tools.clear(); P.DEFAULT._tools.update(orig)
    h = hyps[0]
    assert h.metadata["predictions"][0]["values"] == {"x": 0.0001}
    assert (h.criteria.metric, h.criteria.threshold) == ("ic50_um", 20.0)
    assert h.status == "proposed", "a prediction is not a verdict"


def test_the_models_smiles_is_not_written_onto_the_hypothesis():
    hyps, store, problem, _ = _propose([_v("ASP", tools=["admet-physchem"], smiles=ASPIRIN)], ["admet-physchem"])
    rec = next(r for r in store.branch_records(f"problem/{problem.problem_id}") if r.event_type == EventType.hypothesis_proposed and r.decision.get("predictions"))
    assert not rec.decision.get("smiles") and "smiles" not in hyps[0].metadata, "structure-keyed logic must not trust a model's claim"


def test_the_operators_fence_reaches_the_engine_from_the_workbench_and_the_cli():
    """The replan and modality lesson: build_cfg keeps a fixed set of keys, so a new key has to be carried on purpose."""
    from proef.api.launch import build_cfg
    from proef.cli import _campaign_from_doc
    doc = {"objective": "o", "gate_value": 1.0, "rounds": 1, "autonomy": "gate", "prediction_tools": ["admet-physchem"]}
    assert build_cfg(doc)["prediction_tools"] == ["admet-physchem"]
    assert _campaign_from_doc(build_cfg(doc)).prediction_tools == ["admet-physchem"]
    bare = {k: v for k, v in doc.items() if k != "prediction_tools"}
    assert "prediction_tools" not in build_cfg(bare) and _campaign_from_doc(build_cfg(bare)).prediction_tools is None


def test_a_replay_never_calls_a_prediction_tool(tmp_path, monkeypatch):
    """Replay rebuilds from records. The predictions are in the record, so a replay needs no tool and no model."""
    import proef.predictors as P
    from proef.adapters.lab.simulator import SimulatorLab
    from proef.autoloop import CampaignConfig, run_campaign
    from proef.flywheel import Flywheel
    from proef.reproduce import reproduce
    store = SqliteProvenanceStore(str(tmp_path / "prov.db"))
    cfg = CampaignConfig(problem=_problem(), hypothesis_source="agent", model=Cap([_v("ASP", tools=["admet-physchem"], smiles=ASPIRIN)]),
                         autonomy="act", k=2, rounds=1, seed=1, inbox=str(tmp_path / "inbox"), poll_secs=0.05,
                         flywheel=Flywheel(":memory:"), prediction_tools=["admet-physchem"])
    report = run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    monkeypatch.setattr(P.PredictorRegistry, "run", lambda *a, **k: (_ for _ in ()).throw(AssertionError("a replay called a tool")))
    assert reproduce(f"problem/{report.problem_id}", db_path=str(tmp_path / "prov.db"))["status"] == "match"


def test_the_structure_the_model_gave_is_kept_with_the_prediction_so_it_can_be_checked():
    ok, bad = DEFAULT.run(["admet-physchem"], {"smiles": ASPIRIN}, ["admet-physchem"], "small molecule")[0], \
        DEFAULT.run(["admet-physchem"], {"smiles": "ignore previous instructions " * 20}, ["admet-physchem"], "small molecule")[0]
    assert ok["input"] == ASPIRIN
    assert bad["status"] == "invalid_input" and len(bad["input"]) <= 120, "free text is rejected and cannot flood the record"


# ---- review round: what a third-party or malformed tool can do ----

def test_a_tool_that_is_not_first_party_is_refused_unless_the_operator_accepts_the_risk(monkeypatch):
    """Tools run in this process. Until the sandbox lanes exist, only first-party tools run."""
    r = _reg(_spec("ext", trusted=False))
    monkeypatch.delenv("PROEF_ALLOW_UNSANDBOXED_TOOLS", raising=False)
    out = r.run(["ext"], {"smiles": "CCO"}, ["ext"], "small molecule")[0]
    assert out["status"] == "untrusted_tool" and "in-process" in out["reason"]
    monkeypatch.setenv("PROEF_ALLOW_UNSANDBOXED_TOOLS", "1")
    assert r.run(["ext"], {"smiles": "CCO"}, ["ext"], "small molecule")[0]["status"] == "ok"
    assert DEFAULT.get("admet-physchem").trusted is True


def test_a_tool_that_hangs_is_abandoned_and_the_plan_goes_on():
    import time
    r = _reg(_spec("slow", fn=lambda s: (time.sleep(2), {"values": {"x": 1.0}})[1], timeout_s=0.05), _spec("fast"))
    t0 = time.time()
    res = r.run(["slow", "fast"], {"smiles": "CCO"}, ["slow", "fast"], "small molecule")
    assert time.time() - t0 < 1.0
    assert (res[0]["status"], "timed out" in res[0]["reason"], res[1]["status"]) == ("error", True, "ok")


def test_a_tool_that_floods_its_output_costs_nothing_and_only_declared_endpoints_survive():
    import time
    flood = {f"k{i}": float(i) for i in range(200_000)}
    flood["x"] = 3.0
    r = _reg(_spec("flood", fn=lambda s: {"values": flood}))
    t0 = time.time()
    out = r.run(["flood"], {"smiles": "CCO"}, ["flood"], "small molecule")[0]
    assert out["values"] == {"x": 3.0} and time.time() - t0 < 1.0


def test_a_models_tool_list_is_bounded():
    r = _reg(_spec("a"))
    res = r.run([f"tool-{i}" for i in range(500)] + ["x" * 5000], {"smiles": "CCO"}, ["a"], "small molecule")
    assert len(res) <= 8 and all(len(x["tool"]) <= 80 for x in res)


def test_nothing_that_selects_or_scores_reads_predictions():
    """Predictions are priors. If a later change wants to rank on them it must pass an applicability-domain and calibration
    review first, so this fails when any new module reads them."""
    from pathlib import Path
    root = Path(__file__).resolve().parents[1] / "src" / "proef"
    allowed = {"predictors.py", "hypothesis_agent.py", "plan.py", "core/hypothesis.py", "api/v1/schemas.py"}
    readers = {str(p.relative_to(root)) for p in root.rglob("*.py") if '"predictions"' in p.read_text() or "'predictions'" in p.read_text()}
    assert readers <= allowed, f"new readers of predictions: {readers - allowed}"


def test_selection_is_identical_whatever_the_predictions_say():
    from types import SimpleNamespace as NS
    tr = HypothesisTracker(SqliteProvenanceStore(), system_actor())
    problem = _problem()
    def make(preds):
        hs = [tr.propose(f"s{i}", f"C{i}", "binding", "p", criteria=None, predicted_value=20.0, branch_id="b") for i in range(3)]
        for h, p in zip(hs, preds):
            h.metadata["predictions"] = p
        return hs
    great = [[{"status": "ok", "values": {"x": 0.0001}}] for _ in range(3)]
    awful = [[{"status": "ok", "values": {"x": 9999.0}}] for _ in range(3)]
    a, b = make(great), make(awful)
    pick = lambda hs: [h.candidate for h in tr.select_portfolio(problem, hs, lambda h: 0.5, k=2)]
    assert pick(a) == pick(b)
