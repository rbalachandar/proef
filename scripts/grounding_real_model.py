"""Real-model measurement (5I): which knowledge tools does the model call at `propose`, and do its proposals change with grounding?

Runs the same campaign twice against the SIMULATED lab, grounding off and on, with the model from the environment
(PROEF_LLM_PRESET / PROEF_LLM_MODEL). The knowledge tools hit the public PubMed, ChEMBL and Open Targets APIs. A model-call cap bounds cost.
    python scripts/grounding_real_model.py [--tools pubmed,chembl,opentargets] [--max-model-calls 30]
"""
from __future__ import annotations

import argparse
import json
import re
import tempfile
from collections import Counter
from pathlib import Path

from proef.adapters.databases.chembl_lookup import ChemblLookup
from proef.adapters.databases.open_targets import OpenTargetsAdapter
from proef.adapters.databases.pubmed import PubMedAdapter
from proef.adapters.lab.simulator import SimulatorLab
from proef.adapters.models.from_env import model_from_env
from proef.adapters.store.sqlite import SqliteProvenanceStore
from proef.audit import audit_campaign
from proef.autoloop import CampaignConfig, ResourceBudget, run_campaign
from proef.core.hypothesis import Constraint, ProblemStatement
from proef.flywheel import Flywheel
from proef.plan import PlanDecision

class Spy:
    """Wraps the real model and keeps every prompt and answer, so use of the tool results can be scored by code."""
    def __init__(self, m):
        self.m, self.log = m, []

    def __getattr__(self, k):
        return getattr(self.m, k)

    def generate(self, inv, prompt, *a, **kw):
        r = self.m.generate(inv, prompt, *a, **kw)
        self.log.append((prompt, r.output["text"]))
        return r


def _json(t: str) -> dict:
    m = re.search(r"\{.*\}", t, re.S)
    try:
        return json.loads(m.group(0)) if m else {}
    except ValueError:
        return {}


def use_metrics(log: list) -> dict:
    loop = [(p, t) for p, t in log if "TOOLS OFFERED IN THIS STEP" in p]
    prop = [(p, t) for p, t in log if "TOOLS OFFERED IN THIS STEP" not in p and "hypothesis lead" in p]
    if not loop or not prop:
        return {}
    seen = loop[-1][0].split("SO FAR")[-1]
    done = _json(loop[-1][1]).get("done", "")
    ids = set(re.findall(r"CHEMBL\d+|ENSG\d+|\bPMID\s*\d+", done))
    traced = [i for i in ids if re.sub(r"PMID\s*", "", i) in seen]
    fams = _json(prop[0][1]).get("families", [])
    cites = [re.sub(r"PMID\s*", "", str(f.get("citation") or "")) for f in fams if f.get("citation")]
    cands = [v.get("candidate", "") for f in fams for v in f.get("variants", [])]
    from_tools = [c for c in cands if c and c.lower() in seen.lower()]
    return {"summary_ids": len(ids), "summary_ids_traced": len(traced), "families": len(fams), "families_citing": len(cites),
            "citations_in_tool_results": sum(1 for c in cites if c and c in seen), "variants": len(cands),
            "candidates_named_in_tool_results": len(from_tools), "stopped_by_cap": "step_cap" in done}


SERVICES = {"pubmed": PubMedAdapter, "chembl": ChemblLookup, "opentargets": OpenTargetsAdapter}


OUT = Path("/tmp/grounding_compare")
OBJECTIVE = {"objective": "Find a confirmed sub-20uM EGFR binder", "context": "No candidate list: the model proposes mechanistic families and variants"}


def _role(prompt: str) -> str:
    for marker, role in (("You steer a research campaign", "orchestrator"), ("ABSTAINED HYPOTHESES", "followup"), ("hypothesis lead", "propose_or_replan"), ("RESULTS SO FAR (re-plan", "replan")):
        if marker in prompt:
            return role
    return "other"


def _persist(label: str, spy: "Spy", dec: list, recs=None) -> None:
    """Everything a later comparison could need, raw, so analysis never requires another model run."""
    OUT.mkdir(parents=True, exist_ok=True)
    loop = [(p, t) for p, t in spy.log if "TOOLS OFFERED IN THIS STEP" in p]
    prop = [(p, t) for p, t in spy.log if "TOOLS OFFERED IN THIS STEP" not in p and "hypothesis lead" in p]
    n = len(list(OUT.glob(f"{label}-*.json")))
    (OUT / f"{label}-{n}.json").write_text(json.dumps({
        "arm": label,
        "grounding_summary": _json(loop[-1][1]).get("done") if loop else None,
        "tool_results_seen": loop[-1][0].split("SO FAR")[-1] if loop else None,
        "tool_calls": [d for d in dec if d.get("kind") == "tool_call"],
        "sealed_tool_results": [d for d in dec if d.get("kind") == "tool_result"],     # full, from the ledger (not the truncated prompt)
        "proposal_prompt": prop[0][0] if prop else None,
        "proposal_response": prop[0][1] if prop else None,
        "all_proposal_responses": [t for _, t in prop],
        "role_prompts": [{"role": _role(p), "has_background": "BACKGROUND READ" in p, "after_round": "BACKGROUND READ AFTER ROUND" in p}
                         for p, _ in spy.log if "TOOLS OFFERED IN THIS STEP" not in p],
        "orchestrator_choices": [{k: d.get(k) for k in ("round", "chosen", "source", "valid", "why", "allowed")} for d in dec if d.get("kind") == "orchestrator_choice"],
        "decide_loop_prompts": sum(1 for p, _ in spy.log if "one step (decide)" in p),
        "model_calls": sum(1 for d in dec if d.get("kind") == "model_call"),
        "audit_violations": [str(v) for v in audit_campaign(recs)] if recs is not None else None,
    }, indent=1, default=str))


def arm(tools: tuple[str, ...], max_calls: int, tmp: Path, label: str, **cfg_extra) -> dict:
    store = SqliteProvenanceStore(str(tmp / f"{label}.db"))
    prob = ProblemStatement(objective=OBJECTIVE["objective"], context=OBJECTIVE["context"],
                            constraints=[Constraint(name="potency", description="IC50 <= 20 uM", kind="hard", limit=20.0, unit="uM")],
                            success_metric="orthogonal confirmation")
    spy = Spy(model_from_env())
    kw = dict(problem=prob, hypothesis_source="agent", model=spy, autonomy="act", k=2, rounds=1, seed=1,
              inbox=str(tmp / f"{label}-in"), poll_secs=0.05, flywheel=Flywheel(":memory:"),
              budget=ResourceBudget(max_model_calls=max_calls), grounding_tools=tools,
              knowledge_services={k: SERVICES[k]() for k in tools},
              plan_approver=lambda plan, pid: PlanDecision(approved=True, by="script"))
    kw.update(cfg_extra)
    cfg = CampaignConfig(**kw)
    log: list[str] = []
    rep = run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=log.append)
    recs = store.branch_records(f"problem/{rep.problem_id}")
    dec = [r.decision or {} for r in recs]
    calls = [d for d in dec if d.get("kind") == "tool_call"]
    props = [r.decision.get("statement") or r.decision.get("candidate") for r in recs if r.event_type.value == "hypothesis_proposed" and r.decision]
    _persist(label, spy, dec, recs)
    return {"arm": label, "tools": list(tools), "model_calls": sum(1 for d in dec if d.get("kind") == "model_call"),
            "tool_calls": Counter(f'{d["tool"]}:{d["status"]}' + (f'/{d["code"]}' if d.get("code") else "") for d in calls),
            "sealed_results": sum(1 for d in dec if d.get("kind") == "tool_result"), "proposals": props,
            "audit_violations": [str(v) for v in audit_campaign(recs)], "use": use_metrics(spy.log), "grounding_log": [m for m in log if "grounding" in m]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--tools", default="pubmed,chembl,opentargets")
    ap.add_argument("--max-model-calls", type=int, default=30)
    ap.add_argument("--grounded-only", action="store_true")
    ap.add_argument("--repeats", type=int, default=1)
    ap.add_argument("--objective", default=None)
    ap.add_argument("--context", default=None)
    ap.add_argument("--decide", action="store_true", help="2 rounds, a model orchestrator, follow-ups and re-plan, so the decide loop can fire")
    ap.add_argument("--out", default=None, help="directory for the raw per-run files")
    a = ap.parse_args()
    tools = tuple(t for t in a.tools.split(",") if t)
    global OUT
    if a.objective:
        OBJECTIVE["objective"], OBJECTIVE["context"] = a.objective, a.context or OBJECTIVE["context"]
    if a.out:
        OUT = Path(a.out)
    runs = []
    with tempfile.TemporaryDirectory() as d:
        for i in range(a.repeats):
            for label, t in ([] if a.grounded_only else [("ungrounded", ())]) + [("grounded", tools)]:
                r = arm(t, a.max_model_calls, Path(d), f"{label}-{i}", **({"rounds": 2, "orchestrator": "model", "followups": True, "replan": True} if a.decide else {}))
                r["arm"] = label
                r["proposals"] = len(r["proposals"])
                runs.append(r)
                print(json.dumps(r, default=str), flush=True)
    for label in ("ungrounded", "grounded"):
        rs = [r for r in runs if r["arm"] == label]
        if rs:
            print(f"\n{label}: runs={len(rs)} model_calls={[r['model_calls'] for r in rs]} violations={sum(len(r['audit_violations']) for r in rs)}"
                  f" tools={dict(sum((Counter(r['tool_calls']) for r in rs), Counter()))}")
            u = [r["use"] for r in rs if r["use"]]
            for k in ("summary_ids", "summary_ids_traced", "families", "families_citing", "citations_in_tool_results", "variants",
                      "candidates_named_in_tool_results"):
                print(f"   {k}: {[x.get(k) for x in u]}")


if __name__ == "__main__":
    main()
