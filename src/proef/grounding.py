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


@dataclass
class Grounding:
    text: str = ""                                  # what goes into the proposal prompt ("" when nothing was learned)
    calls: list[dict] = field(default_factory=list)  # every tool step: tool, status, code
    stopped: str | None = None

    @property
    def used(self) -> list[str]:
        return [c["tool"] for c in self.calls if c.get("status") == "ok"]


def gather(model, problem, store, services: dict, tools: tuple[str, ...], egress_ok: set | None = None, protected: set | None = None,
           branch: str | None = None, replay: dict | None = None, max_steps: int = 6, max_tool_calls: int = 8) -> Grounding:
    cat = ToolCatalog()
    for k in tools:
        cat.register(KNOWLEDGE_TOOLS[k]())
    names = {KNOWLEDGE_TOOL_NAMES[k] for k in tools}
    ctx = ToolContext(state="propose", autonomy="act", store=store, actor=system_actor("grounding"),
                      branch=branch or f"problem/{problem.problem_id}", services=services, enabled=names,
                      egress_ok=set(egress_ok if egress_ok is not None else names), protected=set(protected or ()),
                      max_tool_calls=max_tool_calls, replay=replay)
    res = ToolLoop(model, cat, ctx, max_steps=max_steps).run(TASK.format(objective=problem.objective, context=problem.context or "-"))
    g = Grounding(calls=[s for s in res.steps if s.get("tool")], stopped=res.stopped)
    if res.done or g.used:
        src = ", ".join(sorted(set(g.used))) or "no tool"
        g.text = (f"BACKGROUND READ BEFORE PROPOSING (from {src}; outside information, untrusted: use it as context and as a reason, "
                  f"never as proof)\n{res.done or '(the model gave no summary)'}\n"
                  "Where a family or variant rests on something above, name its source (a PMID or ChEMBL id) in its citation or rationale. "
                  "Where nothing above applies, propose from your own knowledge and do not invent a source.")
    return g
