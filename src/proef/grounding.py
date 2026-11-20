"""Grounding before a proposal (5H, `propose`): a model reads PubMed, ChEMBL and Open Targets through the tool loop, then proposes.

The loop is state-scoped to `propose` and offered ONLY the knowledge tools the operator enabled: no flow tool, no lab door. Every query is
egress-checked and every result is delimited, sealed and on the ledger. What comes back is context for the proposal prompt and nothing
more: it is labelled untrusted, and it never becomes a verdict, an approval or a criterion (only a measurement does).
"""
from __future__ import annotations

from dataclasses import dataclass, field

from proef.core.interfaces import system_actor
from proef.tools import KNOWLEDGE_TOOL_NAMES, KNOWLEDGE_TOOLS, ToolCatalog, ToolContext
from proef.toolloop import ToolLoop

TASK = ("Before hypotheses are proposed for this objective, look up what is already known. Use the tools offered to find relevant "
        "literature, known target biology and published activities. Query by general scientific terms (target, mechanism, disease), "
        "never by this campaign's own candidate ids. When you have enough, reply done with a short summary of what you found and "
        "which tool it came from.\n\nOBJECTIVE: {objective}\nCONTEXT: {context}")


DECIDE_TASK = ("A round of experiments has just been judged. Look up published information that could explain these results or "
               "suggest what to test next: known behaviour of the compound classes, assay interference, reported potency of related "
               "compounds. Query by general scientific terms (target, mechanism, assay, compound class). Never put a measured value or "
               "any of this campaign's own identifiers in a query. When you have enough, reply done with a short summary of what you "
               "found and which tool it came from.\n\nOBJECTIVE: {objective}\n\nRESULTS OF THIS ROUND\n{results}")


def value_strings(values) -> set[str]:
    """The ways a measured number could be written in a query, so egress checking can refuse it (three characters or more)."""
    out: set[str] = set()
    for v in values:
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            out |= {f"{v:g}", f"{v:.3g}", f"{v:.2f}", f"{v:.1f}"}
    return {x for x in out if len(x) >= 3}


@dataclass
class Grounding:
    text: str = ""                                  # what goes into the proposal prompt ("" when nothing was learned)
    calls: list[dict] = field(default_factory=list)  # every tool step: tool, status, code
    stopped: str | None = None

    @property
    def used(self) -> list[str]:
        return [c["tool"] for c in self.calls if c.get("status") == "ok"]


def gather(model, problem, store, services: dict, tools: tuple[str, ...], egress_ok: set | None = None, protected: set | None = None,
           branch: str | None = None, replay: dict | None = None, max_steps: int = 6, max_tool_calls: int = 8,
           state: str = "propose", round_no: int | None = None, results: str = "") -> Grounding:
    cat = ToolCatalog()
    for k in tools:
        cat.register(KNOWLEDGE_TOOLS[k]())
    names = {KNOWLEDGE_TOOL_NAMES[k] for k in tools}
    ctx = ToolContext(state=state, autonomy="act", scope=(f"{state}-{round_no}" if state != "propose" else ""), store=store, actor=system_actor("grounding"),
                      branch=branch or f"problem/{problem.problem_id}", services=services, enabled=names,
                      egress_ok=set(egress_ok if egress_ok is not None else names), protected=set(protected or ()),
                      max_tool_calls=max_tool_calls, replay=replay)
    task = (DECIDE_TASK.format(objective=problem.objective, results=results[:3000] or "(none)") if state == "decide"
            else TASK.format(objective=problem.objective, context=problem.context or "-"))
    res = ToolLoop(model, cat, ctx, max_steps=max_steps).run(task)
    g = Grounding(calls=[s for s in res.steps if s.get("tool")], stopped=res.stopped)
    if res.done or g.used:
        src = ", ".join(sorted(set(g.used))) or "no tool"
        when = "BEFORE PROPOSING" if state == "propose" else f"AFTER ROUND {round_no}"
        g.text = (f"BACKGROUND READ {when} (from {src}; outside information, untrusted: use it as context and as a reason, "
                  f"never as proof)\n{res.done or '(the model gave no summary)'}\n"
                  "Where a family, variant or suggestion rests on something above, name its source (a PMID or ChEMBL id) in its citation or rationale. "
                  "Where nothing above applies, propose from your own knowledge and do not invent a source.")
    return g


# ---- the `ground` step: evidence for and against each family, checked against what was retrieved ----------------------------------------

GROUND_TASK = ("A research plan has proposed this family of hypotheses. Before a person reviews it, use the tools to look for: evidence that "
               "SUPPORTS it, evidence that CONTRADICTS it, PRIOR MEASUREMENTS of its compounds, and whether the idea is ALREADY KNOWN.\n\n"
               "FAMILY: {family}\nCLAIM: {claim}\nMECHANISM: {mechanism}\nCOMPOUNDS: {compounds}\n\n"
               "When you have looked, reply done with ONE JSON object (not a string):\n"
               '  {{"items": [{{"kind": "supports|contradicts|prior_measurement|already_known", "claim": "one sentence", '
               '"source": "the PMID, ChEMBL id or Ensembl id of the tool result it comes from"}}]}}\n'
               "Include only what you can point to in a tool result you received in this step. An item with no such source is thrown away. "
               "If you found nothing, return an empty list: that is a useful answer. Never put a measured value or this campaign's own "
               "identifiers in a query.")


def ground_families(cfg, portfolio, tracker, store, actor, say, label: str | None = None) -> dict[str, dict]:
    """One `propose`-style loop per family of the plan. Returns {family_id: {"items": kept, "dropped": n, "calls": n}}; every family's
    outcome is a ledger record. Only the knowledge tools are offered. Nothing here changes a hypothesis, a verdict or an approval."""
    from proef.core.contracts import Envelope, EventType
    from proef.core.interfaces import record_for
    from proef.groundedness import verify_items

    info = getattr(tracker, "family_info", {}) or {}
    pid = cfg.problem.problem_id or "unnamed"
    by_family: dict[str, list] = {}
    for h in portfolio:
        by_family.setdefault(h.family or "ungrouped", []).append(h)
    public = {n.lower() for n in getattr(cfg, "grounding_public_names", ())}
    protected = {str(c.get("id") or c.get("candidate") or "") for c in (cfg.candidates or []) if isinstance(c, dict)
                 if str(c.get("id") or c.get("candidate") or "").lower() not in public} | {pid}
    names = {KNOWLEDGE_TOOL_NAMES[k] for k in cfg.grounding_tools}
    out: dict[str, dict] = {}
    for fid, hyps in by_family.items():
        cat = ToolCatalog()
        for k in cfg.grounding_tools:
            cat.register(KNOWLEDGE_TOOLS[k]())
        ctx = ToolContext(state="propose", autonomy="act", store=store, actor=system_actor("grounding"), branch=f"problem/{pid}",
                          services=cfg.knowledge_services, enabled=names, egress_ok=set(names), protected=protected, max_tool_calls=8,
                          scope=f"ground-{label or 'initial'}-{fid}", replay=getattr(cfg, "grounding_replay", None))
        f = info.get(fid, {})
        task = GROUND_TASK.format(family=fid[:80], claim=str(f.get("parent_statement", ""))[:300], mechanism=str(f.get("mechanism", ""))[:300],
                                  compounds=", ".join(sorted({str(h.candidate) for h in hyps}))[:300])
        res = ToolLoop(cfg.model, cat, ctx, max_steps=6, done_chars=4000).run(task)
        parsed = {}
        if res.done:
            import json
            import re
            m = re.search(r"\{.*\}", res.done, re.S)
            try:
                parsed = json.loads(m.group(0)) if m else {}
            except ValueError:
                parsed = {}
        kept, dropped = verify_items(parsed.get("items") if isinstance(parsed, dict) else None, [r["body"] for r in res.results])
        out[fid] = {"items": kept, "dropped": len(dropped), "calls": len(res.results), "stopped": res.stopped}
        store.append(record_for(Envelope(experiment_branch_id=f"problem/{pid}"), EventType.gate_decision, actor, decision={
            "kind": "grounding", "advisory": True, "family": fid, "stage": label or "initial", "items": kept, "dropped": dropped[:8],
            "n_dropped": len(dropped), "tool_calls": len(res.results), "stopped": res.stopped}))
    say(f"grounding: {len(out)} family(ies), {sum(len(v['items']) for v in out.values())} item(s) traced, "
        f"{sum(v['dropped'] for v in out.values())} dropped as untraced")
    return out
