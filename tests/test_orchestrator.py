"""Phase 4B, 4C and 4D of docs/LLM-ORCHESTRATION.md: the decision after a round is an explicit state with computed allowed actions, an
orchestrator (fixed or a model) chooses among them, every choice is recorded, and a kill switch and a wall clock end a campaign."""
import json

import pytest

from proef.adapters.lab.simulator import SimulatorLab
from proef.autoloop import ResourceBudget, run_campaign
from proef.campaign_fsm import Choice, FixedOrchestrator, ModelOrchestrator, State, View, allowed_actions
from proef.core.contracts import EventType, ToolResult
from proef.reproduce import reproduce

import tests.test_followups as F

P = "You steer a research campaign"


def view(**kw):
    base = dict(round_no=1, rounds=2, verdicts={"A": "inconclusive"}, abstained=["A"], queued=3, runs_left=10, done=set())
    return View(**{**base, **kw})


def allowed(v=None, **kw):
    base = dict(view=v or view(), followups_permitted=True, replan_permitted=True, has_model=True, agent_source=True)
    return allowed_actions(State.ROUND_JUDGED, **{**base, **kw})


# ---- allowed actions are computed from preconditions ----

def test_the_allowed_actions_follow_from_what_is_permitted_and_what_remains():
    assert allowed() == ["follow_up", "replan", "next_round", "stop"]
    assert allowed(followups_permitted=False) == ["replan", "next_round", "stop"], "the operator's switch is a permission"
    assert allowed(replan_permitted=False) == ["follow_up", "next_round", "stop"]
    assert allowed(has_model=False) == ["replan", "next_round", "stop"]
    assert allowed(agent_source=False) == ["follow_up", "next_round", "stop"], "a candidate list has no model to re-plan with"
    assert allowed(view(abstained=[])) == ["replan", "next_round", "stop"], "nothing abstained, nothing to follow up"
    assert allowed(view(round_no=2)) == ["follow_up", "next_round"], "no round left to re-plan in, and nothing left to stop"
    assert allowed(view(done={"follow_up", "replan"})) == ["next_round", "stop"], "each action once per round"
    assert allowed(followups_permitted=False, replan_permitted=False, view=view(abstained=[])) == ["next_round", "stop"]


def test_the_fixed_orchestrator_keeps_the_order_the_engine_always_had_and_never_stops():
    f = FixedOrchestrator()
    assert [f.choose(State.ROUND_JUDGED, a, view()).action for a in (["follow_up", "replan", "next_round", "stop"],
                                                                      ["replan", "next_round", "stop"], ["next_round", "stop"])] == \
        ["follow_up", "replan", "next_round"]


# ---- the model orchestrator ----

class Fake:
    name, model, model_version = "fake", "f", "f-1"

    def __init__(self, text=None, boom=False):
        self.text, self.boom, self.prompts = text, boom, []

    def generate(self, inv, prompt, **kw):
        self.prompts.append(prompt)
        if self.boom:
            raise RuntimeError("model down")
        return ToolResult(envelope=inv.envelope.model_copy(), invocation_id=inv.invocation_id, status="succeeded", output={"text": self.text})


def test_a_valid_choice_is_taken_and_the_model_was_shown_only_the_allowed_actions():
    m = Fake('{"action": "stop", "why": "goal is met"}')
    c = ModelOrchestrator(m).choose(State.ROUND_JUDGED, ["next_round", "stop"], view())
    assert (c.action, c.source, c.valid, c.why) == ("stop", "model", True, "goal is met")
    assert "replan" not in m.prompts[0].split("YOU MAY CHOOSE")[1].split("Choose the single")[0], "never offered what is not allowed"


@pytest.mark.parametrize("text", ['{"action": "replan"}', '{"action": "delete_everything"}', "not json at all", '{"action": ["stop"]}', "{}"])
def test_anything_outside_the_allowed_list_falls_back_to_the_fixed_choice_and_says_so(text):
    c = ModelOrchestrator(Fake(text)).choose(State.ROUND_JUDGED, ["next_round", "stop"], view())
    assert (c.action, c.source, c.valid) == ("next_round", "fixed-fallback", False) and c.reasons


def test_a_failed_or_refused_model_call_falls_back_too():
    c = ModelOrchestrator(Fake(boom=True)).choose(State.ROUND_JUDGED, ["follow_up", "next_round", "stop"], view())
    assert (c.action, c.source, c.valid) == ("follow_up", "fixed-fallback", False) and "could not be used" in c.reasons[0]


# ---- the engine ----

class OModel(F.Model):
    """The campaign's scripted model that also answers the orchestrator's question from a script."""

    def __init__(self, answer, script):
        super().__init__(answer)
        self.script, self.asked = list(script), []

    def generate(self, inv, prompt, **kw):
        if P in prompt:
            self.prompts.append(prompt)
            self.asked.append(prompt)
            text = json.dumps(self.script.pop(0) if self.script else {"action": "next_round", "why": "default"})
            return ToolResult(envelope=inv.envelope.model_copy(), invocation_id=inv.invocation_id, status="succeeded", output={"text": text},
                              model_version="s-1")
        return super().generate(inv, prompt, **kw)


def _orch_run(tmp_path, script, **kw):
    import proef.autoloop as al
    store_model = OModel(F._answer, script)
    gate_fn = kw.pop("autonomy_gate", None)          # a plan approver: the campaign then runs under approvals
    store = F.SqliteProvenanceStore(str(tmp_path / "prov.db"))
    cfg = F.CampaignConfig(problem=F.ProblemStatement(objective="o", constraints=[F.Constraint(name="potency", description="IC50 <= 20 uM", kind="hard", limit=20.0, unit="uM")]),
                           hypothesis_source="agent", model=store_model, autonomy="gate" if gate_fn else "act", plan_approver=gate_fn, k=8, max_families=8, rounds=kw.pop("rounds", 2), seed=1,
                           followups=kw.pop("followups", True), replan=kw.pop("replan", False), orchestrator=kw.pop("orchestrator", "model"),
                           inbox=str(tmp_path / "inbox"), poll_secs=0.05, flywheel=F.Flywheel(":memory:"), **kw)
    report = run_campaign(cfg, kw.get("lab") or SimulatorLab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    return report, store.branch_records(f"problem/{report.problem_id}"), store_model


def choices(recs):
    return [(r.decision["round"], r.decision["chosen"], r.decision["source"], r.decision["valid"]) for r in recs if (r.decision or {}).get("kind") == "orchestrator_choice"]


def test_the_fixed_orchestrator_records_the_choices_it_makes_and_changes_nothing(tmp_path):
    report, store, recs, model, _ = F._run(tmp_path)         # followups on, fixed orchestrator (the default)
    assert choices(recs)[0][1:] == ("follow_up", "fixed", True) and choices(recs)[1][1] == "next_round"
    assert [r for r in recs if (r.decision or {}).get("followup_of")], "the same follow-up ran as before the state machine"


def test_a_model_that_chooses_next_round_skips_follow_ups_the_operator_only_permitted(tmp_path):
    report, recs, model = _orch_run(tmp_path, [{"action": "next_round", "why": "not worth it"}])
    assert choices(recs)[0] == (1, "next_round", "model", True)
    assert not [r for r in recs if (r.decision or {}).get("kind") == "followup_proposal"], "permitted is not required"


def test_a_model_chooses_follow_up_then_next_round_and_is_asked_again_after_each_action(tmp_path):
    report, recs, model = _orch_run(tmp_path, [{"action": "follow_up"}, {"action": "next_round"}])
    assert [c[1] for c in choices(recs)][:2] == ["follow_up", "next_round"]
    assert "ALREADY DONE THIS ROUND: follow_up" in model.asked[1] and "follow_up" not in model.asked[1].split("YOU MAY CHOOSE")[1].split("Choose the")[0]
    assert [r for r in recs if (r.decision or {}).get("followup_of")], "the chosen follow-up ran"


def test_a_model_can_end_the_campaign_and_what_was_queued_is_left_untested(tmp_path):
    report, recs, model = _orch_run(tmp_path, [{"action": "stop", "why": "enough evidence"}])
    assert report.stopped == "orchestrator" and report.rounds_run == 1
    stop = [r for r in recs if (r.decision or {}).get("kind") == "campaign_stopped"]
    assert len(stop) == 1 and stop[0].decision["why"] == "orchestrator" and stop[0].decision["detail"] == "enough evidence"


def test_an_invalid_choice_falls_back_to_the_fixed_order_and_the_record_says_so(tmp_path):
    report, recs, model = _orch_run(tmp_path, [{"action": "replan"}])        # replan is not permitted in this campaign
    c = next(r.decision for r in recs if (r.decision or {}).get("kind") == "orchestrator_choice")
    assert (c["chosen"], c["source"], c["valid"]) == ("follow_up", "fixed-fallback", False) and "not one of the allowed" in c["reasons"][0]


def test_a_custom_orchestrator_cannot_leave_the_allowed_list(tmp_path):
    class Rogue:
        def choose(self, state, allowed, view):
            return Choice("launch_the_missiles", "because")
    report, recs, _ = _orch_run(tmp_path, [], orchestrator=Rogue())
    c = next(r.decision for r in recs if (r.decision or {}).get("kind") == "orchestrator_choice")
    assert (c["chosen"], c["valid"], c["source"]) == ("next_round", False, "fixed-fallback")


def test_an_orchestrated_campaign_replays_from_the_ledger(tmp_path):
    report, recs, model = _orch_run(tmp_path, [{"action": "follow_up"}, {"action": "next_round"}])
    res = reproduce(f"problem/{report.problem_id}", db_path=str(tmp_path / "prov.db"))
    assert res["status"] == "match" and res["model_pinned"] is True, res
    assert res["model_calls_replayed"] == len(model.prompts), "the orchestrator's answers were played back too"


def test_a_model_orchestrator_is_governed_like_any_model_call(tmp_path):
    """A budget of 1 model call: the plan is proposed, then every orchestrator question is refused and the fixed order answers."""
    report, recs, model = _orch_run(tmp_path, [{"action": "stop"}], budget=ResourceBudget(max_model_calls=1))
    assert all(c[2] == "fixed-fallback" for c in choices(recs)) and report.stopped is None
    assert [r for r in recs if (r.decision or {}).get("kind") == "budget_refusal"]


def test_the_flags_reach_the_engine(tmp_path):
    from proef.api.launch import build_cfg
    from proef.cli import _campaign_from_doc
    doc = {"objective": "o", "gate_value": 1.0, "rounds": 2, "autonomy": "gate", "orchestrator": "model"}
    assert _campaign_from_doc(build_cfg(doc)).orchestrator == "model"
    assert _campaign_from_doc(build_cfg({k: v for k, v in doc.items() if k != "orchestrator"})).orchestrator == "fixed"
    assert "orchestrator" not in build_cfg({**doc, "candidates": "A,B"}), "a candidate list has no model to ask"


# ---- 4D: the kill switch and the wall clock ----

def test_a_halt_engaged_during_a_campaign_ends_it_at_the_next_transition(tmp_path, monkeypatch):
    import proef.governance as G
    monkeypatch.setattr(G, "HALT_FILE", tmp_path / "HALT")
    monkeypatch.setattr(G, "HALT_HISTORY", tmp_path / "HALT-history.json")

    class HaltsAtTheFirstDecision:
        def choose(self, state, allowed, view):
            G.HaltSwitch().engage("spill in cabinet 2")           # an operator hits the switch while the campaign runs
            return Choice("next_round", "carry on")
    report, recs, _ = _orch_run(tmp_path, [], orchestrator=HaltsAtTheFirstDecision(), followups=False)
    stop = [r for r in recs if (r.decision or {}).get("kind") == "campaign_stopped"]
    assert report.stopped == "halt" and len(stop) == 1 and "spill in cabinet 2" in stop[0].decision["detail"]
    assert report.rounds_run == 1, "round 2 never started"


def test_the_wall_clock_budget_ends_a_campaign_and_is_recorded(tmp_path):
    import time

    class Slow(OModel):
        def generate(self, inv, prompt, **kw):
            time.sleep(0.6)
            return super().generate(inv, prompt, **kw)
    store = F.SqliteProvenanceStore(str(tmp_path / "prov.db"))
    cfg = F.CampaignConfig(problem=F.ProblemStatement(objective="o", constraints=[F.Constraint(name="potency", description="IC50 <= 20 uM", kind="hard", limit=20.0, unit="uM")]),
                           hypothesis_source="agent", model=Slow(F._answer, []), autonomy="act", k=8, max_families=8, rounds=2, seed=1,
                           inbox=str(tmp_path / "inbox"), poll_secs=0.05, flywheel=F.Flywheel(":memory:"), budget=ResourceBudget(wall_clock_secs=0.3))
    report = run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    recs = store.branch_records(f"problem/{report.problem_id}")
    assert report.stopped == "wall_clock" and [r for r in recs if (r.decision or {}).get("why") == "wall_clock"]
    assert not [r for r in recs if r.event_type == EventType.lab_run], "stopped before any lab run"


def test_a_generous_wall_clock_changes_nothing(tmp_path):
    report, recs, _ = _orch_run(tmp_path, [], orchestrator="fixed", budget=ResourceBudget(wall_clock_secs=3600))
    assert report.stopped is None and report.verdicts


def test_no_tool_a_model_is_given_can_reach_the_halt_or_pause_state():
    """The kill switch is only a kill switch if the thing it stops cannot touch it."""
    import re
    from pathlib import Path
    from proef.mcp_server import TOOLS
    from proef.toolkit import AgentToolkit
    names = {t["name"] for t in TOOLS} | {t["name"] for t in AgentToolkit.schemas()}
    assert not [n for n in names if re.search(r"halt|pause|resume|kill|stop", n)], names
    root = Path(__file__).resolve().parents[1] / "src" / "proef"
    for f in ("toolkit.py", "mcp_server.py"):
        src = (root / f).read_text()
        assert not re.search(r"\.(engage|clear|pause|resume)\(|HaltSwitch|PauseRegistry|HALT_FILE|PAUSES_FILE", src), f"{f} touches halt or pause"


def test_the_orchestrators_choices_and_an_early_end_show_in_the_campaign_feed(tmp_path):
    from proef.api.v1.campaigns import activity_items
    report, recs, _ = _orch_run(tmp_path, [{"action": "stop", "why": "enough evidence"}])
    text = " | ".join(i.text for i in activity_items(recs))
    assert "After round 1: the model chose stop: enough evidence" in text and "Campaign ended early by the orchestrator" in text


def test_a_campaign_ended_by_the_wall_clock_spends_no_more_model_calls(tmp_path):
    """The advisory LLM reviewer runs after the rounds. After a stop it would be one more call on a campaign that ran out of time."""
    import time

    class Slow(OModel):
        def generate(self, inv, prompt, **kw):
            time.sleep(0.6)
            return super().generate(inv, prompt, **kw)
    store = F.SqliteProvenanceStore(str(tmp_path / "prov.db"))
    model = Slow(F._answer, [])
    cfg = F.CampaignConfig(problem=F.ProblemStatement(objective="o", constraints=[F.Constraint(name="potency", description="IC50 <= 20 uM", kind="hard", limit=20.0, unit="uM")]),
                           hypothesis_source="agent", model=model, autonomy="act", k=8, max_families=8, rounds=2, seed=1,
                           inbox=str(tmp_path / "inbox"), poll_secs=0.05, flywheel=F.Flywheel(":memory:"), budget=ResourceBudget(wall_clock_secs=0.3))
    report = run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    recs = store.branch_records(f"problem/{report.problem_id}")
    assert report.stopped == "wall_clock" and not [r for r in recs if (r.decision or {}).get("kind") == "review_report_llm"]
    assert len(model.prompts) == 1, "only the proposal was ever asked"


# ---- found by the independent review of Phase 4 ----

def test_stop_is_not_offered_on_the_last_round_and_the_prompt_says_what_a_last_round_follow_up_is():
    assert "stop" not in allowed(view(round_no=2)) and "stop" in allowed(view(round_no=1))
    m = Fake('{"action": "next_round"}')
    ModelOrchestrator(m).choose(State.ROUND_JUDGED, ["follow_up", "next_round"], view(round_no=2))
    assert "last round, so nothing can run" in m.prompts[0]


def test_a_halt_during_the_last_round_is_not_recorded_as_ending_the_campaign_early(tmp_path, monkeypatch):
    import proef.governance as G
    monkeypatch.setattr(G, "HALT_FILE", tmp_path / "HALT")
    monkeypatch.setattr(G, "HALT_HISTORY", tmp_path / "HALT-history.json")

    class HaltsOnTheLastRound:
        """Engages the switch and asks for a follow-up, so the decision loop goes round again and its next limit check sees the halt."""
        def __init__(self):
            self.asked = 0

        def choose(self, state, allowed, view):
            self.asked += 1
            if self.asked == 1:
                G.HaltSwitch().engage("end of shift")
                return Choice("follow_up", "one more")
            return Choice("next_round", "carry on")
    orch = HaltsOnTheLastRound()
    report, recs, _ = _orch_run(tmp_path, [], orchestrator=orch, followups=True, rounds=1)
    assert orch.asked == 1, "the loop was cut short by the halt, not by the orchestrator"
    assert report.stopped is None and not [r for r in recs if (r.decision or {}).get("kind") == "campaign_stopped"]
    assert report.verdicts, "every round had run"


def test_time_spent_waiting_for_a_person_does_not_count_against_the_wall_clock(tmp_path):
    import time
    from proef.plan import PlanDecision

    def slow_human(plan, pid):
        time.sleep(0.7)                  # a person takes a while over the plan
        return PlanDecision(approved=True, by="kim")
    # one round: the check that matters is the one before it starts, after the person's 0.7s. The campaign's own work takes longer than the
    # 0.5s budget in total, which is why this does not look at the later checks
    report, recs, _ = _orch_run(tmp_path, [], orchestrator="fixed", followups=False, rounds=1, budget=ResourceBudget(wall_clock_secs=0.5),
                                autonomy_gate=slow_human)
    assert report.stopped is None and any(r.event_type == EventType.lab_run for r in recs)


def test_the_programs_time_limit_reaches_the_engine():
    from proef.api.launch import build_cfg
    from proef.api.v1 import schemas as S
    from proef.api.v1.programs import campaign_doc
    from proef.cli import _campaign_from_doc
    p = {"slug": "s", "name": "n", "program": {"objective": "o", "metric": "ic50_um", "direction": "min", "gate_value": 20, "unit": "uM",
                                                "limits": {"max_wall_minutes": 2}}}
    doc = campaign_doc(p, S.CampaignCreate())
    assert doc["budget"]["wall_clock_secs"] == 120.0 and _campaign_from_doc(build_cfg(doc)).budget.wall_clock_secs == 120.0
    p["program"]["limits"] = {}
    assert "wall_clock_secs" not in campaign_doc(p, S.CampaignCreate())["budget"], "0 means no limit"


def test_the_orchestrator_keeps_a_reserve_of_model_calls_for_the_real_work():
    class Gov(Fake):
        def __init__(self, left, **kw):
            super().__init__(**kw)
            self.left = left

        def remaining(self):
            return self.left
    low, ok = Gov(3, text='{"action": "stop"}'), Gov(30, text='{"action": "stop"}')
    c = ModelOrchestrator(low).choose(State.ROUND_JUDGED, ["next_round", "stop"], view())
    assert (c.source, c.action) == ("fixed-fallback", "next_round") and "kept for" in c.reasons[0] and not low.prompts, "no call was spent"
    assert ModelOrchestrator(ok).choose(State.ROUND_JUDGED, ["next_round", "stop"], view()).action == "stop"
