"""The research plan: what a model proposed, shown to a person BEFORE any lab submission.

In `gate` autonomy a campaign whose hypotheses came from a model pauses after proposing, shows the plan
(families, their mechanisms and refuted-if clauses, and every variant), and waits. The person can approve
it, decline it, or remove individual hypotheses. Whatever they remove is recorded with a reason and never
tested; the rounds then run on what remains.
"""
from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from proef.protocol_design import describe


@dataclass
class PlanDecision:
    approved: bool
    excluded: set[str] = field(default_factory=set)      # hypothesis ids the person removed
    by: str = "operator"
    note: str = ""


def already_tested(portfolio, tested) -> list[dict]:
    """Warnings for proposed hypotheses on a compound a previous round already measured. A re-plan can propose the same
    compound again under a new family. That is a new measurement of something with a verdict, and the person should see it."""
    prior = {}
    for t in tested or []:
        prior.setdefault(t.candidate, t)
    out: dict[str, dict] = {}
    for h in portfolio:
        t = prior.get(h.candidate)
        if t is None:
            continue
        e = out.setdefault(h.candidate, {"kind": "already_tested", "candidate": h.candidate, "families": [],
                                         "hypothesis_ids": [], "verdict": t.status, "prior_family": t.family or ""})
        e["families"].append(h.family or "ungrouped")
        e["hypothesis_ids"].append(h.hypothesis_id)
    return list(out.values())


def build_plan(portfolio, family_info: dict | None, family_scores: dict | None, tested=None) -> dict:
    """A JSON-safe plan from the proposed variants. Pure: no I/O. `tested`: hypotheses earlier rounds already judged."""
    family_info, family_scores = family_info or {}, family_scores or {}
    fams: dict[str, dict] = {}
    for h in portfolio:
        fid = h.family or "ungrouped"
        info = family_info.get(fid, {})
        f = fams.setdefault(fid, {
            "id": fid, "claim": info.get("parent_statement", ""),
            "mechanism": info.get("mechanism") or h.mechanism or "",
            "refuted_if": info.get("refutation_condition") or h.refutation_condition or "",
            "confidence": family_scores.get(fid),
            "citation": h.citation or info.get("citation") or "",
            "citation_status": h.citation_status if (h.citation or info.get("citation")) else "",
            "variants": []})
        f["variants"].append({
            "hypothesis_id": h.hypothesis_id, "candidate": h.candidate, "statement": h.statement,
            "predicted": h.predicted, "rationale": h.rationale, "assay": h.assay_type,
            "protocol": describe((h.metadata or {}).get("protocol")),
            "lab": (h.metadata or {}).get("lab_check")})
    return {"families": list(fams.values()), "n_hypotheses": len(portfolio), "warnings": duplicate_compounds(portfolio) + already_tested(portfolio, tested)}


def duplicate_compounds(portfolio) -> list[dict]:
    """Compounds that appear under more than one family. Each such hypothesis would be tested on its own, so the same
    compound can be measured twice (a wasted run), and the gate metric cannot tell the two mechanisms apart. Deterministic,
    so a person sees it before approving, whether or not a critic model happens to notice."""
    by: dict[str, dict] = {}
    for h in portfolio:
        key = (h.candidate or "").strip().lower()
        if not key:
            continue
        e = by.setdefault(key, {"candidate": h.candidate, "families": [], "hypothesis_ids": []})
        if (h.family or "ungrouped") not in e["families"]:
            e["families"].append(h.family or "ungrouped")
        e["hypothesis_ids"].append(h.hypothesis_id)
    return [{"kind": "duplicate_compound", **e} for e in by.values() if len(e["hypothesis_ids"]) > 1]


def web_plan_approver(inbox, poll_secs: float = 2.0, timeout_secs: float = 24 * 3600,
                      notify: Callable[[str], None] | None = None) -> Callable[[dict, str], PlanDecision]:
    """Request the plan into the approval inbox and wait for the person's decision. Denies on timeout."""
    from proef.approvals import ApprovalInbox
    box = inbox if isinstance(inbox, ApprovalInbox) else ApprovalInbox(str(Path(inbox)))
    say = notify or (lambda m: None)

    def approve(plan: dict, problem_id: str) -> PlanDecision:
        n, m = plan["n_hypotheses"], len(plan["families"])
        stage = f" ({plan['label']})" if plan.get("label") else ""   # "after round 1" for a re-plan
        title = (f"[problem/{problem_id}] Approve the research plan{stage}: {n} hypothes{'is' if n == 1 else 'es'} "
                 f"in {m} famil{'y' if m == 1 else 'ies'}")
        aid = box.request(title, detail="Review what the model proposed. Remove any hypothesis that should not be tested.",
                          payload=plan)
        say(f"plan approval requested ({aid}): {n} hypothes{'is' if n == 1 else 'es'} in {m} famil{'y' if m == 1 else 'ies'}{stage}: pending in the workbench")
        deadline = time.time() + timeout_secs
        while time.time() < deadline:
            rec = box.state().get(aid) or {}
            if rec.get("status") in ("approved", "denied"):
                data = rec.get("data") or {}
                return PlanDecision(approved=rec["status"] == "approved", excluded=set(data.get("exclude") or []),
                                    by=rec.get("decided_by", "operator"), note=str(data.get("note") or ""))
            time.sleep(poll_secs)
        say("plan approval timed out")
        return PlanDecision(approved=False, note="timed out with no decision")

    return approve
