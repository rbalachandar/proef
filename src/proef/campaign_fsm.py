"""The decision point after a round, as an explicit state with the actions it allows, and who chooses among them.

After a round's results are in, a campaign can follow up abstentions, re-plan, go on to the next round, or stop. That used to be two
`if cfg.flag` blocks in a fixed order, so nothing else could choose. Now the allowed actions are COMPUTED from preconditions in
code (what the operator permits, the budget, what already happened this round), and an orchestrator chooses among them:
  - the fixed orchestrator reproduces the old order exactly;
  - the model orchestrator asks a model. It can only pick an allowed action. Anything else, a refused call or an unparseable
    answer falls back to the fixed choice, and the fallback is recorded.
Every choice is a ledger record. The operator's `followups` and `replan` flags are what the model MAY choose, never what it must.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol


class State(str, Enum):
    ROUND_JUDGED = "round_judged"          # a round's results are in: what next?


class Action(str, Enum):
    FOLLOW_UP = "follow_up"
    REPLAN = "replan"
    NEXT_ROUND = "next_round"
    STOP = "stop"


ACTION_HELP = {
    Action.FOLLOW_UP: "ask what to do with the abstained results (a validated follow-up may then run)",
    Action.REPLAN: "propose new hypotheses from the results so far (a person approves them under approvals)",
    Action.NEXT_ROUND: "go on to the next round with what is queued",
    Action.STOP: "end the campaign here and keep what has been learned",
}


@dataclass
class View:
    """The facts an orchestrator may use. Plain data: no handles to a lab, a store or a tracker."""
    round_no: int
    rounds: int
    verdicts: dict[str, str]
    abstained: list[str]
    queued: int
    runs_left: int
    done: set[str] = field(default_factory=set)


@dataclass
class Choice:
    action: str
    why: str = ""
    source: str = "fixed"                  # fixed | model | fixed-fallback
    valid: bool = True
    reasons: list[str] = field(default_factory=list)


def allowed_actions(state: State, *, view: View, followups_permitted: bool, replan_permitted: bool, has_model: bool,
                    agent_source: bool) -> list[str]:
    """What this state allows, from preconditions. next_round is always allowed, and so is stop while a round remains."""
    if state is not State.ROUND_JUDGED:
        return []
    out: list[str] = []
    if followups_permitted and has_model and view.abstained and Action.FOLLOW_UP.value not in view.done:
        out.append(Action.FOLLOW_UP.value)
    if replan_permitted and agent_source and view.round_no < max(1, view.rounds) and Action.REPLAN.value not in view.done:
        out.append(Action.REPLAN.value)
    out.append(Action.NEXT_ROUND.value)
    if view.round_no < max(1, view.rounds):
        out.append(Action.STOP.value)         # on the last round there is nothing left to end
    return out


def _help(action: str, view: View) -> str:
    if action == Action.FOLLOW_UP.value and view.round_no >= max(1, view.rounds):
        return "record a suggestion for a person about the abstained results (this is the last round, so nothing can run)"
    return ACTION_HELP[Action(action)]


class Orchestrator(Protocol):
    def choose(self, state: State, allowed: list[str], view: View) -> Choice: ...


class FixedOrchestrator:
    """The order the engine always had: follow up, then re-plan, then the next round. It never stops a campaign early."""

    def choose(self, state: State, allowed: list[str], view: View) -> Choice:
        for a in (Action.FOLLOW_UP.value, Action.REPLAN.value):
            if a in allowed:
                return Choice(a, "the configured order", "fixed")
        return Choice(Action.NEXT_ROUND.value, "nothing else is allowed or configured", "fixed")


PROMPT = """You steer a research campaign between rounds. A round just finished.

ROUND {round_no} OF {rounds}
RESULTS SO FAR (candidate: verdict)
{verdicts}
ABSTAINED: {abstained}
QUEUED FOR THE NEXT ROUND: {queued} hypotheses. LAB RUNS LEFT IN THE BUDGET: {runs_left}.
ALREADY DONE THIS ROUND: {done}

YOU MAY CHOOSE ONE OF THESE ACTIONS (nothing else is allowed):
{actions}

Choose the single most useful action now. You will be asked again after it, until you choose next_round or stop.
Reply ONLY with JSON: {{"action": "one of the actions above", "why": "one sentence"}}"""


class ModelOrchestrator:
    """Asks a model which allowed action to take. The model decides nothing outside the list, and a failure is not an error: the
    fixed orchestrator answers instead and the choice says so."""

    RESERVE = 4        # model calls kept for the follow-up, re-plan and review work that steering must not starve

    def __init__(self, model: Any, fallback: Orchestrator | None = None, reserve: int | None = None):
        self.model, self.fallback = model, fallback or FixedOrchestrator()
        self.reserve = self.RESERVE if reserve is None else reserve

    def choose(self, state: State, allowed: list[str], view: View) -> Choice:
        from proef.core.contracts import Envelope, ToolInvocation
        prompt = PROMPT.format(
            round_no=view.round_no, rounds=view.rounds,
            verdicts="\n".join(f"- {c}: {v}" for c, v in sorted(view.verdicts.items()))[:3000] or "- nothing judged yet",
            abstained=", ".join(view.abstained) or "none", queued=view.queued, runs_left=view.runs_left,
            done=", ".join(sorted(view.done)) or "nothing",
            actions="\n".join(f"- {a}: {_help(a, view)}" for a in allowed))
        fb = self.fallback.choose(state, allowed, view)
        left = getattr(self.model, "remaining", None)
        if callable(left) and left() <= self.reserve:
            # steering must not spend the calls the real work needs: with only the reserve left, the fixed order answers
            return Choice(fb.action, fb.why, "fixed-fallback", False,
                          [f"{left()} model calls left, kept for follow-ups, re-plans and the review"])
        try:
            raw = self.model.generate(ToolInvocation(envelope=Envelope(experiment_branch_id="orchestrator"),
                                                     tool="c.fm.generate_molecules"), prompt).output["text"]
            m = re.search(r"\{.*\}", raw, re.S)
            data = json.loads(m.group(0)) if m else {}
        except Exception as exc:  # noqa: BLE001 - a refused, failed or unparseable call is not a campaign failure
            return Choice(fb.action, fb.why, "fixed-fallback", False, [f"the model's answer could not be used ({type(exc).__name__})"])
        action = str(data.get("action") or "")[:30]
        why = str(data.get("why") or "")[:300]
        if action not in allowed:
            return Choice(fb.action, fb.why, "fixed-fallback", False,
                          [f"'{action}' is not one of the allowed actions ({', '.join(allowed)})"])
        return Choice(action, why, "model", True, [])
