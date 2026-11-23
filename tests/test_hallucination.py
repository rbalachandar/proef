"""The deterministic detectors for what a model fabricates, and the eval that scores them (5F/5G narrative kinds)."""
import pytest

from proef import groundedness as g
from proef.evals.hallucination import FPR_MAX, RECALL_MIN, _t_hallucination_detectors, score
from proef.grounding import library_secrets


# ---- the eval ----

def test_every_category_meets_the_thresholds_set_before_the_first_run():
    res = score()
    assert set(res) == {"untestable_refutation", "invented_candidate", "bad_structure", "quoted_number", "gate_misstated", "state_claims", "ruled_out",
                        "phantom_tool", "fabricated_citation", "unsupported_value", "claimed_refused"}
    for cat, v in res.items():
        assert v["recall"] >= RECALL_MIN and v["false_positive_rate"] <= FPR_MAX, (cat, v)


def test_the_hard_cases_are_in_the_fixture_and_show_up_as_limits_not_hidden():
    res = score()
    assert res["untestable_refutation"]["missed"] and res["quoted_number"]["missed"], "a vague clause and a number-free claim are missed, and reported"
    assert res["state_claims"]["missed"], "a wrong claim with no name, number or verdict word cannot be checked, and is reported"
    r = _t_hallucination_detectors()
    assert r.passed and "not a real model" in r.notes and r.metrics["categories"]["quoted_number"]["missed"]


# ---- the detectors ----

def test_a_refuted_if_clause_must_name_something_that_could_be_observed():
    assert g.untestable_refutation("") == "empty" and g.untestable_refutation("If it does not work.") == "circular"
    assert g.untestable_refutation("it is wrong") == "no observable or threshold"
    assert g.untestable_refutation("No saturable binding below 20 uM") is None


def test_a_threshold_in_the_wrong_unit_is_a_misstated_gate_but_the_same_value_in_another_unit_is_not():
    assert g.gate_misstated("The gate is IC50 below 20 nM.", 20.0)[0]["stated_um"] == 0.02
    assert g.gate_misstated("The gate is IC50 below 0.02 mM.", 20.0) == [] and g.gate_misstated("below 20 nM", 20.0) == [], "no gate wording, no claim"


def test_a_quoted_value_must_match_the_data_in_any_unit_and_a_dose_is_not_a_measurement():
    f = [{"candidate": "gefitinib", "observed_um": 2.0, "ci_um": (1.5, 2.6)}]
    assert g.quoted_number_problems("gefitinib gave IC50 of 200 nM", f)[0]["quoted_um"] == 0.2
    assert g.quoted_number_problems("gefitinib: IC50 = 2000 nM", f) == [] and g.quoted_number_problems("gefitinib was tested at 10 uM", f) == []


def test_a_name_outside_the_library_is_flagged_only_when_a_library_was_offered():
    assert g.not_in_library("lapatinib", ["gefitinib"]) and not g.not_in_library(" Gefitinib ", ["gefitinib"]) and not g.not_in_library("x", [])


def test_a_structure_must_parse_and_agree_with_the_one_the_library_holds():
    lib = {"aspirin": "CC(=O)Oc1ccccc1C(=O)O"}
    assert g.structure_problem("C1CC(", "x"), "an unparseable string is caught with or without RDKit"
    assert g.structure_problem("OC(=O)c1ccccc1OC(C)=O", "aspirin") is None
    pytest.importorskip("rdkit", reason="comparing against the library's structure needs RDKit's canonical form")
    assert "different structure" in g.structure_problem("CCN(CC)CC", "aspirin", lib)
    assert g.structure_problem("OC(=O)c1ccccc1OC(C)=O", "aspirin", lib) is None, "a different spelling of the same molecule passes"


def test_a_repeated_ruled_out_idea_is_found_by_candidate_or_by_family():
    neg = [{"candidate": "erlotinib", "family": "atp_site", "outcome": "invalidated"}]
    assert g.ruled_out("Erlotinib", None, neg)["match"] == "candidate" and g.ruled_out("new", "atp_site", neg)["match"] == "family"
    assert g.ruled_out("osimertinib", "covalent", neg) is None


def test_a_claimed_tool_call_with_nothing_on_the_ledger_is_a_phantom_and_a_negated_claim_is_not():
    called, offered = {"pubmed_search"}, {"predict", "pubmed_search"}
    assert g.phantom_tool_claims("I ran predict on the structure", called, offered) == ["predict"]
    assert g.phantom_tool_claims("I did not run predict", called, offered) == [] and g.phantom_tool_claims("I never used predict", called, offered) == []
    assert g.phantom_tool_claims("I searched pubmed_search", called, offered) == []


# ---- what a private library must not send out (found while building this) ----

def test_a_librarys_ids_structures_and_sequences_are_protected_from_queries_unless_declared_public():
    lib = [{"sample_id": "C-101", "smiles": "CC(=O)Oc1ccccc1C(=O)O"}, {"sample_id": "BIO-7", "sequence": "MKTAYIAKQR"}, {"id": "gefitinib"}]
    assert {"C-101", "CC(=O)Oc1ccccc1C(=O)O", "BIO-7", "MKTAYIAKQR", "gefitinib"} <= library_secrets(lib)
    assert "gefitinib" not in library_secrets(lib, public=("Gefitinib",)) and "C-101" in library_secrets(lib, public=("Gefitinib",))


def test_a_library_id_in_a_query_is_blocked_before_it_reaches_the_network():
    import json
    from proef.adapters.store.sqlite import SqliteProvenanceStore
    from proef.core.contracts import ToolResult
    from proef.core.hypothesis import ProblemStatement
    from proef.grounding import gather
    from tests.test_grounding import Pubmed

    class Ask:
        name, model_version, model = "s", "s-1", "s-1"

        def __init__(self):
            self.n = 0

        def generate(self, inv, prompt, **kw):
            self.n += 1
            q = ["C-101 binding", "CC(=O)Oc1ccccc1C(=O)O solubility", "EGFR kinase"][min(self.n - 1, 2)]
            text = {"tool": "pubmed_search", "args": {"query": q}} if self.n <= 3 else {"done": "d"}
            return ToolResult(envelope=inv.envelope.model_copy(), invocation_id=inv.invocation_id, status="succeeded", output={"text": json.dumps(text)})
    pm = Pubmed()
    lib = [{"sample_id": "C-101", "smiles": "CC(=O)Oc1ccccc1C(=O)O"}]
    gather(Ask(), ProblemStatement(objective="o", problem_id="p-1"), SqliteProvenanceStore(":memory:"), {"pubmed": pm}, ("pubmed",),
           protected=library_secrets(lib) | {"p-1"})
    assert pm.queries == ["EGFR kinase"], "the id and the structure never left"


# ---- plan review ----

def test_plan_review_warns_about_what_a_model_made_up_and_the_api_schema_accepts_it():
    from proef.api.v1.schemas import Plan
    from proef.plan import build_plan

    class H:
        def __init__(self, hid, cand, family, refuted, smiles=None):
            self.hypothesis_id, self.candidate, self.statement, self.predicted, self.rationale, self.assay_type = hid, cand, "s", "p", "r", "binding"
            self.family, self.mechanism, self.refutation_condition, self.citation, self.citation_status = family, "m", refuted, "", ""
            self.metadata = {"smiles": smiles} if smiles else {}
    hs = [H("a", "tarcevaxin", "f1", "If it does not work."), H("b", "gefitinib", "f2", "No binding below 20 uM", smiles="C1CC("),
          H("c", "erlotinib", "atp_site", "No binding below 20 uM")]
    plan = build_plan(hs, {}, {}, library=["gefitinib", "erlotinib"], structures={}, negatives=[{"candidate": "erlotinib", "family": "atp_site", "outcome": "invalidated"}])
    kinds = sorted(w["kind"] for w in plan["warnings"])
    assert kinds == ["bad_structure", "not_in_library", "ruled_out", "untestable_refutation"], kinds
    parsed = Plan.model_validate(plan)
    assert {w.kind for w in parsed.warnings} == set(kinds)


def test_a_clean_plan_raises_none_of_these_warnings():
    from proef.plan import build_plan

    class H:
        hypothesis_id, candidate, statement, predicted, rationale, assay_type = "a", "gefitinib", "s", "p", "r", "binding"
        family, mechanism, refutation_condition, citation, citation_status, metadata = "f", "m", "No binding below 20 uM", "", "", {}
    assert build_plan([H()], {}, {}, library=["gefitinib"], structures={}, negatives=[])["warnings"] == []


# ---- a sentence that states the right gate is not misstating it (found in a real model's text) ----

def test_a_prediction_in_a_sentence_that_also_states_the_right_gate_is_not_a_misstated_gate():
    t = "At ATP near the Km, AZ191 inhibits the enzyme with an IC50 below 1 uM and therefore below the 20-uM gate."
    assert g.gate_misstated(t, 20.0) == []
    assert g.gate_misstated("The gate is IC50 below 20 nM.", 20.0), "a wrong gate is still caught"


# ---- claims about the campaign's own state ----

def test_a_claim_about_a_verdict_a_queue_or_a_budget_is_checked_against_the_campaign():
    v = {"AZ191": "inconclusive", "harmine": "invalidated"}
    assert [x["kind"] for x in g.state_claims("AZ191 was confirmed.", v, 2, 40)] == ["verdict"]
    assert g.state_claims("harmine was refuted.", v, 2, 40) == [], "refuted and invalidated are the same class"
    assert [x["claimed"] for x in g.state_claims("Three hypotheses are queued.", v, 1, 40)] == [3]
    assert [x["claimed"] for x in g.state_claims("Only 12 lab runs remain.", v, 1, 48)] == [12]
    assert g.state_claims("Three hypotheses are queued and 48 lab runs remain.", {}, 3, 48) == []


# ---- hooks: plan review, follow-ups, the orchestrator ----

def test_plan_review_flags_a_quoted_value_and_a_misstated_gate_against_the_results():
    from proef.api.v1.schemas import Plan
    from proef.plan import build_plan

    class H:
        hypothesis_id, candidate, predicted, assay_type = "a", "gefitinib", "p", "binding"
        statement, rationale = "gefitinib will stay potent", "Earlier gefitinib gave IC50 of 0.2 uM, so it clears the gate of below 20 nM."
        family, mechanism, refutation_condition, citation, citation_status, metadata = "f", "m", "No binding below 20 uM", "", "", {}
    plan = build_plan([H()], {}, {}, findings=[{"candidate": "gefitinib", "observed_um": 2.0, "ci_um": (1.5, 2.6)}], gate_um=20.0)
    kinds = sorted(w["kind"] for w in plan["warnings"])
    assert kinds == ["gate_misstated", "quoted_number"], kinds
    assert "0.2 uM" in next(w for w in plan["warnings"] if w["kind"] == "quoted_number")["reason"]
    assert {w.kind for w in Plan.model_validate(plan).warnings} == set(kinds)


def test_a_followup_reason_that_misquotes_the_result_is_flagged_on_its_record_and_changes_nothing(tmp_path):
    import tests.test_followups as F
    ids_answer = lambda ids: [{"hypothesis_id": ids["C1"], "action": "follow_up", "replicates": 6, "concentrations_uM": F.GOOD,  # noqa: E731
                               "why": "C1 gave IC50 of 0.1 uM so it needs a tighter ladder"}]
    _, _, recs, _, _ = F._run(tmp_path, answer=ids_answer)
    prop = next(r.decision for r in recs if (r.decision or {}).get("kind") == "followup_proposal")
    assert [f["kind"] for f in prop["flags"]] == ["quoted_number"] and prop["validation"]["ok"], "advisory: the follow-up still ran"


def test_an_orchestrators_reason_is_checked_against_the_state_it_was_shown(tmp_path):
    import json

    import tests.test_followups as F
    from proef.adapters.lab.simulator import SimulatorLab
    from proef.adapters.store.sqlite import SqliteProvenanceStore
    from proef.autoloop import CampaignConfig, ResourceBudget, run_campaign
    from proef.core.contracts import ToolResult
    from proef.core.hypothesis import Constraint, ProblemStatement
    from proef.flywheel import Flywheel

    class Steering(F.Model):
        def generate(self, inv, prompt, **kw):
            if "You steer a research campaign" in prompt:
                text = json.dumps({"action": "next_round", "why": "Proceed: 999 lab runs remain."})
                return ToolResult(envelope=inv.envelope.model_copy(), invocation_id=inv.invocation_id, status="succeeded", output={"text": text})
            return super().generate(inv, prompt, **kw)
    store = SqliteProvenanceStore(str(tmp_path / "p.db"))
    cfg = CampaignConfig(problem=ProblemStatement(objective="o", constraints=[Constraint(name="potency", description="IC50 <= 20 uM", kind="hard", limit=20.0, unit="uM")]),
                         hypothesis_source="agent", model=Steering(F._answer), autonomy="act", k=8, max_families=8, rounds=2, seed=1, orchestrator="model",
                         inbox=str(tmp_path / "in"), poll_secs=0.05, flywheel=Flywheel(":memory:"), budget=ResourceBudget(max_total_runs=50, max_hypotheses_total=30))
    rep = run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    ch = [r.decision for r in store.branch_records(f"problem/{rep.problem_id}") if (r.decision or {}).get("kind") == "orchestrator_choice"]
    assert ch and ch[0]["chosen"] == "next_round" and ch[0]["source"] == "model"
    assert [f["kind"] for f in ch[0]["flags"]] == ["state_runs_left"] and ch[0]["flags"][0]["claimed"] == 999


# ---- found by running the detectors on a real model's orchestrator text (Nov 21 experiment) ----

def test_a_verdict_word_used_as_an_adjective_is_not_a_claim_about_the_candidate():
    v = {"AZ191": "inconclusive"}
    assert g.state_claims("The inconclusive AZ191 result may benefit from a validated follow-up before proceeding.", v) == []
    assert g.state_claims("AZ191 needs a confirmed assay.", v) == []
    assert [x["claimed"] for x in g.state_claims("AZ191 was validated, so proceed.", v)] == ["validated"], "a real wrong claim is still caught"
    assert g.state_claims("AZ191 remains inconclusive.", v) == []


def test_a_published_value_quoted_beside_a_measured_one_is_not_a_misquote_but_a_wrong_measurement_still_is():
    f = [{"candidate": "CHEMBL1396654", "observed_um": 18.791, "ci_um": None}]
    assert g.quoted_number_problems("Prioritize CHEMBL1396654, whose reported DYRK1B IC50 is 506 nM (ChEMBL CHEMBL1396654).", f) == []
    assert g.quoted_number_problems("CHEMBL1396654 gave IC50 of 0.5 uM", f)[0]["quoted_um"] == 0.5


def test_a_chembl_id_citation_is_resolved_at_chembl_and_a_missing_one_is_invalid_not_unreachable(tmp_path):
    import json as _json
    import urllib.error
    from pathlib import Path
    from unittest import mock

    from proef import citations
    with mock.patch.object(citations, "CACHE", Path(tmp_path) / "c.json"):
        ok = citations.verify("CHEMBL428690; PMID 1", lambda url: _json.dumps({"chembl_id": "CHEMBL428690", "entity_type": "COMPOUND"}).encode()
                              if "chembl_id_lookup/CHEMBL428690" in url else b"{}")
        assert citations.parse("CHEMBL3038473; CHEMBL3055") == ("chembl", "CHEMBL3038473")
        assert ok["status"] == "verified" and ok["kind"] == "chembl"

        def missing(url):
            raise urllib.error.HTTPError(url, 404, "Not Found", None, None)
        assert citations.verify("CHEMBL999999999", missing)["status"] == "invalid"

        def down(url):
            raise urllib.error.HTTPError(url, 503, "Unavailable", None, None)
        assert citations.verify("CHEMBL1", down)["status"] == "check-failed", "an outage is not evidence of a fake citation"


def test_digits_inside_a_chembl_id_are_not_read_as_a_pmid_and_a_multi_id_citation_needs_every_id_to_resolve(tmp_path):
    import json as _json
    from pathlib import Path
    from unittest import mock

    from proef import citations
    assert citations.parse("CHEMBL428690; PMID 1") == ("chembl", "CHEMBL428690"), "428690 is part of the ChEMBL id, not a PMID"
    assert citations.parse("CHEMBL296468; CHEMBL428690; PMID:23528858") == ("pmid", "23528858")
    assert citations.parse("23528858") == ("pmid", "23528858") and citations.parse("PMID 43005627") == ("pmid", "43005627")
    assert citations.ids_of("PMID 43005627; PMID: 23528858; CHEMBL428690, 10.1056/NEJMoa1411817.") == [
        ("pmid", "43005627"), ("pmid", "23528858"), ("chembl", "CHEMBL428690"), ("doi", "10.1056/NEJMoa1411817")]

    def fetch(url):
        if "esummary" in url:
            pid = url.split("id=")[1].split("&")[0]
            return _json.dumps({"result": {pid: {"title": "t", "pubdate": "2020", "source": "J"}}} if pid == "23528858" else {"result": {}}).encode()
        return _json.dumps({"chembl_id": "CHEMBL428690", "entity_type": "COMPOUND"}).encode()
    with mock.patch.object(citations, "CACHE", Path(tmp_path) / "c.json"):
        assert citations.verify_each("PMID 23528858; CHEMBL428690", fetch)["status"] == "verified"
        assert citations.verify_each("PMID 23528858; PMID 99999999", fetch)["status"] == "invalid", "one fabricated id makes the citation invalid"


# ---- what a tool loop says about its own calls and what they returned ----

def test_a_value_in_a_summary_must_be_in_the_results_or_the_task_in_any_unit():
    b = [{"standard_value": "41.0", "units": "nM"}, {"standard_value": "1100.0"}]
    assert g.unsupported_values("IC50 of 41 nM", b) == [] and g.unsupported_values("that is 0.041 uM", b) == []
    assert g.unsupported_values("IC50 of 410 nM", b) == [{"stated": "410 nM"}]
    assert g.unsupported_values("activities of 1,100 nM", b) == [], "a thousands separator is not a second number"
    assert g.unsupported_values("sub-20 uM activities", [], extra="Find a confirmed sub-20uM inhibitor") == [], "the task's own gate is not a finding"
    assert g.unsupported_values("sub-20 uM activities", []) == [{"stated": "20 uM"}]


def test_a_real_value_attached_to_the_wrong_compound_is_caught_and_a_value_after_the_next_name_is_not_blamed_on_the_first():
    b = [{"name": "gefitinib", "standard_value": "900"}, {"name": "erlotinib", "standard_value": "41"}]
    assert g.misattributed_values("gefitinib has an IC50 of 41 nM.", b) == [{"compound": "gefitinib", "stated": "41 nM"}]
    assert g.misattributed_values("erlotinib has an IC50 of 41 nM and gefitinib 900 nM.", b) == []
    assert g.misattributed_values("lapatinib has an IC50 of 41 nM.", b) == [], "no record for that name: nothing to compare against"


def test_a_summary_is_checked_against_the_loops_own_calls_for_phantoms_refused_claims_and_ignored_refusals():
    offered = {"pubmed_search", "chembl_search"}
    calls = [{"tool": "pubmed_search", "status": "refused", "code": "egress_blocked", "sig": "a"},
             {"tool": "pubmed_search", "status": "refused", "code": "egress_blocked", "sig": "a"}]
    kinds = lambda text, calls=calls: [f["kind"] for f in g.loop_narrative_flags(text, calls, offered, [])]  # noqa: E731
    assert kinds("I searched pubmed_search and found strong support.") == ["claimed_refused_call", "ignored_refusal"]
    assert "phantom_call" in kinds("I queried chembl_search for it.")
    assert kinds("The query was refused, so nothing was found.") == ["ignored_refusal"]
    assert g.loop_narrative_flags("I searched pubmed_search.", [{"tool": "pubmed_search", "status": "ok", "sig": "b"}], offered, [{"x": 1}]) == []


def test_every_loop_summary_is_checked_and_a_flag_is_recorded_on_the_ledger_not_hidden(tmp_path):
    import json as _json

    from proef.adapters.store.sqlite import SqliteProvenanceStore
    from proef.core.contracts import ToolResult
    from proef.core.hypothesis import ProblemStatement
    from proef.grounding import gather
    from tests.test_grounding import Pubmed

    class Liar:
        name, model_version, model = "s", "s-1", "s-1"

        def __init__(self):
            self.n = 0

        def generate(self, inv, prompt, **kw):
            self.n += 1
            out = ({"tool": "pubmed_search", "args": {"query": "EGFR kinase"}} if self.n == 1 else
                   {"done": "I also ran chembl_search and it reported an IC50 of 7 nM."})
            return ToolResult(envelope=inv.envelope.model_copy(), invocation_id=inv.invocation_id, status="succeeded", output={"text": _json.dumps(out)})
    store = SqliteProvenanceStore(":memory:")
    g1 = gather(Liar(), ProblemStatement(objective="o", problem_id="p-1"), store, {"pubmed": Pubmed()}, ("pubmed", "chembl"))
    assert {f["kind"] for f in g1.flags} == {"phantom_call", "unsupported_value"}
    rec = next(r.decision for r in store.branch_records("problem/p-1") if (r.decision or {}).get("kind") == "loop_flags")
    assert rec["advisory"] and rec["state"] == "propose" and {f["kind"] for f in rec["flags"]} == {"phantom_call", "unsupported_value"}


def test_without_rdkit_a_truncated_or_garbled_structure_is_still_caught_and_a_valid_one_is_not():
    """CI's minimal job has no RDKit; the fallback check must not wave `C1CC(` through."""
    from proef.predictors import _unbalanced
    for bad in ("C1CC(", "c1ccccc1(", "CC(=O", "C1CCCC", "[NH3+", "C)C", "C%12CC"):
        assert _unbalanced(bad), bad
    for good in ("CCO", "OC(=O)c1ccccc1OC(C)=O", "CC(=O)Oc1ccccc1C(=O)O", "[NH3+]CC([O-])=O", "C%12CCCCC%12", "c1ccc2ccccc2c1", "[13CH4]"):
        assert _unbalanced(good) is None, good
