"""Phase 5A of docs/LLM-ORCHESTRATION.md: the checkpoint audit. Every rule must catch a synthetic violation (a rule never seen to fire is not
trusted), and real campaigns run every way the engine can be driven must audit clean."""
from types import SimpleNamespace as NS

import pytest

from proef.audit import audit_campaign


def rec(event, **decision):
    actor = decision.pop("_actor", NS(kind=NS(value="system"), id="engine"))
    return NS(event_type=NS(value=event), decision=decision, actor=actor, artifact=None)


def env(**over):
    base = {"autonomy": "act", "plan_review": False, "budget": None}
    return rec("problem_defined", problem_id="p", constraints=[{"kind": "hard", "limit": 20.0}], environment={**base, **over})


def rules(recs, **kw):
    return {x.rule for x in audit_campaign(recs, **kw)}


RUN = lambda i, title="test-A": rec("lab_run", run_id=f"r{i}", title=title)  # noqa: E731
VERDICT = lambda **kw: rec("hypothesis_validated", metric="ic50_um", threshold=20.0, criteria_preregistered=True, evidence_run_id="r1", **kw)  # noqa: E731


def test_an_empty_clean_ledger_has_no_violations():
    assert audit_campaign([env(), RUN(1), VERDICT()]) == []


# ---- one synthetic violation per rule ----

def test_observe_that_ran_something():
    assert "observe_runs_nothing" in rules([env(autonomy="observe"), RUN(1)])


def test_a_gated_run_with_no_plan_cover():
    recs = [env(autonomy="gate", plan_review=True), RUN(1), RUN(2, "test-B"), rec("gate_decision", kind="covered_by_plan")]
    assert "gated_runs_are_covered" in rules(recs)
    assert "gated_runs_are_covered" not in rules(recs + [rec("gate_decision", kind="covered_by_plan")])
    assert "gated_runs_are_covered" not in rules([env(autonomy="gate", plan_review=True), RUN(1, "auto-confirmation-A"),
                                                  rec("gate_decision", kind="covered_by_plan")]), "confirmations follow their own gate"


def test_a_machine_that_wrote_a_persons_decision():
    machine = NS(kind=NS(value="agent"), id="campaign")
    person = NS(kind=NS(value="human"), id="kim")
    assert "no_decision_by_a_machine" in rules([env(), rec("gate_decision", kind="human_review", decision="accept", _actor=machine)])
    assert "no_decision_by_a_machine" not in rules([env(), rec("gate_decision", kind="human_review", decision="accept", _actor=person)])


def test_a_verdict_judged_at_someone_elses_threshold():
    assert "gate_is_the_programs" in rules([env(), RUN(1), rec("hypothesis_validated", metric="ic50_um", threshold=0.001, criteria_preregistered=True, evidence_run_id="r1")])


def test_a_verdict_on_a_placeholder_that_did_not_abstain():
    assert "placeholder_abstains" in rules([env(), RUN(1), rec("hypothesis_invalidated", criteria_preregistered=False, evidence_run_id="r1")])
    assert "placeholder_abstains" not in rules([env(), RUN(1), rec("hypothesis_inconclusive", criteria_preregistered=False, evidence_run_id="r1")])


def test_a_verdict_with_no_run_behind_it():
    assert "verdict_has_evidence" in rules([env(), RUN(1), rec("hypothesis_validated", metric="ic50_um", threshold=20.0, criteria_preregistered=True, evidence_run_id="nope")])


def test_a_verdict_written_by_an_agent_in_free_text():
    assert "no_free_text_verdict" in rules([env(), rec("hypothesis_validated", declared_by="agent (free-text evidence; not a tool-verified fit)")])


def test_a_choice_outside_the_allowed_list():
    assert "choice_is_allowed" in rules([env(), rec("gate_decision", kind="orchestrator_choice", round=1, chosen="launch", allowed=["next_round"])])


def test_work_after_a_stop():
    stopped = rec("gate_decision", kind="campaign_stopped", why="halt")
    assert "nothing_after_a_stop" in rules([env(), stopped, RUN(1)])
    assert "nothing_after_a_stop" in rules([env(), stopped, rec("tool_invocation", kind="model_call", n=1)])
    assert "nothing_after_a_stop" not in rules([env(), RUN(1), stopped])
    # a stop the orchestrator chose ends the rounds; the advisory review of what was learned still runs, but nothing else does
    chosen = rec("gate_decision", kind="campaign_stopped", why="orchestrator")
    review = rec("tool_invocation", kind="model_call", n=1, role="reviewer")
    assert "nothing_after_a_stop" not in rules([env(), chosen, review, rec("tool_invocation", kind="model_response", n=1)])
    assert "nothing_after_a_stop" in rules([env(), chosen, RUN(1)])
    assert "nothing_after_a_stop" in rules([env(), chosen, rec("tool_invocation", kind="model_call", n=1, role="model")])
    assert "nothing_after_a_stop" in rules([env(), stopped, review]), "after a halt not even the review calls a model"


def test_budgets_that_did_not_hold():
    assert "run_budget_holds" in rules([env(budget={"max_total_runs": 1}), RUN(1), RUN(2, "test-B")])
    calls = [rec("tool_invocation", kind="model_call", n=i) for i in (1, 2, 3)]
    answers = [rec("tool_invocation", kind="model_response", n=i) for i in (1, 2, 3)]
    assert "model_call_budget_holds" in rules([env(budget={"max_model_calls": 2})] + calls + answers)


def test_a_model_call_with_no_sealed_answer_or_a_gap_in_the_numbers():
    assert "model_calls_are_sealed" in rules([env(), rec("tool_invocation", kind="model_call", n=1)])
    assert "model_calls_are_sealed" not in rules([env(), rec("tool_invocation", kind="model_call", n=1), rec("tool_invocation", kind="model_error", n=1)])
    both = [rec("tool_invocation", kind=k, n=n) for n in (1, 3) for k in ("model_call", "model_response")]
    assert "model_calls_are_numbered" in rules([env()] + both)


def test_a_persons_decision_marked_executable_and_a_follow_up_nobody_validated():
    assert "person_decisions_are_not_executed" in rules([env(), rec("gate_decision", kind="followup_proposal", hypothesis_id="h", action="drop", executes=True)])
    orphan = rec("hypothesis_proposed", followup_of="h1")
    assert "follow_up_was_validated" in rules([env(), orphan])
    bad = rec("gate_decision", kind="followup_proposal", hypothesis_id="h1", action="follow_up", executes=True, validation={"ok": False})
    assert "follow_up_was_validated" in rules([env(), bad, orphan])
    good = rec("gate_decision", kind="followup_proposal", hypothesis_id="h1", action="follow_up", executes=True, validation={"ok": True})
    assert "follow_up_was_validated" not in rules([env(), good, orphan])


def test_a_hypothesis_the_lab_refused_that_ran_anyway():
    assert "unserved_hypotheses_do_not_run" in rules([env(), rec("gate_decision", kind="lab_cannot_serve", candidate="B"), RUN(1, "test-B")])


def test_a_broken_chain():
    class Bad:
        def verify_chain(self, branch):
            return [{"problem": "payload-tampered"}]
    assert "chain_verifies" in rules([env()], store=Bad(), branch="problem/p")

    class Fine:
        def verify_chain(self, branch):
            return []
    assert "chain_verifies" not in rules([env()], store=Fine(), branch="problem/p")


# ---- real campaigns, run every way the engine can be driven, audit clean ----

def _clean(store, report):
    from proef.audit import audit_campaign as a
    branch = f"problem/{report.problem_id}"
    return a(store.branch_records(branch), store=store, branch=branch)


def test_campaigns_driven_by_every_orchestrator_and_autonomy_audit_clean(tmp_path):
    import tests.test_followups as F
    import tests.test_orchestrator as O
    from proef.plan import PlanDecision
    ok = PlanDecision(approved=True, by="kim")
    cases = {
        "fixed, act, follow-ups": lambda p: F._run(p),
        "fixed, gate, plan review": lambda p: F._run(p, autonomy="gate", plan_approver=lambda plan, pid: ok),
        "model stops": lambda p: O._orch_run(p, [{"action": "stop"}]),
        "model follows up": lambda p: O._orch_run(p, [{"action": "follow_up"}, {"action": "next_round"}]),
        "model chooses badly": lambda p: O._orch_run(p, [{"action": "delete_everything"}]),
    }
    for name, make in cases.items():
        d = tmp_path / name.replace(" ", "_").replace(",", "")
        d.mkdir()
        out = make(d)
        report, store = out[0], (out[1] if hasattr(out[1], "branch_records") else None)
        if store is None:                       # _orch_run returns (report, recs, model): rebuild the store handle from its db
            from proef.adapters.store.sqlite import SqliteProvenanceStore
            store = SqliteProvenanceStore(str(d / "prov.db"))
        assert _clean(store, report) == [], (name, [str(x) for x in _clean(store, report)])


def test_audit_cli_reports_missing_branch_and_clean_campaign(tmp_path, capsys):
    from proef.cli import main
    db = str(tmp_path / "p.db")
    assert main(["audit", "nope", "--db", db]) == 1
    assert "no records" in capsys.readouterr().out
