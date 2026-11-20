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
