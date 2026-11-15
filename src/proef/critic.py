"""A second agent that reads the plan before the person does.

The hypothesis lead proposes; the critic only criticises. It looks for what a careful colleague would catch in
review (near-duplicate hypotheses, a claim the gate's metric cannot test, a "refuted if" that cannot be observed,
an assay that does not match the claim) and returns short notes. It is advisory by construction: it cannot remove
a hypothesis, change a gate or approve anything, and a failed or malformed reply yields no notes rather than an
error. Run it on a DIFFERENT model than the proposer when you can (PROEF_LLM_CRITIC_MODEL): two instances of one
model share blind spots. The notes, and which model wrote them, are recorded in the ledger.
"""
from __future__ import annotations

import json
import re
from typing import Any

MAX_NOTES = 8

PROMPT = """You are the critic on a research team. A colleague proposed this plan; you do not run experiments and you
decide nothing. Find the weaknesses a careful reviewer would raise BEFORE any lab time is spent.

OBJECTIVE: {objective}
THE GATE (the only thing the experiments will measure): {gate}

PLAN
{plan}

Look for: near-duplicate hypotheses that waste runs; a claim the gate's metric cannot test (it would be judged on
the metric only); a "refuted if" that no experiment here could observe; an assay that does not match the claim;
a confidence that looks unearned; a missing or unverifiable citation relied upon.

Reply ONLY with JSON: {{"notes": [{{"target": "family id or candidate id, or 'plan'", "severity": "concern|note",
"text": "one or two sentences, specific"}}]}}. At most {max_notes} notes. If the plan is sound, return {{"notes": []}}."""


def _plan_text(plan: dict) -> str:
    lines = []
    for f in plan.get("families", []):
        lines.append(f"- family {f.get('id')} (confidence {f.get('confidence')}): {f.get('claim')}")
        lines.append(f"    mechanism: {f.get('mechanism')}; refuted if: {f.get('refuted_if')}; citation: {f.get('citation_status') or 'none'}")
        for v in f.get("variants", []):
            lines.append(f"    * {v.get('candidate')}: {v.get('statement')} [assay {v.get('assay')}]")
    return "\n".join(lines)[:6000]


def critique_plan(model: Any, plan: dict, objective: str, gate: str = "") -> list[dict]:
    """Advisory notes on a plan: [{target, severity, text}]. Never raises."""
    from proef.core.contracts import Envelope, ToolInvocation
    try:
        prompt = PROMPT.format(objective=objective, gate=gate or "the program's gate", plan=_plan_text(plan), max_notes=MAX_NOTES)
        inv = ToolInvocation(envelope=Envelope(experiment_branch_id="critic"), tool="c.fm.generate_molecules")
        raw = model.generate(inv, prompt).output["text"]
        m = re.search(r"\{.*\}", raw, re.S)
        notes = json.loads(m.group(0)).get("notes", []) if m else []
    except Exception:  # noqa: BLE001 - a critic that fails must never block the plan
        return []
    out = []
    for n in notes[:MAX_NOTES] if isinstance(notes, list) else []:
        if isinstance(n, dict) and str(n.get("text", "")).strip():
            out.append({"target": str(n.get("target") or "plan")[:60],
                        "severity": "concern" if n.get("severity") == "concern" else "note",
                        "text": str(n["text"]).strip()[:300]})
    return out
