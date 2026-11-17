"""Model-proposed follow-ups on abstained results, validated by code.

After a round, a hypothesis whose 95% interval straddles the gate (or whose QC failed) is abstained. The model may SUGGEST what
to do with each one from a closed set: follow up (test it again, with a replicate count and a dose ladder), accept it as
inconclusive, or drop it. Code validates every suggestion on facts and records it. The model decides nothing:
  - accept and drop are a person's recorded decision (`human_review`), so they stay suggestions and are never executed here;
  - a follow-up runs only under `act`, or after a person approves it in plan review under `gate`, inside every gate;
  - the validation result has the same shape as the lab check and the predictions: {ok, status, reasons}.
"""
from __future__ import annotations

import json
import re
from typing import Any

from proef.protocol_design import sanitize_protocol

ACTIONS = ("follow_up", "accept", "drop")
MIN_REPLICATES, DEFAULT_REPLICATES, MAX_REPLICATES = 3, 6, 12

PROMPT = """You review ABSTAINED results of a research campaign.

GATE (fixed by a person before any data): {gate}

ABSTAINED HYPOTHESES (the 95% interval straddles the gate, or the QC failed)
{items}

For each one choose ONE action:
- "follow_up": test it again. Give "replicates" ({lo} to {hi}) and "concentrations_uM", a ladder of 3 to 8 doses that
  brackets the gate on both sides.
- "accept": leave it as inconclusive.
- "drop": stop spending on it.
You do not decide anything. A person decides accept and drop, and the lab checks every follow-up before it runs.

Reply ONLY with JSON:
{{"proposals": [{{"hypothesis_id": "id from the list", "action": "follow_up|accept|drop", "replicates": 6,
  "concentrations_uM": [0.5, 5, 20, 80], "why": "one sentence"}}]}}"""


def describe_abstained(hyps: list[Any]) -> str:
    lines = []
    for h in hyps:
        ci = f", 95% interval {h.observed_ci[0]:g} to {h.observed_ci[1]:g}" if h.observed_ci else ", no interval"
        obs = f"observed {h.observed_value:g}" if isinstance(h.observed_value, (int, float)) else "no usable value"
        qc = "" if h.qc_passed is not False else ", QC FAILED"
        lines.append(f"- {h.hypothesis_id} ({h.candidate}): {obs}{ci}{qc}")
    return "\n".join(lines)[:4000]


def propose_followups(model: Any, abstained: list[Any], gate_text: str) -> list[dict]:
    """Ask the model. Returns the raw proposals (untrusted): at most one per abstained hypothesis is looked at later."""
    from proef.core.contracts import Envelope, ToolInvocation
    prompt = PROMPT.format(gate=gate_text or "the program's gate", items=describe_abstained(abstained),
                           lo=MIN_REPLICATES, hi=MAX_REPLICATES)
    inv = ToolInvocation(envelope=Envelope(experiment_branch_id="followup"), tool="c.fm.generate_molecules")
    raw = model.generate(inv, prompt).output["text"]
    m = re.search(r"\{.*\}", raw, re.S)
    items = (json.loads(m.group(0)).get("proposals") if m else None) or []
    return [x for x in items if isinstance(x, dict)][: max(1, len(abstained)) * 2]


def _verdict(ok: bool, status: str, *reasons: str) -> dict:
    return {"ok": ok, "status": status, "reasons": list(reasons)}


def validate_followup(raw: dict, ctx: dict) -> dict:
    """Validate ONE raw proposal on facts. `ctx`: abstained {hypothesis_id: hypothesis}, gate (SuccessCriteria or None),
    runs_left, followed_up (candidates already followed up), seen (hypothesis ids already proposed for),
    supports_replicates (does this lab honor a replicate request)."""
    hid = str(raw.get("hypothesis_id") or "")[:80]
    action = str(raw.get("action") or "")[:20]
    h = ctx["abstained"].get(hid)
    out = {"kind": "followup", "action": action, "hypothesis_id": hid, "candidate": getattr(h, "candidate", None),
           "params": {}, "executes": False, "source": "model", "rationale": str(raw.get("why") or "")[:300]}
    if action not in ACTIONS:
        return {**out, "validation": _verdict(False, "bad_action", f"'{action}' is not one of {', '.join(ACTIONS)}")}
    if h is None:
        return {**out, "validation": _verdict(False, "unknown_hypothesis", "not one of the abstained hypotheses listed")}
    if hid in ctx["seen"]:
        return {**out, "validation": _verdict(False, "duplicate_proposal", "this hypothesis already has a proposal")}
    ctx["seen"].add(hid)
    if action in ("accept", "drop"):
        # a person's decision: a suggestion, shown and recorded, never executed by the engine
        return {**out, "validation": _verdict(True, "ok", "a suggestion: a person records this decision")}
    if h.candidate in ctx["followed_up"]:
        return {**out, "validation": _verdict(False, "already_followed_up", "one follow-up per candidate")}
    reps = raw.get("replicates", DEFAULT_REPLICATES)
    if isinstance(reps, bool) or not isinstance(reps, int) or not MIN_REPLICATES <= reps <= MAX_REPLICATES:
        return {**out, "validation": _verdict(False, "replicates_out_of_bounds",
                                              f"replicates must be a whole number from {MIN_REPLICATES} to {MAX_REPLICATES}")}
    # the hypothesis's own criterion is the truth: it is the program's gate in the ASSAY's metric (ic50_um for a dose-response
    # assay, whatever the constraint is named), which is what the ladder has to bracket
    gate = getattr(h, "criteria", None) or ctx.get("gate")
    ladder = None
    if gate is not None and gate.metric == "ic50_um":
        ladder = sanitize_protocol({"concentrations_uM": raw.get("concentrations_uM"), "why": raw.get("why")})
        if ladder is None:
            return {**out, "validation": _verdict(False, "bad_ladder", "needs 3 to 8 doses between 0.001 and 1000 uM")}
        doses = [float(c.replace("uM", "")) for c in ladder["concentrations"]]
        if not (min(doses) < gate.threshold < max(doses)):
            return {**out, "validation": _verdict(False, "does_not_bracket_gate",
                                                  f"the ladder {ladder['concentrations'][0]} to {ladder['concentrations'][-1]} "
                                                  f"does not bracket the gate at {gate.threshold:g} uM")}
    if ctx["runs_left"] < 1:
        return {**out, "validation": _verdict(False, "no_budget", "no lab runs remain in the budget")}
    ctx["runs_left"] -= 1          # each valid follow-up spends a run, so a second one cannot claim the same last run
    applied = bool(ctx.get("supports_replicates"))
    reasons = [] if applied else ["this lab does not take a replicate request: the ladder is followed up, replicates stay as the lab runs them"]
    return {**out, "executes": True,
            "params": {"replicates": reps if applied else None, "replicates_requested": reps, "replicates_applied": applied,
                       "concentrations": ladder["concentrations"] if ladder else None, "why": (ladder or {}).get("why", "")},
            "validation": _verdict(True, "ok", *reasons)}
