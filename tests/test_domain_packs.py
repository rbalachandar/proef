"""Phase 1 of docs/LLM-ORCHESTRATION.md: domain knowledge is DATA in the modality pack, so the hypothesis code does not
fork per domain, and the gate stays the program's in every domain."""
import json

from proef.adapters.store.sqlite import SqliteProvenanceStore
from proef.core.contracts import EventType, ToolResult
from proef.core.hypothesis import Constraint, HypothesisTracker, ProblemStatement
from proef.core.interfaces import system_actor
from proef.hypothesis_agent import propose_families_with_agent
from proef.modality import PACKS, metric_for_assay, pack_or_default


class Capture:
    """A scripted model that records the prompt it was given and answers with a fixed plan."""
    name, model_version, model = "cap", "c-1", "cap-1"

    def __init__(self, plan):
        self.plan, self.prompts = plan, []

    def generate(self, inv, prompt, **kw):
        self.prompts.append(prompt)
        return ToolResult(envelope=inv.envelope.model_copy(), invocation_id=inv.invocation_id, status="succeeded",
                          output={"text": json.dumps(self.plan)}, model_version=self.model_version)

    def predict(self, inv, kind, payload):
        raise NotImplementedError


def _plan(candidate, assay):
    return {"families": [{"family": "f", "parent_statement": "claim", "kind": "mechanistic", "mechanism": "m",
                          "refutation_condition": "r", "confidence": 0.6,
                          "variants": [{"statement": f"{candidate} meets the gate", "candidate": candidate,
                                        "assay_type": assay, "predicted": "p", "predicted_value": 0.0001,
                                        "rationale": "x"}]}]}


def _problem(metric, desc, limit, unit):
    return ProblemStatement(objective="meet the gate", constraints=[
        Constraint(name=metric, description=desc, kind="hard", limit=limit, unit=unit)])


def _run(modality, plan, problem):
    store = SqliteProvenanceStore()
    tr = HypothesisTracker(store, system_actor())
    model = Capture(plan)
    hyps, _ = propose_families_with_agent(tr, problem, model, modality=modality)
    recs = [r for r in store.branch_records(f"problem/{problem.problem_id}") if r.event_type == EventType.hypothesis_proposed
            and "criteria" in (r.decision or {})]   # a family's parent claim carries no criteria
    return model, hyps, recs


def test_the_default_prompt_is_the_one_used_before_packs_carried_domain_data():
    model, _, _ = _run(None, _plan("erlotinib", "binding"), _problem("potency", "IC50 <= 20 uM", 20.0, "uM"))
    p = model.prompts[0]
    assert "hypothesis lead of a drug-discovery program" in p and "e.g. erlotinib" in p
    assert "molecules, binding, pathway" in p and "the compound or sample name only" in p


def test_a_materials_program_is_not_prompted_with_drug_discovery_wording():
    model, _, _ = _run("material", _plan("LLZO", "conductivity"),
                       _problem("conductivity", "conductivity >= 1.0 mS/cm", 1.0, "mS/cm"))
    p = model.prompts[0]
    assert "materials-discovery program" in p and "Li7La3Zr2O12" in p and "composition, structure, phase" in p
    assert "drug-discovery" not in p and "erlotinib" not in p


def test_a_biologics_program_asks_for_a_sequence_name():
    model, _, _ = _run("biologic", _plan("b1", "binding"), _problem("potency", "IC50 <= 20 nM", 20.0, "nM"))
    assert "sequence or sample name only, e.g. binder-017" in model.prompts[0]


def test_a_unknown_or_empty_modality_falls_back_to_small_molecule():
    assert pack_or_default("nonsense").name == "small molecule" == pack_or_default(None).name == pack_or_default("").name


def test_the_gate_is_the_programs_in_every_domain_whatever_the_model_predicts():
    cases = [("small molecule", "binding", _problem("potency", "IC50 <= 20 uM", 20.0, "uM"), "ic50_um", "min"),
             ("material", "tensile", _problem("tensile_mpa", "tensile_mpa >= 50 MPa", 50.0, "MPa"), "tensile_mpa", "max"),
             # a model that names a chemistry assay for a materials program does not turn the gate into an IC50
             ("material", "binding", _problem("conductivity", "conductivity >= 1.0 mS/cm", 1.0, "mS/cm"), "conductivity", "max"),
             ("material", "conductivity", _problem("conductivity", "conductivity >= 1.0 mS/cm", 1.0, "mS/cm"), "conductivity", "max")]
    for modality, assay, problem, metric, direction in cases:
        _, _, recs = _run(modality, _plan("X", assay), problem)
        crit = recs[0].decision["criteria"]
        assert (crit["metric"], crit["direction"]) == (metric, direction), (modality, assay, crit)
        assert crit["threshold"] == problem.constraints[0].limit, "the model's predicted_value never becomes the gate"


def test_which_assays_are_dose_response_is_pack_data_not_code():
    assert metric_for_assay("binding", "small molecule") == "ic50_um"
    assert metric_for_assay("binding", "material") is None, "a materials pack has no dose-response fits"
    assert metric_for_assay("tensile", "small molecule") is None
    assert PACKS["material"].dose_response_assays == frozenset()


def test_there_is_one_definition_of_the_dose_response_assays():
    from proef.adapters.lab.simulator import DOSE_RESPONSE_ASSAYS
    from proef.modality import DOSE_RESPONSE
    assert DOSE_RESPONSE_ASSAYS is DOSE_RESPONSE


def test_the_modality_reaches_the_engine_from_the_workbench_and_the_cli():
    """The `replan` lesson: build_cfg keeps a fixed set of keys, so a new key has to be carried on purpose."""
    from proef.api.launch import build_cfg
    from proef.cli import _campaign_from_doc
    doc = {"objective": "o", "gate_value": 1.0, "rounds": 1, "autonomy": "gate", "modality": "material"}
    assert build_cfg(doc)["modality"] == "material"
    assert _campaign_from_doc(build_cfg(doc)).modality == "material"
    assert "modality" not in build_cfg({k: v for k, v in doc.items() if k != "modality"})
    assert _campaign_from_doc(build_cfg({k: v for k, v in doc.items() if k != "modality"})).modality is None


# ---- unknown modalities never fall back silently (found in this phase's hole review) ----

def test_the_api_refuses_an_unknown_modality_and_normalizes_a_known_one():
    import pytest
    from pydantic import ValidationError
    from proef.api.v1.schemas import ProgramCreate
    with pytest.raises(ValidationError, match="unknown modality"):
        ProgramCreate(name="p", modality="materials")      # a typo must not become small molecule
    assert ProgramCreate(name="p", modality=" Material.").modality == "material"
    assert ProgramCreate(name="p").modality == "small molecule"


def test_the_engine_refuses_an_unknown_modality():
    import pytest
    from proef.adapters.lab.simulator import SimulatorLab
    from proef.autoloop import CampaignConfig, run_campaign
    cfg = CampaignConfig(problem=_problem("potency", "IC50 <= 20 uM", 20.0, "uM"), modality="materials",
                         hypotheses=[{"statement": "s", "candidate": "A", "assay_type": "binding", "predicted": "p",
                                      "predicted_value": 20.0}])
    with pytest.raises(ValueError, match="unknown modality"):
        run_campaign(cfg, SimulatorLab(seed=1), SqliteProvenanceStore(), notify=lambda m: None)


def test_the_workbench_modality_list_is_the_pack_list():
    """The new-program dialog hardcodes the options. A pack added in modality.py must appear there."""
    import re
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "web/src/features/launch/NewProgramDialog.tsx").read_text()
    listed = re.search(r"\[((?:'[^']+',?\s*)+)\]\.map\(\(m\) => <option", src)
    assert listed, "the modality options moved: update this test"
    assert set(re.findall(r"'([^']+)'", listed.group(1))) == set(PACKS)


def test_the_assay_a_listed_candidate_is_tested_by_comes_from_the_pack():
    """launch.py hardcoded `binding if small molecule else custom`. It is now the pack's data, with the same values."""
    from proef.api.launch import build_cfg
    base = {"objective": "o", "gate_value": 1.0, "candidates": "A,B", "rounds": 1}
    assert {h["assay_type"] for h in build_cfg({**base})["hypotheses"]} == {"binding"}
    assert {h["assay_type"] for h in build_cfg({**base, "modality": "small molecule"})["hypotheses"]} == {"binding"}
    for m in ("material", "biologic", "agrochemical"):
        assert {h["assay_type"] for h in build_cfg({**base, "modality": m})["hypotheses"]} == {"custom"}, m
