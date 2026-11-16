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


def test_a_model_designed_dose_ladder_is_validated_and_bounded():
    from proef.protocol_design import describe, sanitize_protocol
    ok = sanitize_protocol({"concentrations_uM": [50, 0.1, 2, 10, 0.5, 2], "why": "brackets the gate"})
    assert ok["concentrations"] == ["0.1uM", "0.5uM", "2uM", "10uM", "50uM"], "sorted, de-duplicated"
    assert "5 doses, 0.1 to 50 µM" in describe(ok)
    assert sanitize_protocol({"concentrations_uM": [1, 2]}) is None, "fewer than 3 doses: unusable, fall back to the default"
    assert sanitize_protocol({"concentrations_uM": [1e9, -3, "x", 0, 5]}) is None, "out of range or junk is dropped"
    assert len(sanitize_protocol({"concentrations_uM": list(range(1, 40))})["concentrations"]) == 8, "capped at 8 doses"
    assert sanitize_protocol("a string") is None and sanitize_protocol(None) is None


def test_the_designed_ladder_reaches_the_lab_protocol_and_the_plan(tmp_path):
    class Designer(Scripted):
        def generate(self, inv, prompt, **kw):
            fams = json.loads(json.dumps(PLAN_JSON))
            for v in fams["families"][0]["variants"]:
                v["protocol"] = {"concentrations_uM": [0.2, 1, 5, 20, 80], "why": "brackets 20 uM"}
            return ToolResult(envelope=inv.envelope.model_copy(), invocation_id=inv.invocation_id, status="succeeded",
                              output={"text": json.dumps(fams)}, model_version="s-1")
    plans = []
    store = SqliteProvenanceStore(str(tmp_path / "prov.db"))
    cfg = _gate_cfg(tmp_path, Designer(), lambda plan, pid: plans.append(plan) or PlanDecision(approved=True, by="ana"))
    cfg.concentrations = ("1uM", "10uM", "100uM")
    report = run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    v = {x["candidate"]: x for f in plans[0]["families"] for x in f["variants"]}
    assert "5 doses" in v["H1"]["protocol"] and v["C1"]["protocol"] == "", "only the variant that designed one shows it"
    runs = {(r.decision or {})["title"]: (r.decision or {})["concentrations"]
            for r in store.branch_records(f"problem/{report.problem_id}") if r.event_type.value == "lab_run"}
    assert runs["test-H1"] == [0.2, 1.0, 5.0, 20.0, 80.0], "the lab received the model's validated ladder"
    assert runs["test-C1"] == [1.0, 10.0, 100.0], "a variant with no design gets the campaign default"


def test_a_critic_model_reviews_the_plan_before_the_person_sees_it_and_decides_nothing(tmp_path):
    class Critic(Scripted):
        name, model_version = "critic-model", "c-1"
        def generate(self, inv, prompt, **kw):
            assert "You are the critic" in prompt and "H1 binds" in prompt, "the critic is shown the proposed plan"
            doc = {"notes": [{"target": "H2", "severity": "concern", "text": "H2 duplicates H1; spend the run elsewhere."},
                             {"target": "plan", "severity": "weird", "text": "  Only one assay type is used.  "},
                             {"target": "x", "text": "   "}]}
            return ToolResult(envelope=inv.envelope.model_copy(), invocation_id=inv.invocation_id, status="succeeded",
                              output={"text": "sure: " + json.dumps(doc)}, model_version="c-1")
    seen = {}
    store = SqliteProvenanceStore(str(tmp_path / "prov.db"))
    cfg = _gate_cfg(tmp_path, Scripted(), lambda plan, pid: seen.update(plan=plan) or PlanDecision(approved=True, by="ana"))
    cfg.critic_model = Critic()
    report = run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    crit = seen["plan"]["critique"]
    assert crit["model"] == "critic-model/c-1"
    assert [(n["target"], n["severity"]) for n in crit["notes"]] == [("H2", "concern"), ("plan", "note")], "blank notes dropped, bad severity normalised"
    recs = store.branch_records(f"problem/{report.problem_id}")
    assert [r.decision for r in recs if (r.decision or {}).get("kind") == "plan_critique"][0]["advisory"] is True
    pd = [r.decision for r in recs if (r.decision or {}).get("kind") == "plan_decision"][0]
    assert (pd["n_proposed"], pd["n_kept"], pd["removed"]) == (3, 3, []), "the critic's concern removed nothing: only the person can"


def test_a_critic_that_fails_never_blocks_the_plan(tmp_path):
    class Broken(Scripted):
        def generate(self, inv, prompt, **kw):
            raise RuntimeError("gateway down")
    seen = {}
    store = SqliteProvenanceStore(str(tmp_path / "prov.db"))
    cfg = _gate_cfg(tmp_path, Scripted(), lambda plan, pid: seen.update(plan=plan) or PlanDecision(approved=True, by="ana"))
    cfg.critic_model = Broken()
    run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    assert seen["plan"]["critique"]["notes"] == []


def test_the_critic_can_run_on_its_own_model(monkeypatch):
    from proef.adapters.models.from_env import model_status
    monkeypatch.setenv("PROEF_LLM_MODEL", "proposer-1"); monkeypatch.setenv("PROEF_LLM_TOKEN", "k"); monkeypatch.setenv("PROEF_LLM_BASE_URL", "http://localhost:1/v1")
    monkeypatch.setenv("PROEF_LLM_API", "openai"); monkeypatch.delenv("PROEF_LLM_CRITIC_MODEL", raising=False)
    assert model_status("critic")["model"] == "proposer-1", "falls back to the main model"
    monkeypatch.setenv("PROEF_LLM_CRITIC_MODEL", "critic-9")
    assert model_status("critic")["model"] == "critic-9" and model_status()["model"] == "proposer-1"


def test_the_activity_feed_carries_the_ledger_event_type_and_a_hash_prefix(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from proef.api.app import app
    from proef.wizard_store import WizardStore
    report, recs = _run(tmp_path, lambda plan, pid: PlanDecision(approved=True, by="kim"))
    eid = "plan-hash-1"
    ws = WizardStore()
    ws.register_agent(eid, "find IC50<=20", log="", autonomy="gate")
    ws.link_problem(eid, report.problem_id)
    monkeypatch.setattr("proef.views.ids.PROVENANCE_DB", str(tmp_path / "prov.db"))
    with TestClient(app) as c:
        items = c.get(f"/api/v1/campaigns/{eid}/activity?limit=200").json()
    assert items and all(i["event"] for i in items) and all(len(i["hash"]) == 7 for i in items)
    chain = {r.record_hash[:7] for r in recs}
    assert {i["hash"] for i in items} <= chain, "every hash shown is a real record of this campaign's chain"
    assert "gate_decision" in {i["event"] for i in items}


def test_a_model_cannot_move_the_gate_by_predicting_a_number(tmp_path):
    """Found while recording the demo: a variant's predicted value became the criterion recorded on it when proposed, so a
    model that predicted 1 uM put a gate of 1 uM on the record (and on the cockpit) instead of the program's 20 uM."""
    class Bold(Scripted):
        def generate(self, inv, prompt, **kw):
            doc = json.loads(json.dumps(PLAN_JSON))
            for f in doc["families"]:
                for v in f["variants"]:
                    v["predicted_value"] = 1.0                      # the model is optimistic
            return ToolResult(envelope=inv.envelope.model_copy(), invocation_id=inv.invocation_id, status="succeeded",
                              output={"text": json.dumps(doc)}, model_version="s-1")
    store = SqliteProvenanceStore(str(tmp_path / "prov.db"))
    cfg = _gate_cfg(tmp_path, Bold(), lambda plan, pid: PlanDecision(approved=True, by="ana"))
    report = run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    proposed = [(r.decision or {}).get("criteria") for r in store.branch_records(f"problem/{report.problem_id}")
                if r.event_type.value == "hypothesis_proposed" and (r.decision or {}).get("criteria")]
    assert len(proposed) == 3
    assert {c["threshold"] for c in proposed} == {20.0}, f"recorded criteria: {[c['threshold'] for c in proposed]}"


def test_a_label_the_model_echoes_into_a_candidate_name_is_stripped():
    from proef.hypothesis_agent import clean_candidate
    assert [clean_candidate(x) for x in ("proposed: erlotinib", "proposed_osimertinib", "Candidate - H1", "EG-01", None)] \
        == ["erlotinib", "osimertinib", "H1", "EG-01", ""]


def test_each_round_explains_its_own_selection_even_after_a_plan_removal(tmp_path, monkeypatch):
    """Found in the recorded demo: a plan removal writes its own 'filtered' record, and the cockpit matched the n-th such
    record to round n, so every round showed the PREVIOUS round's reasoning."""
    from fastapi.testclient import TestClient

    from proef.api.app import app
    from proef.wizard_store import WizardStore

    def approver(plan, pid):
        h2 = next(v["hypothesis_id"] for f in plan["families"] for v in f["variants"] if v["candidate"] == "H2")
        return PlanDecision(approved=True, excluded={h2}, by="kim")
    store = SqliteProvenanceStore(str(tmp_path / "prov.db"))
    cfg = _gate_cfg(tmp_path, Scripted(), approver, k=1, rounds=2)
    report = run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    eid = "why-rounds-1"
    WizardStore().register_agent(eid, "find IC50<=20", log="", autonomy="gate")
    WizardStore().link_problem(eid, report.problem_id)
    monkeypatch.setattr("proef.views.ids.PROVENANCE_DB", str(tmp_path / "prov.db"))
    with TestClient(app) as c:
        rounds = c.get(f"/api/v1/campaigns/{eid}").json()["rounds"]
    assert len(rounds) == 2
    for r in rounds:
        assert {p["candidate"] for p in r["why"]["picks"]} == set(r["candidates"]), \
            f"round {r['n']} tested {r['candidates']} but its explanation says it picked {[p['candidate'] for p in r['why']['picks']]}"


def test_the_plan_warns_when_one_compound_is_proposed_under_two_families():
    from types import SimpleNamespace as NS
    from proef.plan import build_plan, duplicate_compounds
    def h(i, cand, fam):
        return NS(hypothesis_id=i, candidate=cand, family=fam, mechanism="m", refutation_condition="r", citation="", citation_status="",
                  statement="s", predicted="p", rationale="r", assay_type="binding", metadata={})
    port = [h("a", "erlotinib", "atp_site"), h("b", "Erlotinib ", "covalent"), h("c", "gefitinib", "atp_site"), h("d", "afatinib", "covalent")]
    warn = duplicate_compounds(port)
    assert [(w["candidate"], w["families"], w["hypothesis_ids"]) for w in warn] == [("erlotinib", ["atp_site", "covalent"], ["a", "b"])], \
        "case and spacing differences are the same compound"
    assert build_plan(port, {}, {})["warnings"] == warn
    assert duplicate_compounds(port[2:]) == [], "no warning when every compound appears once"


def test_the_replan_setting_survives_the_launcher_into_the_campaign_config():
    """Found in a live run: build_cfg kept a fixed set of keys and dropped `replan`, so the workbench's checkbox did nothing."""
    from proef.api.launch import build_cfg
    from proef.cli import _campaign_from_doc
    doc = {"objective": "find IC50<=20", "gate_value": 20, "rounds": 2, "autonomy": "gate"}
    assert build_cfg({**doc, "replan": True})["replan"] is True
    assert "replan" not in build_cfg(doc)
    assert "replan" not in build_cfg({**doc, "replan": True, "candidates": "A,B"}), "a candidate list has no model to re-plan"
    assert _campaign_from_doc(build_cfg({**doc, "replan": True})).replan is True
    assert _campaign_from_doc(build_cfg(doc)).replan is False


def test_a_replan_review_says_which_round_it_follows_and_the_grammar_is_right(tmp_path):
    """Found in the live re-plan run: the second review looked identical to the first and read '1 hypotheses in 1 family'."""
    from proef.approvals import ApprovalInbox
    box = ApprovalInbox(str(tmp_path / "approvals.json"))
    approve = web_plan_approver(box, poll_secs=0.01, timeout_secs=0.05)
    plan = {"n_hypotheses": 1, "families": [{"id": "f", "variants": []}], "label": "after round 1"}
    approve(plan, "prob-x")          # times out and denies: only the request matters here
    titles = [v["title"] for v in box.state().values()]
    assert titles == ["[problem/prob-x] Approve the research plan (after round 1): 1 hypothesis in 1 family"], titles
    approve({"n_hypotheses": 2, "families": [{"id": "f"}], "label": None}, "prob-y")
    assert any(t == "[problem/prob-y] Approve the research plan: 2 hypotheses in 1 family" for t in
               (v["title"] for v in box.state().values()))


def test_a_replan_that_proposes_an_already_tested_compound_is_flagged_with_its_verdict():
    """Found in the live re-plan run: round 2 proposed gefitinib again under a new family, and nothing said it already had a verdict."""
    from types import SimpleNamespace as NS
    from proef.plan import build_plan
    new = [NS(hypothesis_id="n1", candidate="gefitinib", family="competition", statement="s", predicted="", rationale="",
              assay_type="binding", metadata={}, mechanism="", refutation_condition="", citation="", citation_status="")]
    tested = [NS(candidate="gefitinib", family="atp_site", status="invalidated")]
    w = build_plan(new, {}, {}, tested=tested)["warnings"]
    assert w == [{"kind": "already_tested", "candidate": "gefitinib", "families": ["competition"], "hypothesis_ids": ["n1"],
                  "verdict": "invalidated", "prior_family": "atp_site"}]
    assert build_plan(new, {}, {})["warnings"] == [], "the first plan has no earlier rounds to compare with"
