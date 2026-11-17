"""Phase 2 of docs/LLM-ORCHESTRATION.md: the model picks an instrument from the operator's pool, and CODE checks the pick
on facts (in the pool, declares the assay, is where the campaign runs). The reasoning is never read, a bad pick is shown
and never silently fixed, and a hypothesis the campaign's lab cannot serve never reaches it."""
import json

import pytest

from proef.adapters.lab.simulator import SimulatorLab
from proef.adapters.store.sqlite import SqliteProvenanceStore
from proef.autoloop import CampaignConfig, run_campaign
from proef.core.contracts import EventType, ToolResult
from proef.core.hypothesis import Constraint, HypothesisTracker, ProblemStatement
from proef.core.interfaces import system_actor
from proef.flywheel import Flywheel
from proef.hypothesis_agent import propose_families_with_agent
from proef.labselect import build_menu, check_pick, menu_text
from proef.plan import PlanDecision, build_plan

MENU = [
    {"name": "plate-reader", "assay_types": ["binding", "enzymatic"], "readouts": [], "bsl_max": 2,
     "simulated": False, "hardware_validated": True},
    {"name": "virtual-lab", "assay_types": ["binding", "cytotoxicity"], "readouts": [], "bsl_max": 2,
     "simulated": True, "hardware_validated": False},
    {"name": "undeclared", "assay_types": [], "readouts": [], "bsl_max": None, "simulated": False,
     "hardware_validated": False},
]


# ---- the validator, case by case ----

def test_a_pick_that_holds_up_is_ok_and_names_where_it_runs():
    c = check_pick("plate-reader", "binding", MENU, "plate-reader")
    assert (c["status"], c["ok"], c["runs_on"]) == ("ok", True, "plate-reader")


def test_no_pick_is_unstated_not_a_failure():
    assert check_pick("", "binding", MENU, "plate-reader")["status"] == "unstated"
    assert check_pick(None, "binding", MENU, "plate-reader")["ok"] is True


def test_an_invented_instrument_is_rejected_and_the_run_is_not_redirected():
    c = check_pick("quantum-sequencer", "binding", MENU, "plate-reader")
    assert (c["status"], c["ok"]) == ("invented", False)
    assert c["runs_on"] == "plate-reader" and "not in the operator's pool" in c["reasons"][0]


def test_an_instrument_that_declares_no_such_assay_is_rejected():
    c = check_pick("plate-reader", "cytotoxicity", MENU, "plate-reader")
    assert c["status"] == "lacks" and "declares no 'cytotoxicity' assay" in c["reasons"][0]


def test_a_valid_instrument_that_is_not_where_the_campaign_runs_is_flagged():
    c = check_pick("virtual-lab", "binding", MENU, "plate-reader")
    assert (c["status"], c["ok"]) == ("other_instrument", False)
    assert c["runs_on"] == "plate-reader", "the plan states the truth, not the model's pick"


def test_the_campaign_lab_that_cannot_serve_the_assay_is_cannot_run_and_lists_who_can():
    c = check_pick(None, "cytotoxicity", MENU, "plate-reader")
    assert (c["status"], c["ok"]) == ("cannot_run", False)
    assert "virtual-lab" in c["reasons"][0]


def test_a_simulated_pick_over_a_validated_one_is_flagged():
    c = check_pick("virtual-lab", "binding", MENU, "virtual-lab")
    assert c["ok"] and "simulated_over_validated" in c["flags"]


def test_an_instrument_that_declares_no_assays_is_unverified_not_blocked():
    c = check_pick(None, "binding", MENU, "undeclared")
    assert (c["status"], c["ok"]) == ("unverified", True)


def test_the_models_reasoning_is_never_read():
    a = check_pick("plate-reader", "binding", MENU, "plate-reader")
    assert a == check_pick("plate-reader", "binding", MENU, "plate-reader")
    import inspect
    assert "reason" not in inspect.signature(check_pick).parameters


# ---- the menu: one source for the prompt and the validator ----

def test_the_menu_labels_real_and_simulated_and_comes_from_the_pool():
    from proef.labselect import build_pool
    pool = build_pool([{"simulator": {"seed": 1}}, {"simulator": {"seed": 2, "instruments": {"assays": ["binding"], "bsl_max": 1}}}])
    menu = build_menu(pool)
    assert [m["name"] for m in menu] == ["simulator-0", "simulator-1"] and all(m["simulated"] for m in menu)
    text = menu_text(menu)
    assert "simulator-0" in text and "simulated" in text and "BSL up to 1" in text


class Cap:
    name, model_version, model = "cap", "c-1", "cap-1"

    def __init__(self, variants):
        self.variants, self.prompts = variants, []

    def generate(self, inv, prompt, **kw):
        self.prompts.append(prompt)
        # one family per variant: the portfolio filter takes one variant per family per round
        plan = {"families": [{"family": f"f{i}", "parent_statement": f"claim {i}", "kind": "mechanistic", "mechanism": "m",
                              "refutation_condition": "r", "confidence": 0.6, "variants": [v]}
                             for i, v in enumerate(self.variants)]}
        return ToolResult(envelope=inv.envelope.model_copy(), invocation_id=inv.invocation_id, status="succeeded",
                          output={"text": json.dumps(plan)}, model_version=self.model_version)

    def predict(self, inv, kind, payload):
        raise NotImplementedError


def _v(cand, assay="binding", instrument=None):
    d = {"statement": f"{cand} meets the gate", "candidate": cand, "assay_type": assay, "predicted": "p",
         "predicted_value": 20.0, "rationale": "x"}
    if instrument is not None:
        d["instrument"] = instrument
    return d


PROBLEM = lambda: ProblemStatement(objective="o", constraints=[Constraint(name="potency", description="IC50 <= 20 uM", kind="hard", limit=20.0, unit="uM")])  # noqa: E731


def test_the_prompt_lists_the_instruments_only_when_there_is_a_menu():
    tr = HypothesisTracker(SqliteProvenanceStore(), system_actor())
    with_menu, without = Cap([_v("A")]), Cap([_v("A")])
    propose_families_with_agent(tr, PROBLEM(), with_menu, instruments={"menu": MENU, "runs_on": "plate-reader"})
    propose_families_with_agent(HypothesisTracker(SqliteProvenanceStore(), system_actor()), PROBLEM(), without)
    assert "AVAILABLE INSTRUMENTS" in with_menu.prompts[0] and "plate-reader" in with_menu.prompts[0]
    assert '"instrument"' in with_menu.prompts[0]
    assert "AVAILABLE INSTRUMENTS" not in without.prompts[0] and '"instrument"' not in without.prompts[0]


def test_each_picks_check_rides_in_the_sealed_proposal_record():
    store = SqliteProvenanceStore()
    tr = HypothesisTracker(store, system_actor())
    problem = PROBLEM()
    model = Cap([_v("A", instrument="plate-reader"), _v("B", instrument="quantum-sequencer"), _v("C", "cytotoxicity")])
    hyps, _ = propose_families_with_agent(tr, problem, model, instruments={"menu": MENU, "runs_on": "plate-reader"})
    by = {h.candidate: h.metadata["lab_check"]["status"] for h in hyps}
    assert by == {"A": "ok", "B": "invented", "C": "cannot_run"}
    recs = [r for r in store.branch_records(f"problem/{problem.problem_id}") if r.event_type == EventType.hypothesis_proposed]
    sealed = {r.decision["lab_check"]["status"] for r in recs if (r.decision or {}).get("lab_check")}
    assert sealed == {"ok", "invented", "cannot_run"}, "the validator's verdict on each pick is in the sealed record"


def test_the_plan_shows_where_each_hypothesis_runs_and_what_is_wrong_with_the_pick():
    tr = HypothesisTracker(SqliteProvenanceStore(), system_actor())
    model = Cap([_v("A", instrument="plate-reader"), _v("C", "cytotoxicity")])
    hyps, _ = propose_families_with_agent(tr, PROBLEM(), model, instruments={"menu": MENU, "runs_on": "plate-reader"})
    plan = build_plan(hyps, tr.family_info, tr.family_scores)
    labs = {v["candidate"]: v["lab"] for f in plan["families"] for v in f["variants"]}
    assert labs["A"]["ok"] and labs["A"]["runs_on"] == "plate-reader"
    assert labs["C"]["status"] == "cannot_run" and not labs["C"]["ok"]


# ---- end to end: what the engine does with it ----

def _campaign(tmp_path, variants, lab=None, labs=None, replan=False, plan_approver=None):
    store = SqliteProvenanceStore(str(tmp_path / "prov.db"))
    model = Cap(variants)
    cfg = CampaignConfig(problem=PROBLEM(), hypothesis_source="agent", model=model, autonomy="act" if plan_approver is None else "gate",
                         k=4, rounds=2 if replan else 1, seed=1, inbox=str(tmp_path / "inbox"), poll_secs=0.05,
                         flywheel=Flywheel(":memory:"), labs=labs, replan=replan, plan_approver=plan_approver)
    msgs = []
    report = run_campaign(cfg, lab or SimulatorLab(seed=3), store, approver=lambda t: True, notify=msgs.append)
    return report, store, store.branch_records(f"problem/{report.problem_id}"), msgs, model


def test_a_hypothesis_the_campaign_lab_cannot_serve_is_never_submitted_and_says_why(tmp_path):
    lab = SimulatorLab(seed=3, instruments={"assays": ["binding"], "bsl_max": 2})
    report, store, recs, msgs, _ = _campaign(tmp_path, [_v("OK1"), _v("NOPE", "cytotoxicity")], lab=lab)
    titles = {(r.decision or {}).get("title") for r in recs if r.event_type == EventType.lab_run}
    assert "test-OK1" in titles and "test-NOPE" not in titles, titles
    blocked = [r for r in recs if (r.decision or {}).get("kind") == "lab_cannot_serve"]
    assert [b.decision["candidate"] for b in blocked] == ["NOPE"]
    assert "cytotoxicity" in blocked[0].decision["reasons"][0]
    assert any("NOPE: not submitted" in m for m in msgs)


def test_a_model_that_picks_another_valid_instrument_does_not_move_the_run(tmp_path):
    labs = [{"simulator": {"seed": 3}}, {"simulator": {"seed": 4}}]
    report, store, recs, msgs, _ = _campaign(tmp_path, [_v("A", instrument="simulator-1")], labs=labs)
    sel = next(r for r in recs if (r.decision or {}).get("kind") == "lab_selected")
    assert sel.decision["chosen"]["name"] == "simulator-0"
    h = [r for r in recs if r.event_type == EventType.hypothesis_proposed and (r.decision or {}).get("lab_check")]
    assert h and h[0].decision["lab_check"]["status"] == "other_instrument" and h[0].decision["lab_check"]["runs_on"] == "simulator-0"
    assert any(r.event_type == EventType.lab_run and (r.decision or {}).get("title") == "test-A" for r in recs), "it still runs, on the campaign's lab"


def test_a_replan_is_shown_the_same_menu(tmp_path):
    approver = lambda plan, pid: PlanDecision(approved=True, by="t")  # noqa: E731
    *_, model = _campaign(tmp_path, [_v("A"), _v("B")], replan=True, plan_approver=approver)
    plans = [p for p in model.prompts if p.startswith("You are the hypothesis lead")]   # not the reviewer's prompt
    assert len(plans) >= 2 and all("AVAILABLE INSTRUMENTS" in p for p in plans), "initial plan and re-plan"


def test_replay_does_not_submit_what_the_original_refused_to_(tmp_path):
    """Found while wiring replay: a replay rebuilds hypotheses from the records, and without the recorded lab check it would
    have submitted the hypothesis the campaign's lab could not serve."""
    from proef.reproduce import reproduce
    lab = SimulatorLab(seed=3, instruments={"assays": ["binding"], "bsl_max": 2})
    report, store, recs, _, _ = _campaign(tmp_path, [_v("OK1"), _v("NOPE", "cytotoxicity")], lab=lab)
    assert report.verdicts.get("NOPE") == "proposed", "selected, but never submitted, so never judged"
    res = reproduce(f"problem/{report.problem_id}", db_path=str(tmp_path / "prov.db"))
    assert res["status"] == "match", res          # and a replay that ran NOPE would show as a difference (reproduce.py)


def test_a_replay_that_judges_something_the_original_never_did_is_not_a_match(tmp_path, monkeypatch):
    """reproduce() compared only the recorded verdicts, so extra hypotheses in a replay were invisible."""
    import proef.autoloop as al
    from proef.reproduce import reproduce
    report, store, recs, _, _ = _campaign(tmp_path, [_v("OK1")])
    real = al.run_campaign

    def run_with_an_extra(cfg, lab, st, **kw):
        rep = real(cfg, lab, st, **kw)
        rep.verdicts["EXTRA"] = "validated"        # the replay judged something the original never did
        return rep
    monkeypatch.setattr(al, "run_campaign", run_with_an_extra)
    res = reproduce(f"problem/{report.problem_id}", db_path=str(tmp_path / "prov.db"))
    assert res["status"] == "differs" and {"candidate": "EXTRA", "recorded": None, "replayed": "validated"} in res["diffs"]
