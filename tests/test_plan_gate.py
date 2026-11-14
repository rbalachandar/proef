"""A model-proposed plan is shown to a person BEFORE anything is submitted to the lab: approved, declined,
or trimmed hypothesis by hypothesis. Every outcome is on the record, and removed hypotheses are never tested."""
import json
import threading
import time

from proef.adapters.lab.simulator import SimulatorLab
from proef.adapters.store.sqlite import SqliteProvenanceStore
from proef.approvals import ApprovalInbox
from proef.autoloop import CampaignConfig, run_campaign
from proef.core.contracts import ToolResult
from proef.core.hypothesis import Constraint, ProblemStatement
from proef.flywheel import Flywheel
from proef.plan import PlanDecision, web_plan_approver

PLAN_JSON = {"families": [
    {"family": "hinge", "parent_statement": "Reversible hinge binders inhibit the target", "kind": "mechanistic",
     "mechanism": "occupies the ATP site", "refutation_condition": "IC50 > 20 uM in a replicated assay",
     "confidence": 0.8, "variants": [
         {"statement": "H1 binds", "candidate": "H1", "assay_type": "binding", "predicted": "<=20",
          "predicted_value": 20.0, "rationale": "r1"},
         {"statement": "H2 binds", "candidate": "H2", "assay_type": "binding", "predicted": "<=20",
          "predicted_value": 20.0, "rationale": "r2"}]},
    {"family": "covalent", "parent_statement": "A covalent warhead inhibits the target", "kind": "mechanistic",
     "mechanism": "reacts with a cysteine", "refutation_condition": "no time-dependence", "confidence": 0.4,
     "variants": [{"statement": "C1 binds", "candidate": "C1", "assay_type": "binding", "predicted": "<=20",
                   "predicted_value": 20.0, "rationale": "r3"}]}]}


class Scripted:
    name, model_version, model = "scripted", "s-1", "scripted-1"

    def generate(self, inv, prompt, **kw):
        return ToolResult(envelope=inv.envelope.model_copy(), invocation_id=inv.invocation_id, status="succeeded",
                          output={"text": json.dumps(PLAN_JSON)}, model_version=self.model_version)

    def predict(self, inv, kind, payload):
        raise NotImplementedError


def _run(tmp_path, plan_approver):
    store = SqliteProvenanceStore(str(tmp_path / "prov.db"))
    cfg = CampaignConfig(
        problem=ProblemStatement(objective="find IC50<=20", constraints=[
            Constraint(name="potency", description="IC50 <= 20 uM", kind="hard", limit=20.0, unit="uM")]),
        hypothesis_source="agent", model=Scripted(), autonomy="gate", k=3, rounds=1, seed=1,
        inbox=str(tmp_path / "inbox"), poll_secs=0.05, flywheel=Flywheel(":memory:"),
        plan_approver=plan_approver)
    report = run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda title: True, notify=lambda m: None)
    return report, store.branch_records(f"problem/{report.problem_id}")


def _kinds(recs):
    return [(r.decision or {}).get("kind") for r in recs]


def test_the_plan_shown_to_the_person_carries_the_models_reasoning(tmp_path):
    seen = {}
    def approver(plan, pid):
        seen.update(plan=plan, pid=pid)
        return PlanDecision(approved=True, by="kim")
    _run(tmp_path, approver)
    fams = {f["id"]: f for f in seen["plan"]["families"]}
    assert set(fams) == {"hinge", "covalent"} and seen["plan"]["n_hypotheses"] == 3
    assert fams["hinge"]["mechanism"] == "occupies the ATP site"
    assert fams["hinge"]["refuted_if"] == "IC50 > 20 uM in a replicated assay" and fams["hinge"]["confidence"] == 0.8
    assert [v["candidate"] for v in fams["hinge"]["variants"]] == ["H1", "H2"]


def test_removed_hypotheses_are_never_tested_and_the_removal_is_recorded(tmp_path):
    def approver(plan, pid):
        h2 = next(v["hypothesis_id"] for f in plan["families"] for v in f["variants"] if v["candidate"] == "H2")
        return PlanDecision(approved=True, excluded={h2}, by="kim")
    report, recs = _run(tmp_path, approver)
    tested = {(r.decision or {}).get("candidate") or ((r.artifact.metadata or {}).get("candidate") if r.artifact else None)
              for r in recs if r.event_type.value == "hypothesis_tested"}
    assert "H2" not in tested and {"H1", "C1"} <= tested
    d = next(r.decision for r in recs if (r.decision or {}).get("kind") == "plan_decision")
    assert d["approved"] is True and d["decided_by"] == "kim" and d["n_proposed"] == 3 and d["n_kept"] == 2
    removal = next(r.decision for r in recs if (r.decision or {}).get("kind") == "plan_review")
    assert removal["decisions"][0]["selected"] is False and "removed from the plan by kim" in removal["decisions"][0]["reason"]


def test_a_declined_plan_submits_nothing_to_the_lab(tmp_path):
    report, recs = _run(tmp_path, lambda plan, pid: PlanDecision(approved=False, by="kim", note="wrong target"))
    assert report.rounds_run == 0 and report.selected == []
    assert not [r for r in recs if r.event_type.value == "lab_run"]
    d = next(r.decision for r in recs if (r.decision or {}).get("kind") == "plan_decision")
    assert d["approved"] is False and d["reason"] == "the plan was declined" and d["note"] == "wrong target"


def test_removing_every_hypothesis_is_treated_as_a_decline(tmp_path):
    def approver(plan, pid):
        return PlanDecision(approved=True, excluded={v["hypothesis_id"] for f in plan["families"] for v in f["variants"]})
    report, recs = _run(tmp_path, approver)
    assert report.rounds_run == 0
    assert next(r.decision for r in recs if (r.decision or {}).get("kind") == "plan_decision")["reason"] == "every hypothesis was removed"


def test_no_plan_gate_without_a_plan_approver(tmp_path):
    report, recs = _run(tmp_path, None)
    assert "plan_decision" not in _kinds(recs) and report.rounds_run == 1


def test_inbox_round_trip_a_person_decides_from_another_thread(tmp_path):
    box = ApprovalInbox(str(tmp_path / "approvals.jsonl"))
    plan = {"families": [{"id": "f", "variants": [{"hypothesis_id": "hyp-1"}, {"hypothesis_id": "hyp-2"}]}], "n_hypotheses": 2}

    def person():
        for _ in range(100):
            pend = box.pending()
            if pend:
                assert pend[0]["payload"]["n_hypotheses"] == 2 and pend[0]["title"].startswith("[problem/prob-x]")
                box.decide(pend[0]["id"], True, by="kim", data={"exclude": ["hyp-2"], "note": "drop 2"})
                return
            time.sleep(0.05)
    t = threading.Thread(target=person); t.start()
    d = web_plan_approver(box, poll_secs=0.05, timeout_secs=10)(plan, "prob-x")
    t.join()
    assert (d.approved, d.excluded, d.by, d.note) == (True, {"hyp-2"}, "kim", "drop 2")


def test_the_plan_approver_denies_on_timeout(tmp_path):
    box = ApprovalInbox(str(tmp_path / "approvals.jsonl"))
    d = web_plan_approver(box, poll_secs=0.05, timeout_secs=0.2)({"families": [], "n_hypotheses": 0}, "prob-y")
    assert d.approved is False


def test_the_cockpit_reads_a_plan_reviewed_campaign_correctly(tmp_path, monkeypatch):
    """Rounds, eliminated-before-testing and the activity feed must understand the new record kinds."""
    from fastapi.testclient import TestClient

    from proef.api.app import app
    from proef.wizard_store import WizardStore

    def approver(plan, pid):
        h2 = next(v["hypothesis_id"] for f in plan["families"] for v in f["variants"] if v["candidate"] == "H2")
        return PlanDecision(approved=True, excluded={h2}, by="kim")
    report, recs = _run(tmp_path, approver)
    eid = "plan-cockpit-1"
    ws = WizardStore()
    ws.register_agent(eid, "find IC50<=20", log="", autonomy="gate")
    ws.link_problem(eid, report.problem_id)
    monkeypatch.setattr("proef.views.ids.PROVENANCE_DB", str(tmp_path / "prov.db"))
    with TestClient(app) as c:
        d = c.get(f"/api/v1/campaigns/{eid}").json()
        act = [a["text"] for a in c.get(f"/api/v1/campaigns/{eid}/activity").json()]
    assert len(d["rounds"]) == 1 and len(d["hypotheses"]) == 3          # no phantom round; the family claim is not a hypothesis row
    assert {c for r in d["rounds"] for c in r["candidates"]} == {"H1", "C1"}
    gone = [e for e in d["eliminated"] if e["candidate"] == "H2"]
    assert gone and "removed from the plan by kim" in gone[0]["reason"]
    assert any("kim approved the research plan: 2 of 3 hypotheses go ahead (1 removed)" in t for t in act), act
    assert any("Plan review: 1 hypothesis removed before testing" in t for t in act), act
    assert d["plan_review"] == {"approved": True, "by": "kim", "proposed": 3, "kept": 2, "removed": 1,
                                "note": None, "ts": d["plan_review"]["ts"]}
    assert not any(t.startswith("?:") for t in act), "a family verdict must be named, never '?'"


def test_family_claims_are_not_counted_as_hypotheses_awaiting_an_experiment():
    from proef.api.v1.campaigns import testable
    cards = [{"key": "fam", "candidate": None, "head": "Reversible binders work"},
             {"key": "h1", "candidate": "H1"}, {"key": "h2", "candidate": "H2"}]
    assert [c["key"] for c in testable(cards)] == ["h1", "h2"] and testable(None) == []


def test_a_plan_reviewed_campaign_replays_the_plan_the_person_approved(tmp_path):
    """Replay must not resurrect what the person removed; a declined plan has nothing to replay."""
    from proef.reproduce import reproduce

    def approver(plan, pid):
        h2 = next(v["hypothesis_id"] for f in plan["families"] for v in f["variants"] if v["candidate"] == "H2")
        return PlanDecision(approved=True, excluded={h2}, by="kim")
    report, _ = _run(tmp_path, approver)
    res = reproduce(f"problem/{report.problem_id}", db_path=str(tmp_path / "prov.db"))
    assert res["status"] == "match", res
    assert "H2" not in json.dumps(res, default=str)

    declined_dir = tmp_path / "declined"
    declined_dir.mkdir()
    declined, _ = _run(declined_dir, lambda plan, pid: PlanDecision(approved=False))
    out = reproduce(f"problem/{declined.problem_id}", db_path=str(declined_dir / "prov.db"))
    assert out["status"] == "not-reproducible" and "declined" in out["reason"]


def test_replay_runs_the_loop_the_way_it_was_recorded(tmp_path):
    """An approval-gated, two-round campaign is replayed gated and for two rounds, not as a one-round autonomous run."""
    from proef.reproduce import reproduce
    store = SqliteProvenanceStore(str(tmp_path / "prov.db"))
    cfg = CampaignConfig(
        problem=ProblemStatement(objective="find IC50<=20", constraints=[
            Constraint(name="potency", description="IC50 <= 20 uM", kind="hard", limit=20.0, unit="uM")]),
        hypothesis_source="agent", model=Scripted(), autonomy="gate", k=2, rounds=2, seed=1,
        inbox=str(tmp_path / "inbox"), poll_secs=0.05, flywheel=Flywheel(":memory:"),
        plan_approver=lambda plan, pid: PlanDecision(approved=True, by="kim"))
    report = run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    env = next(r.decision for r in store.branch_records(f"problem/{report.problem_id}")
               if r.event_type.value == "problem_defined")["environment"]
    assert env["rounds"] == 2 and env["autonomy"] == "gate"
    res = reproduce(f"problem/{report.problem_id}", db_path=str(tmp_path / "prov.db"))
    assert res["status"] == "match", res


def _gate_cfg(tmp_path, model, plan_approver, **kw):
    return CampaignConfig(
        problem=ProblemStatement(objective="find IC50<=20", constraints=[
            Constraint(name="potency", description="IC50 <= 20 uM", kind="hard", limit=20.0, unit="uM")]),
        hypothesis_source="agent", model=model, autonomy="gate", seed=1,
        inbox=str(tmp_path / "inbox"), poll_secs=0.05, flywheel=Flywheel(":memory:"),
        plan_approver=plan_approver, **{"k": 3, "rounds": 1, **kw})


def test_an_approved_plan_also_approves_its_own_submissions(tmp_path):
    asked = []
    store = SqliteProvenanceStore(str(tmp_path / "prov.db"))
    cfg = _gate_cfg(tmp_path, Scripted(), lambda plan, pid: PlanDecision(approved=True, by="ana"))
    report = run_campaign(cfg, SimulatorLab(seed=3), store,
                          approver=lambda t: asked.append(t) or True, notify=lambda m: None)
    recs = store.branch_records(f"problem/{report.problem_id}")
    assert asked == [], "the plan approval covers the submissions; nobody is asked again"
    assert _kinds(recs).count("covered_by_plan") == len(report.selected) > 0


def test_with_coverage_off_every_submission_still_asks(tmp_path):
    asked = []
    store = SqliteProvenanceStore(str(tmp_path / "prov.db"))
    cfg = _gate_cfg(tmp_path, Scripted(), lambda plan, pid: PlanDecision(approved=True, by="ana"),
                    plan_covers_submissions=False)
    run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: asked.append(t) or True, notify=lambda m: None)
    assert asked


class Replanner(Scripted):
    def generate(self, inv, prompt, **kw):
        if "RESULTS SO FAR" not in prompt:
            return super().generate(inv, prompt, **kw)
        doc = {"families": [{"family": "followup", "parent_statement": "Analogs of what held up",
                             "kind": "mechanistic", "mechanism": "same site", "refutation_condition": "IC50 > 20",
                             "confidence": 0.6, "variants": [
            {"statement": "N1 binds", "candidate": "N1", "assay_type": "binding", "predicted": "<=20",
             "predicted_value": 20.0, "rationale": "r"}]}]}
        return ToolResult(envelope=inv.envelope.model_copy(), invocation_id=inv.invocation_id,
                          status="succeeded", output={"text": json.dumps(doc)}, model_version="s-1")


def test_replanning_proposes_from_results_and_asks_again(tmp_path):
    plans = []
    store = SqliteProvenanceStore(str(tmp_path / "prov.db"))
    cfg = _gate_cfg(tmp_path, Replanner(), lambda plan, pid: plans.append(plan) or PlanDecision(approved=True, by="ana"),
                    k=1, rounds=3, replan=True)
    report = run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    assert len(plans) >= 2 and plans[1].get("label") == "after round 1"
    assert "N1" in report.selected + [c for p in plans[1:] for f in p["families"] for v in f["variants"] for c in [v.get("candidate")]]
