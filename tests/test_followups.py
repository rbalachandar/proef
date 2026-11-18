"""Phase 3B and 3C of docs/LLM-ORCHESTRATION.md: the model SUGGESTS what to do with abstained hypotheses, code validates every
suggestion, and only a valid follow-up can run. Accept and drop are a person's decision and are never executed."""
import json
import re
from types import SimpleNamespace as NS

import pytest

from proef.adapters.lab.simulator import SimulatorLab
from proef.adapters.store.sqlite import SqliteProvenanceStore
from proef.autoloop import CampaignConfig, ResourceBudget, _followups, run_campaign
from proef.core.contracts import EventType, ToolResult
from proef.core.hypothesis import Constraint, ProblemStatement, SuccessCriteria
from proef.core.interfaces import system_actor
from proef.flywheel import Flywheel
from proef.followup import validate_followup
from proef.plan import PlanDecision

GATE = SuccessCriteria(metric="ic50_um", direction="min", threshold=20.0)
GOOD = [0.5, 5, 20, 80]            # brackets the gate at 20


def _h(hid="h1", cand="C1", status="inconclusive"):
    return NS(hypothesis_id=hid, candidate=cand, status=status, observed_value=21.0, observed_ci=(12.0, 33.0), qc_passed=True)


def _ctx(*hs, runs_left=5, followed=(), supports=True, gate=GATE):
    return {"abstained": {h.hypothesis_id: h for h in hs}, "gate": gate, "runs_left": runs_left, "followed_up": set(followed),
            "seen": set(), "supports_replicates": supports}


def _v(raw, ctx):
    return validate_followup(raw, ctx)["validation"]


# ---- the validator, on facts ----

def test_a_valid_follow_up_executes_and_carries_its_parameters():
    p = validate_followup({"hypothesis_id": "h1", "action": "follow_up", "replicates": 6, "concentrations_uM": GOOD, "why": "w"}, _ctx(_h()))
    assert p["validation"]["ok"] and p["executes"] is True
    assert p["params"]["replicates"] == 6 and p["params"]["concentrations"][0] == "0.5uM"


def test_accept_and_drop_are_suggestions_that_never_execute():
    for action in ("accept", "drop"):
        p = validate_followup({"hypothesis_id": "h1", "action": action}, _ctx(_h()))
        assert p["validation"]["ok"] and p["executes"] is False and "a person records" in p["validation"]["reasons"][0]


def test_every_way_a_proposal_can_be_wrong_is_a_status():
    c = lambda **kw: _ctx(_h(), **kw)  # noqa: E731
    base = {"hypothesis_id": "h1", "action": "follow_up", "replicates": 6, "concentrations_uM": GOOD}
    assert _v({**base, "action": "delete_everything"}, c())["status"] == "bad_action"
    assert _v({**base, "hypothesis_id": "nope"}, c())["status"] == "unknown_hypothesis"
    assert _v({**base, "replicates": 99}, c())["status"] == "replicates_out_of_bounds"
    assert _v({**base, "replicates": 1}, c())["status"] == "replicates_out_of_bounds"
    assert _v({**base, "replicates": True}, c())["status"] == "replicates_out_of_bounds"
    assert _v({**base, "replicates": "6"}, c())["status"] == "replicates_out_of_bounds"
    assert _v({**base, "concentrations_uM": [1, 2]}, c())["status"] == "bad_ladder"
    assert _v({**base, "concentrations_uM": [30, 50, 80, 200]}, c())["status"] == "does_not_bracket_gate"
    assert _v(base, c(runs_left=0))["status"] == "no_budget"
    assert _v(base, c(followed=["C1"]))["status"] == "already_followed_up"


def test_two_follow_ups_cannot_claim_the_same_last_run():
    ctx = _ctx(_h("h1", "C1"), _h("h2", "C2"), runs_left=1)
    base = {"action": "follow_up", "replicates": 6, "concentrations_uM": GOOD}
    a = validate_followup({**base, "hypothesis_id": "h1"}, ctx)["validation"]
    b = validate_followup({**base, "hypothesis_id": "h2"}, ctx)["validation"]
    assert a["ok"] and (b["ok"], b["status"]) == (False, "no_budget")


def test_a_hypothesis_gets_one_proposal_and_a_model_cannot_name_one_that_is_not_abstained():
    ctx = _ctx(_h())
    first = validate_followup({"hypothesis_id": "h1", "action": "accept"}, ctx)
    second = validate_followup({"hypothesis_id": "h1", "action": "follow_up", "replicates": 6, "concentrations_uM": GOOD}, ctx)
    assert first["validation"]["ok"] and second["validation"]["status"] == "duplicate_proposal"
    assert _v({"hypothesis_id": "validated-one", "action": "accept"}, _ctx(_h()))["status"] == "unknown_hypothesis"


def test_a_lab_that_cannot_take_a_replicate_request_still_follows_up_with_the_ladder_and_says_so():
    p = validate_followup({"hypothesis_id": "h1", "action": "follow_up", "replicates": 8, "concentrations_uM": GOOD}, _ctx(_h(), supports=False))
    assert p["validation"]["ok"] and p["params"]["replicates"] is None and p["params"]["replicates_requested"] == 8
    assert "does not take a replicate request" in p["validation"]["reasons"][0]


def test_a_property_gate_needs_no_ladder():
    prop = SuccessCriteria(metric="conductivity", direction="max", threshold=1.0)
    p = validate_followup({"hypothesis_id": "h1", "action": "follow_up", "replicates": 6}, _ctx(_h(), gate=prop))
    assert p["validation"]["ok"] and p["params"]["concentrations"] is None


def test_the_models_free_text_is_bounded():
    p = validate_followup({"hypothesis_id": "h" * 500, "action": "x" * 500, "why": "w" * 5000}, _ctx(_h()))
    assert len(p["hypothesis_id"]) <= 80 and len(p["action"]) <= 20 and len(p["rationale"]) <= 300


# ---- the engine ----

PLAN = lambda names: {"families": [{"family": f"f{n}", "parent_statement": f"c{n}", "kind": "mechanistic", "mechanism": "m",  # noqa: E731
                                    "refutation_condition": "r", "confidence": 0.7, "variants": [{
                                        "statement": f"{n} binds", "candidate": n, "assay_type": "binding", "predicted": "p",
                                        "predicted_value": 20.0, "rationale": "x"}]} for n in names]}


class Model:
    """Proposes C1 to C8 (C1, C2, C5 and C8 abstain under seed 3 in this order), then answers the follow-up question from the ids in its prompt."""
    name, model_version, model = "scripted", "s-1", "scripted-1"

    def __init__(self, answer):
        self.answer, self.prompts = answer, []

    def generate(self, inv, prompt, **kw):
        self.prompts.append(prompt)
        if "ABSTAINED HYPOTHESES" in prompt:
            ids = {c: i for i, c in re.findall(r"- (hyp-\w+) \((\w+)\)", prompt)}
            text = json.dumps({"proposals": self.answer(ids)})
        elif "hypothesis lead" in prompt:
            text = json.dumps(PLAN([f"C{i}" for i in range(1, 9)]))
        else:
            text = json.dumps({"notes": []})
        return ToolResult(envelope=inv.envelope.model_copy(), invocation_id=inv.invocation_id, status="succeeded",
                          output={"text": text}, model_version=self.model_version)

    def predict(self, inv, kind, payload):
        raise NotImplementedError


def _answer(ids):
    return [{"hypothesis_id": ids["C1"], "action": "follow_up", "replicates": 6, "concentrations_uM": GOOD, "why": "bracket the gate"},
            {"hypothesis_id": ids["C2"], "action": "accept", "why": "close enough"},
            {"hypothesis_id": ids["C5"], "action": "follow_up", "replicates": 99, "concentrations_uM": GOOD},
            {"hypothesis_id": "hyp-invented", "action": "drop"}]


def _run(tmp_path, answer=_answer, autonomy="act", rounds=2, budget=None, plan_approver=None, lab=None, followups=True):
    store = SqliteProvenanceStore(str(tmp_path / "prov.db"))
    model = Model(answer)
    cfg = CampaignConfig(problem=ProblemStatement(objective="o", constraints=[Constraint(name="potency", description="IC50 <= 20 uM", kind="hard", limit=20.0, unit="uM")]),
                         hypothesis_source="agent", model=model, autonomy=autonomy, k=8, max_families=8, rounds=rounds, seed=1, followups=followups,
                         inbox=str(tmp_path / "inbox"), poll_secs=0.05, flywheel=Flywheel(":memory:"), budget=ResourceBudget(max_total_runs=50, max_hypotheses_total=30, max_model_calls=budget) if isinstance(budget, int) else (budget or ResourceBudget(max_hypotheses_total=30)), plan_approver=plan_approver)
    lab = lab or SimulatorLab(seed=3)
    report = run_campaign(cfg, lab, store, approver=lambda t: True, notify=lambda m: None)
    return report, store, store.branch_records(f"problem/{report.problem_id}"), model, lab


def _kind(recs, kind):
    return [r for r in recs if (r.decision or {}).get("kind") == kind]


def test_every_suggestion_is_validated_and_recorded_whatever_the_model_said(tmp_path):
    _, _, recs, _, _ = _run(tmp_path)
    by = {r.decision["candidate"] or r.decision["hypothesis_id"]: r.decision["validation"]["status"] for r in _kind(recs, "followup_proposal")}
    assert by == {"C1": "ok", "C2": "ok", "C5": "replicates_out_of_bounds", "hyp-invented": "unknown_hypothesis"}


def test_only_a_valid_follow_up_runs_and_it_runs_inside_the_next_round_with_its_own_design(tmp_path):
    report, _, recs, _, lab = _run(tmp_path)
    fups = [r for r in recs if r.event_type == EventType.hypothesis_proposed and (r.decision or {}).get("followup_of")]
    assert len(fups) == 1 and fups[0].decision["replicates"] == 6, "only C1's valid follow-up became a hypothesis"
    assert fups[0].decision["criteria"]["threshold"] == 20.0, "judged by the program's gate, as every hypothesis"
    ran = [p for p in lab._runs.values() if p.title == "test-C1"]
    assert len(ran) == 2, "C1 was tested in round 1 and again in round 2"
    assert [p.constraints.get("replicates") for p in ran] == [None, 6], "the follow-up asked for six replicates and got them"
    doses = [s.concentration for s in ran[1].samples if s.sample_id == "C1"]
    assert doses == ["0.5uM", "5uM", "20uM", "80uM"], "its own designed ladder, bracketing the gate"
    assert not [p for p in lab._runs.values() if p.title in ("test-C2", "test-C5") and p.constraints.get("replicates")]


def test_the_engine_never_records_a_decision_a_person_owns(tmp_path):
    _, _, recs, _, _ = _run(tmp_path)
    assert not _kind(recs, "human_review"), "accept and drop were suggested, never executed"


def test_the_suggestion_is_in_the_record_a_person_reads_and_says_it_ran(tmp_path):
    from proef.api.v1.campaigns import _suggestions
    _, _, recs, _, _ = _run(tmp_path)
    sug = _suggestions(recs)
    by = {s.action + ":" + s.status: s for s in sug.values()}
    assert by["follow_up:ok"].ran is True and by["follow_up:ok"].replicates == 6
    assert by["accept:ok"].ran is False and by["follow_up:replicates_out_of_bounds"].ok is False


def test_the_last_round_has_nowhere_to_run_so_it_only_suggests(tmp_path):
    _, _, recs, _, lab = _run(tmp_path, rounds=1)
    assert _kind(recs, "followup_proposal") and not [r for r in recs if (r.decision or {}).get("followup_of")]
    assert len([p for p in lab._runs.values() if p.title == "test-C1"]) == 1


def test_under_approvals_a_follow_up_goes_through_plan_review_and_a_decline_stops_it(tmp_path):
    seen = []

    def approver(plan, pid):
        seen.append(plan)
        return PlanDecision(approved=len(seen) == 1, by="kim")           # approve the first plan, decline the follow-up plan
    _, _, recs, _, lab = _run(tmp_path, autonomy="gate", plan_approver=approver)
    assert len(seen) == 2 and seen[1]["label"] == "follow-ups after round 1"
    assert any(w["kind"] == "already_tested" and w["candidate"] == "C1" for w in seen[1]["warnings"]), "the person is told it was tested"
    assert len([p for p in lab._runs.values() if p.title == "test-C1"]) == 1, "declined: it never ran"


def test_a_refused_model_call_leaves_the_abstentions_to_a_person_and_the_campaign_finishes(tmp_path):
    """Budget of 1: the plan is proposed, the follow-up question is refused and recorded, nothing else changes."""
    report, _, recs, _, _ = _run(tmp_path, budget=ResourceBudget(max_model_calls=1))
    assert _kind(recs, "budget_refusal") and not _kind(recs, "followup_proposal") and report.verdicts


def test_off_by_default(tmp_path):
    _, _, recs, model, _ = _run(tmp_path, followups=False)
    assert not _kind(recs, "followup_proposal") and not any("ABSTAINED HYPOTHESES" in p for p in model.prompts)


def test_observe_records_suggestions_and_runs_nothing(tmp_path):
    store = SqliteProvenanceStore()
    h = _h()
    h.branch_id, h.problem_id, h.family = "problem/p", "p", "f"
    cfg = NS(model=Model(lambda ids: [{"hypothesis_id": "h1", "action": "follow_up", "replicates": 6, "concentrations_uM": GOOD}]),
             problem=NS(problem_id="p", constraints=[Constraint(name="potency", description="IC50 <= 20 uM", kind="hard", limit=20.0, unit="uM")]),
             autonomy="observe", plan_approver=None, budget=None)
    cfg.model.generate = lambda inv, prompt, **kw: ToolResult(
        envelope=inv.envelope.model_copy(), invocation_id=inv.invocation_id, status="succeeded",
        output={"text": json.dumps({"proposals": [{"hypothesis_id": "h1", "action": "follow_up", "replicates": 6, "concentrations_uM": GOOD}]})})
    from proef.core.hypothesis import HypothesisTracker
    new = _followups(cfg, HypothesisTracker(store, system_actor()), [h], store, system_actor(), lambda m: None, SimulatorLab(seed=1), GATE, 1, set(), 5, False, [h])
    assert new == [] and [r for r in store.branch_records("problem/p") if r.decision.get("kind") == "followup_proposal"]


def test_a_replay_of_a_campaign_that_ran_follow_ups_reproduces_it_from_the_sealed_answers(tmp_path):
    """Phase 4A: this used to say "not reproducible", because the model's follow-up answers were not in the ledger."""
    from proef.reproduce import reproduce
    report, _, recs, _, _ = _run(tmp_path)
    assert [r for r in recs if (r.decision or {}).get("followup_of")]
    res = reproduce(f"problem/{report.problem_id}", db_path=str(tmp_path / "prov.db"))
    assert res["status"] == "match" and res["model_pinned"] is True, res


def test_the_flag_reaches_the_engine_from_the_workbench_and_the_cli():
    from proef.api.launch import build_cfg
    from proef.cli import _campaign_from_doc
    doc = {"objective": "o", "gate_value": 1.0, "rounds": 2, "autonomy": "gate", "followups": True}
    assert _campaign_from_doc(build_cfg(doc)).followups is True
    assert _campaign_from_doc(build_cfg({k: v for k, v in doc.items() if k != "followups"})).followups is False
    assert "followups" not in build_cfg({**doc, "candidates": "A,B"}), "a candidate list has no model to ask"


# ---- found by the independent review of Phase 3 ----

def test_a_later_duplicate_cannot_replace_a_valid_suggestion_and_an_unknown_action_is_not_shown_as_accept(tmp_path):
    from proef.api.v1.campaigns import _suggestions

    def answer(ids):
        return [{"hypothesis_id": ids["C1"], "action": "follow_up", "replicates": 6, "concentrations_uM": GOOD},
                {"hypothesis_id": ids["C1"], "action": "accept"},                      # a duplicate, recorded after the valid one
                {"hypothesis_id": ids["C2"], "action": "explode"}]                     # not one of the three actions
    _, _, recs, _, _ = _run(tmp_path, answer=answer)
    sug = _suggestions(recs)
    c1 = next(r.decision["hypothesis_id"] for r in recs if (r.decision or {}).get("kind") == "followup_proposal" and r.decision["candidate"] == "C1")
    assert sug[c1].action == "follow_up" and sug[c1].ok and sug[c1].ran, "the valid suggestion stands"
    assert all(s.action != "accept" or s.status == "ok" for s in sug.values())
    assert len(sug) == 1, "the duplicate and the unknown action are not shown"


def test_a_follow_up_that_would_repeat_the_same_test_is_refused():
    prop = SuccessCriteria(metric="conductivity", direction="max", threshold=1.0)
    base = {"hypothesis_id": "h1", "action": "follow_up", "replicates": 6}
    p = validate_followup(base, _ctx(_h(), gate=prop, supports=False))["validation"]
    assert (p["ok"], p["status"]) == (False, "no_change") and "same test" in p["reasons"][0]
    assert _v({**base, "concentrations_uM": GOOD}, _ctx(_h(), supports=False))["ok"], "a ladder is a real change"
    assert _v(base, _ctx(_h(), gate=prop, supports=True))["ok"], "replicates are a real change when the lab honors them"


def test_a_replicate_value_the_lab_ignores_does_not_sink_the_follow_up():
    base = {"hypothesis_id": "h1", "action": "follow_up", "concentrations_uM": GOOD}
    for bad in (None, 6.0, 20, "six", True):
        assert _v({**base, "replicates": bad}, _ctx(_h(), supports=False))["ok"], bad
    assert _v({**base, "replicates": 6.0}, _ctx(_h(), supports=True))["ok"], "6.0 is six"
    assert _v({**base}, _ctx(_h(), supports=True))["ok"], "no value means the default"
    assert _v({**base, "replicates": 20}, _ctx(_h(), supports=True))["status"] == "replicates_out_of_bounds", "still strict when it will be used"


def test_the_hypothesis_limit_binds_follow_ups_and_two_cannot_share_the_last_slot():
    ctx = _ctx(_h("h1", "C1"), _h("h2", "C2"))
    ctx["hyp_room"] = 1
    base = {"action": "follow_up", "replicates": 6, "concentrations_uM": GOOD}
    assert validate_followup({**base, "hypothesis_id": "h1"}, ctx)["validation"]["ok"]
    assert validate_followup({**base, "hypothesis_id": "h2"}, ctx)["validation"]["status"] == "no_hypothesis_budget"


def test_a_campaign_at_its_hypothesis_limit_adds_no_follow_up(tmp_path):
    _, _, recs, _, lab = _run(tmp_path, budget=ResourceBudget(max_total_runs=50, max_hypotheses_total=8))
    by = {r.decision["candidate"]: r.decision["validation"]["status"] for r in _kind(recs, "followup_proposal") if r.decision.get("candidate")}
    assert by["C1"] == "no_hypothesis_budget"
    assert not [r for r in recs if (r.decision or {}).get("followup_of")]


def test_what_a_follow_up_says_it_changes_is_what_the_lab_will_actually_do(tmp_path):
    def no_replicates(seed):
        lab = SimulatorLab(seed=seed)
        lab.supports_replicates = False      # an instance flag: the constructor sets it, so a class attribute would be overridden
        return lab
    stmt = lambda recs: next(r.decision["statement"] for r in recs if r.event_type == EventType.hypothesis_proposed and r.decision.get("followup_of"))  # noqa: E731
    (tmp_path / "a").mkdir()
    _, _, honored, _, _ = _run(tmp_path / "a")
    assert "6 replicates and a designed dose ladder" in stmt(honored)
    (tmp_path / "b").mkdir()
    _, _, ignored, _, lab = _run(tmp_path / "b", lab=no_replicates(3))
    s = stmt(ignored)
    assert "replicates" not in s and "a designed dose ladder" in s
    assert all(not p.constraints.get("replicates") for p in lab._runs.values()), "and no replicate request was sent"


def test_a_follow_up_keeps_its_parents_prior_and_lab_check(tmp_path):
    from proef.hypothesis_agent import propose_families_with_agent  # noqa: F401  (the plan below carries a tool request)
    plan = PLAN([f"C{i}" for i in range(1, 9)])
    plan["families"][0]["variants"][0].update({"tools": ["admet-physchem"], "smiles": "CC(=O)Oc1ccccc1C(=O)O"})

    class WithTools(Model):
        def generate(self, inv, prompt, **kw):
            if "hypothesis lead" in prompt:
                self.prompts.append(prompt)
                return ToolResult(envelope=inv.envelope.model_copy(), invocation_id=inv.invocation_id, status="succeeded",
                                  output={"text": json.dumps(plan)}, model_version="s-1")
            return super().generate(inv, prompt, **kw)
    store = SqliteProvenanceStore(str(tmp_path / "prov.db"))
    cfg = CampaignConfig(problem=ProblemStatement(objective="o", constraints=[Constraint(name="potency", description="IC50 <= 20 uM", kind="hard", limit=20.0, unit="uM")]),
                         hypothesis_source="agent", model=WithTools(_answer), autonomy="act", k=8, max_families=8, rounds=2, seed=1, followups=True,
                         prediction_tools=["admet-physchem"], inbox=str(tmp_path / "inbox"), poll_secs=0.05, flywheel=Flywheel(":memory:"))
    report = run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    recs = store.branch_records(f"problem/{report.problem_id}")
    fu = next(r for r in recs if r.event_type == EventType.hypothesis_proposed and (r.decision or {}).get("followup_of"))
    parent = next(r for r in recs if r.event_type == EventType.hypothesis_proposed and r.artifact
                  and r.artifact.artifact_id == fu.decision["followup_of"])
    assert fu.decision["predictions"] == parent.decision["predictions"] and fu.decision["predictions"], "the prior travels with the candidate"
    assert fu.decision["lab_check"] == parent.decision["lab_check"]


def test_a_declined_follow_up_does_not_spend_the_candidates_one_follow_up(monkeypatch):
    import proef.autoloop as al
    from proef.core.hypothesis import HypothesisTracker
    store = SqliteProvenanceStore()
    tracker = HypothesisTracker(store, system_actor())
    h = tracker.propose("C1 binds", "C1", "binding", "p", criteria=GATE, branch_id="problem/p")
    h.status, h.observed_value, h.observed_ci, h.qc_passed, h.problem_id = "inconclusive", 21.0, (12.0, 33.0), True, "p"
    answers = iter([([], True), (None, False)])             # first the person declines, then (a later round) approves
    monkeypatch.setattr(al, "_plan_gate", lambda cfg, tr, new, *a, **k: (lambda r: (new, False) if r[0] is None else r)(next(answers)))
    model = Model(lambda ids: [{"hypothesis_id": h.hypothesis_id, "action": "follow_up", "replicates": 6, "concentrations_uM": GOOD}])
    model.generate = lambda inv, prompt, **kw: ToolResult(
        envelope=inv.envelope.model_copy(), invocation_id=inv.invocation_id, status="succeeded",
        output={"text": json.dumps({"proposals": [{"hypothesis_id": h.hypothesis_id, "action": "follow_up", "replicates": 6, "concentrations_uM": GOOD}]})})
    cfg = NS(model=model, problem=NS(problem_id="p", constraints=[Constraint(name="potency", description="IC50 <= 20 uM", kind="hard", limit=20.0, unit="uM")]),
             autonomy="gate", plan_approver=lambda *a: None, budget=None)
    followed = set()
    first = al._followups(cfg, tracker, [h], store, system_actor(), lambda m: None, SimulatorLab(seed=1), GATE, 1, followed, 5, False, [h])
    assert first == [] and followed == set(), "declined: the candidate keeps its one follow-up"
    second = al._followups(cfg, tracker, [h], store, system_actor(), lambda m: None, SimulatorLab(seed=1), GATE, 2, followed, 5, False, [h])
    assert len(second) == 1 and followed == {"C1"}
