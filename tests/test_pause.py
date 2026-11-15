"""A scoped pause HOLDS a campaign's lab submissions and releases them on resume. The global halt still wins.
Both are recorded. A pause on one program or campaign never touches another."""
import threading
import time

import pytest

from proef.core.contracts import Envelope, ProtocolOp, ProtocolStep, ProtocolSubmission, ReagentSpec, SampleKind, SampleSpec
from proef.core.interfaces import system_actor
from proef.core.safety import LabGateway, SafetyEngine
from proef.governance import AuthorizationMatrix, HaltSwitch, PauseRegistry


class MemStore:
    def __init__(self):
        self.recs = []

    def append(self, r):
        self.recs.append(r)
        return r


class MemLab:
    def __init__(self):
        self.n = 0

    def submit_protocol(self, p):
        self.n += 1
        return f"run-{self.n}"


def proto(pid: str):
    return ProtocolSubmission(
        envelope=Envelope(experiment_branch_id=f"problem/{pid}"), title="routine binding screen", assay_type="binding",
        steps=[ProtocolStep(seq=1, op=ProtocolOp.dispense)], samples=[SampleSpec(sample_id="G1", kind=SampleKind.compound)],
        reagents=[ReagentSpec(name="buffer", amount="1 mL", bsl_level=1)])


@pytest.fixture()
def rig(tmp_path, monkeypatch):
    monkeypatch.delenv("PROEF_PROGRAM", raising=False)
    pauses = PauseRegistry(tmp_path / "P.json", tmp_path / "PH.json")
    halt = HaltSwitch(tmp_path / "HALT", tmp_path / "HH.json")
    store, lab = MemStore(), MemLab()
    g = LabGateway(lab, SafetyEngine(), store, system_actor("t"),
                   governance={"matrix": AuthorizationMatrix(), "halt": halt, "pause": pauses})
    g.pause_poll = 0.02
    return g, lab, store, pauses, halt


def kinds(store):
    return [(r.decision or {}).get("kind") for r in store.recs]


def test_the_registry_pauses_resumes_and_keeps_history(tmp_path):
    p = PauseRegistry(tmp_path / "P.json", tmp_path / "PH.json")
    first = p.pause("program:egfr", "reviewing the plan", "ana")
    again = p.pause("program:egfr", "a second click", "bo")
    assert again["by"] == "ana" and again["reason"] == "reviewing the plan", "pausing twice keeps who paused and why"
    assert p.blocking("prob-1", "egfr")["id"] == first["id"]
    assert p.blocking("prob-1", "kras") is None, "another program is untouched"
    p.pause("campaign:prob-1", "check the reagent", "ana")
    assert p.blocking("prob-1", "egfr")["scope"] == "campaign:prob-1", "a campaign's own pause is reported before its program's"
    assert p.resume("program:egfr", "ana") and not p.resume("program:egfr", "ana")
    assert p.history()[-1]["resumed_by"] == "ana"


def test_a_paused_submission_waits_and_goes_through_when_resumed(rig):
    g, lab, store, pauses, _ = rig
    pauses.pause("campaign:prob-1", "check the reagent", "ana")
    out = {}
    t = threading.Thread(target=lambda: out.update(r=g.submit(proto("prob-1"))))
    t.start()
    time.sleep(0.15)
    assert t.is_alive() and lab.n == 0, "held at the gateway: nothing reached the lab"
    pauses.resume("campaign:prob-1", "ana")
    t.join(5)
    assert out["r"] == ("run-1", None) and lab.n == 1
    ks = kinds(store)
    assert ks.index("paused_hold") < ks.index("pause_released"), "both ends of the hold are in the record"
    hold = next(r.decision for r in store.recs if (r.decision or {}).get("kind") == "paused_hold")
    assert hold["paused_by"] == "ana" and hold["reason"] == "check the reagent"


def test_a_pause_is_scoped_to_its_campaign_or_program(rig, monkeypatch):
    g, lab, store, pauses, _ = rig
    g.pause_timeout = 0.1
    pauses.pause("campaign:prob-1", "x", "ana")
    assert g.submit(proto("prob-2"))[0] == "run-1", "another campaign is not held"
    rid, why = g.submit(proto("prob-1"))
    assert rid is None and "[paused]" in why and "campaign:prob-1" in why
    pauses.resume("campaign:prob-1", "ana")
    pauses.pause("program:egfr-hit-finding", "plan review", "ana")
    monkeypatch.setenv("PROEF_PROGRAM", "EGFR hit finding")       # the campaign process knows its program
    assert g.submit(proto("prob-3"))[0] is None, "a program pause holds every campaign of that program"
    monkeypatch.setenv("PROEF_PROGRAM", "KRAS G12C leads")
    assert g.submit(proto("prob-4"))[0] == "run-2", "a campaign of another program runs"


def test_the_global_halt_still_wins_while_a_submission_is_held(rig):
    g, lab, store, pauses, halt = rig
    pauses.pause("campaign:prob-1", "x", "ana")
    out = {}
    t = threading.Thread(target=lambda: out.update(r=g.submit(proto("prob-1"))))
    t.start()
    time.sleep(0.1)
    halt.engage("emergency")
    t.join(5)
    rid, why = out["r"]
    assert rid is None and "[halt]" in why and lab.n == 0


# ---- the API: pause and resume a campaign or a program, sealed under the person's name ----------------------------

def _client(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from proef.api.app import app
    monkeypatch.setattr("proef.governance.PAUSES_FILE", tmp_path / "P.json")
    monkeypatch.setattr("proef.governance.PAUSES_HISTORY", tmp_path / "PH.json")
    return TestClient(app)


def test_a_campaign_can_be_paused_and_resumed_and_both_are_sealed(tmp_path, monkeypatch):
    from proef.adapters.lab.simulator import SimulatorLab
    from proef.wizard_store import WizardStore
    from tests.test_plan_gate import PlanDecision, _run
    report, recs = _run(tmp_path, lambda plan, pid: PlanDecision(approved=True, by="kim"))
    eid = "pause-api-1"
    ws = WizardStore()
    ws.register_agent(eid, "find IC50<=20", log="", autonomy="gate")
    ws.link_problem(eid, report.problem_id)
    monkeypatch.setattr("proef.views.ids.PROVENANCE_DB", str(tmp_path / "prov.db"))
    with _client(tmp_path, monkeypatch) as c:
        assert c.get(f"/api/v1/campaigns/{eid}").json()["pause"] is None
        r = c.post(f"/api/v1/campaigns/{eid}/pause", json={"paused": True, "reason": "check the reagent"})
        assert r.status_code == 200 and r.json()["paused"] is True
        d = c.get(f"/api/v1/campaigns/{eid}").json()
        assert d["pause"]["by"] == "local-user" and d["pause"]["reason"] == "check the reagent" and d["pause"]["kind"] == "campaign"
        assert [p["scope"] for p in c.get("/api/v1/pauses").json()] == [f"campaign:{report.problem_id}"]
        r = c.post(f"/api/v1/campaigns/{eid}/pause", json={"paused": False})
        assert r.json() == {"paused": False, "pause": None}
        assert c.get(f"/api/v1/campaigns/{eid}").json()["pause"] is None
    from proef.adapters.store.sqlite import SqliteProvenanceStore
    sealed = [r for r in SqliteProvenanceStore(str(tmp_path / "prov.db")).branch_records(f"problem/{report.problem_id}")
              if (r.decision or {}).get("kind") in ("pause", "resume")]
    assert [(r.decision["kind"], r.actor.kind.value, r.actor.id) for r in sealed] == [("pause", "human", "local-user"), ("resume", "human", "local-user")]


def test_a_program_pause_shows_on_the_program_and_on_its_campaigns(tmp_path, monkeypatch):
    import uuid
    name = f"Pause Test {uuid.uuid4().hex[:6]}"
    slug = name.lower().replace(" ", "-")
    with _client(tmp_path, monkeypatch) as c:
        assert c.post("/api/v1/programs", json={"name": name}).status_code == 201
        assert c.get(f"/api/v1/programs/{slug}").json()["pause"] is None
        assert c.post(f"/api/v1/programs/{slug}/pause", json={"paused": True, "reason": "plan review"}).json()["paused"] is True
        p = c.get(f"/api/v1/programs/{slug}").json()["pause"]
        assert p["kind"] == "program" and p["reason"] == "plan review"
        assert c.post(f"/api/v1/programs/{slug}/pause", json={"paused": False}).json()["paused"] is False
        assert c.get(f"/api/v1/programs/{slug}").json()["pause"] is None
        assert c.post("/api/v1/programs/nope/pause", json={"paused": True}).status_code == 404


def test_a_campaign_that_has_not_started_cannot_be_paused_yet(tmp_path, monkeypatch):
    from proef.wizard_store import WizardStore
    eid = "pause-api-new"
    WizardStore().register_agent(eid, "not started", log="", autonomy="gate")
    with _client(tmp_path, monkeypatch) as c:
        r = c.post(f"/api/v1/campaigns/{eid}/pause", json={"paused": True})
        assert r.status_code == 409 and r.json()["detail"]["type"] == "not-started"


def test_the_campaign_list_marks_what_is_held_by_a_pause(tmp_path, monkeypatch):
    from proef.wizard_store import WizardStore
    from tests.test_plan_gate import PlanDecision, _run
    report, _ = _run(tmp_path, lambda plan, pid: PlanDecision(approved=True, by="kim"))
    eid = "pause-list-1"
    WizardStore().register_agent(eid, "find IC50<=20", log="", autonomy="gate")
    WizardStore().link_problem(eid, report.problem_id)
    monkeypatch.setattr("proef.views.ids.PROVENANCE_DB", str(tmp_path / "prov.db"))
    with _client(tmp_path, monkeypatch) as c:
        c.post(f"/api/v1/campaigns/{eid}/pause", json={"paused": True})
        mine = next(x for x in c.get("/api/v1/campaigns").json() if x["id"] == eid)
        # this registered campaign is not a running process, so nothing is being held: 'paused' is only for work in progress
        assert mine["paused"] is False


def test_a_running_campaign_that_is_not_linked_yet_can_still_be_paused(tmp_path, monkeypatch):
    """Found in a live run: a running campaign has announced its problem id but the registry has no branch for it yet."""
    from proef.adapters.store.sqlite import SqliteProvenanceStore
    from proef.wizard_store import WizardStore
    eid = "pause-running-1"
    WizardStore().register_agent(eid, "still running", log="", autonomy="gate")   # no link_problem: the registry has no branch
    monkeypatch.setattr("proef.autoloop.announced_branches", lambda: {eid: "prob-running1"})
    monkeypatch.setattr("proef.views.ids.PROVENANCE_DB", str(tmp_path / "prov.db"))
    with _client(tmp_path, monkeypatch) as c:
        r = c.post(f"/api/v1/campaigns/{eid}/pause", json={"paused": True, "reason": "live"})
        assert r.status_code == 200 and r.json()["paused"] is True, r.text
        assert [p["scope"] for p in c.get("/api/v1/pauses").json()] == ["campaign:prob-running1"]
    sealed = [r for r in SqliteProvenanceStore(str(tmp_path / "prov.db")).branch_records("problem/prob-running1")
              if (r.decision or {}).get("kind") == "pause"]
    assert len(sealed) == 1 and sealed[0].actor.kind.value == "human"
