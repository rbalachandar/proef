"""Phase 3A of docs/LLM-ORCHESTRATION.md: a campaign's model calls obey the halt, the pauses and a call budget, like its lab
submissions. Every call and every refusal is on the ledger."""
import json
import threading
import time

import pytest

from proef.adapters.lab.simulator import SimulatorLab
from proef.adapters.store.sqlite import SqliteProvenanceStore
from proef.autoloop import CampaignConfig, ResourceBudget, run_campaign
from proef.core.contracts import ToolResult
from proef.core.hypothesis import Constraint, ProblemStatement
from proef.core.interfaces import system_actor
from proef.flywheel import Flywheel
from proef.governance import HaltSwitch, PauseRegistry
from proef.model_guard import GovernedModel, ModelGuard, ModelRefused
from proef.plan import PlanDecision

PLAN = {"families": [{"family": "f", "parent_statement": "claim", "kind": "mechanistic", "mechanism": "m",
                      "refutation_condition": "r", "confidence": 0.6,
                      "variants": [{"statement": "A binds", "candidate": "A", "assay_type": "binding", "predicted": "p",
                                    "predicted_value": 20.0, "rationale": "x"}]}]}


class Scripted:
    name, model_version, model = "scripted", "s-1", "scripted-1"

    def __init__(self):
        self.calls = 0

    def generate(self, inv, prompt, **kw):
        self.calls += 1
        text = json.dumps(PLAN) if "hypothesis lead" in prompt else json.dumps({"notes": []})
        return ToolResult(envelope=inv.envelope.model_copy(), invocation_id=inv.invocation_id, status="succeeded",
                          output={"text": text}, model_version=self.model_version)

    def predict(self, inv, kind, payload):
        raise NotImplementedError


def _problem():
    return ProblemStatement(objective="o", constraints=[Constraint(name="potency", description="IC50 <= 20 uM", kind="hard", limit=20.0, unit="uM")])


def _campaign(tmp_path, model=None, critic=None, budget=None, replan=False, rounds=1):
    store = SqliteProvenanceStore(str(tmp_path / "prov.db"))
    cfg = CampaignConfig(problem=_problem(), hypothesis_source="agent", model=model or Scripted(), critic_model=critic,
                         autonomy="gate", k=2, rounds=rounds, seed=1, inbox=str(tmp_path / "inbox"), poll_secs=0.05,
                         flywheel=Flywheel(":memory:"), budget=budget, replan=replan,
                         plan_approver=lambda plan, pid: PlanDecision(approved=True, by="t"))
    return cfg, store


def _recs(store, report_or_pid):
    pid = getattr(report_or_pid, "problem_id", report_or_pid)
    return store.branch_records(f"problem/{pid}")


def _kinds(recs, kind):
    return [r for r in recs if (r.decision or {}).get("kind") == kind]


# ---- every call is a record ----

def test_every_model_call_in_a_campaign_is_a_ledger_record(tmp_path):
    model = Scripted()
    cfg, store = _campaign(tmp_path, model=model, critic=Scripted())
    report = run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    calls = _kinds(_recs(store, report), "model_call")
    assert len(calls) >= 2 and [c.decision["n"] for c in calls] == list(range(1, len(calls) + 1))
    assert {c.decision["role"] for c in calls} >= {"model", "critic"}
    assert all(len(c.decision["prompt_hash"]) == 12 for c in calls)


def test_the_config_passed_in_is_not_mutated(tmp_path):
    cfg, store = _campaign(tmp_path)
    raw = cfg.model
    run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    assert cfg.model is raw and not isinstance(cfg.model, GovernedModel)


# ---- halt ----

def test_a_halt_refuses_the_model_call_and_nothing_reaches_the_model(tmp_path, monkeypatch):
    import proef.governance as G
    monkeypatch.setattr(G, "HALT_FILE", tmp_path / "HALT")
    monkeypatch.setattr(G, "HALT_HISTORY", tmp_path / "HALT-history.json")
    HaltSwitch().engage("spill in cabinet 2")
    model = Scripted()
    cfg, store = _campaign(tmp_path, model=model)
    with pytest.raises(ModelRefused, match="halt"):
        run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    assert model.calls == 0, "the model was never reached"
    pid = next(b for b in store.branches() if b.startswith("problem/")).split("/", 1)[1]
    assert _kinds(_recs(store, pid), "halt_refusal") and not _kinds(_recs(store, pid), "model_call")


# ---- pause ----

def _guard(tmp_path, max_calls=50, timeout=None):
    store = SqliteProvenanceStore()
    pause = PauseRegistry(path=tmp_path / "pauses.json", history_path=tmp_path / "ph.json")
    gov = {"halt": HaltSwitch(path=tmp_path / "HALT", history_path=tmp_path / "H"), "pause": pause}
    return ModelGuard(store, system_actor(), "p1", gov, max_calls, pause_poll=0.01, pause_timeout=timeout), store, pause, gov


def test_a_campaign_pause_holds_the_model_call_until_it_is_resumed(tmp_path):
    guard, store, pause, _ = _guard(tmp_path)
    pause.pause("campaign:p1", "check the reagents", by="kim")
    done = threading.Event()
    threading.Thread(target=lambda: (guard.check("model", "x"), done.set()), daemon=True).start()
    time.sleep(0.15)
    assert not done.is_set(), "held while paused"
    pause.resume("campaign:p1", by="kim")
    assert done.wait(2.0), "released on resume"
    recs = store.branch_records("problem/p1")
    assert _kinds(recs, "paused_hold") and _kinds(recs, "model_call")


def test_a_pause_that_does_not_end_refuses_at_the_timeout(tmp_path):
    guard, store, pause, _ = _guard(tmp_path, timeout=0.05)
    pause.pause("campaign:p1", "long hold")
    with pytest.raises(ModelRefused, match="paused"):
        guard.check("model", "x")
    assert _kinds(store.branch_records("problem/p1"), "paused_refusal")


def test_a_halt_while_paused_still_wins(tmp_path):
    guard, store, pause, gov = _guard(tmp_path)
    pause.pause("campaign:p1", "hold")
    threading.Timer(0.05, lambda: gov["halt"].engage("stop")).start()
    with pytest.raises(ModelRefused, match="halt"):
        guard.check("model", "x")


# ---- budget ----

def test_the_model_call_budget_refuses_and_records_the_exhaustion_once(tmp_path):
    guard, store, _, _ = _guard(tmp_path, max_calls=2)
    guard.check("model", "a"); guard.check("model", "b")
    for _ in range(3):
        with pytest.raises(ModelRefused, match="budget exhausted"):
            guard.check("model", "c")
    recs = store.branch_records("problem/p1")
    assert len(_kinds(recs, "model_call")) == 2
    assert len(_kinds(recs, "budget_exhausted")) == 1 and len(_kinds(recs, "budget_refusal")) == 3


def test_a_store_that_cannot_be_read_refuses_rather_than_report_zero_calls(tmp_path):
    guard, store, _, _ = _guard(tmp_path)
    store.branch_records = lambda b: (_ for _ in ()).throw(RuntimeError("disk gone"))
    with pytest.raises(ModelRefused, match="cannot be read"):
        guard.check("model", "x")


def test_an_exhausted_budget_skips_the_critic_and_the_replan_and_the_campaign_goes_on(tmp_path):
    """Budget of 1: the proposal is made. The critic and the re-plan are refused, recorded, and the campaign finishes."""
    cfg, store = _campaign(tmp_path, critic=Scripted(), budget=ResourceBudget(max_model_calls=1), replan=True, rounds=2)
    report = run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    recs = _recs(store, report)
    assert len(_kinds(recs, "model_call")) == 1
    assert len(_kinds(recs, "budget_refusal")) >= 2 and report.verdicts.get("A")


# ---- the operator's cap reaches the engine (the replan and modality lesson) ----

def test_the_programs_model_call_cap_reaches_the_engine_and_a_tree_shares_it():
    from proef.api import v1 as _v1
    from proef.api.launch import build_cfg
    from proef.api.v1 import schemas as S
    from proef.api.v1.programs import campaign_doc
    from proef.cli import _campaign_from_doc
    p = {"slug": "s", "name": "n", "program": {"objective": "o", "metric": "ic50_um", "direction": "min", "gate_value": 20,
                                                "unit": "uM", "limits": {"max_model_calls": 40}}}
    doc = campaign_doc(p, S.CampaignCreate())
    assert doc["budget"]["max_model_calls"] == 40
    assert _campaign_from_doc(build_cfg(doc)).budget.max_model_calls == 40
    assert campaign_doc(p, S.CampaignCreate(), model_calls=10)["budget"]["max_model_calls"] == 10, "a leaf's slice"
    assert campaign_doc(p, S.CampaignCreate(), model_calls=999)["budget"]["max_model_calls"] == 40, "never more than the program allows"
    assert S.Limits().max_model_calls == 200, "programs created before the cap existed get the default"


# ---- found by the independent review of Phase 3 ----

def test_a_pause_hold_is_closed_in_the_ledger_when_it_is_released(tmp_path):
    guard, store, pause, _ = _guard(tmp_path)
    pause.pause("campaign:p1", "check the reagents", by="kim")
    done = threading.Event()
    threading.Thread(target=lambda: (guard.check("model", "x"), done.set()), daemon=True).start()
    time.sleep(0.1)
    pause.resume("campaign:p1", by="kim")
    assert done.wait(2.0)
    released = _kinds(store.branch_records("problem/p1"), "pause_released")
    assert len(released) == 1 and released[0].decision["held_seconds"] >= 0.0 and len(_kinds(store.branch_records("problem/p1"), "paused_hold")) == 1


def test_the_ledger_is_scanned_once_not_on_every_call(tmp_path):
    guard, store, _, _ = _guard(tmp_path, max_calls=100)
    scans = []
    real = store.branch_records
    store.branch_records = lambda b: (scans.append(b), real(b))[1]
    for i in range(10):
        guard.check("model", str(i))
    assert len(scans) == 1
    assert len(_kinds(real("problem/p1"), "model_call")) == 10 and guard.used() == 10
