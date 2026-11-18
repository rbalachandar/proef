"""Agent-driven hypothesis generation — the agent decides the structure.

Given a ProblemStatement (and optionally a candidate library), a ModelProvider
proposes the hypothesis structure itself:

  - how many mechanistic FAMILIES (distinct bets) and what they claim
  - how many VARIANTS per family (parameter sweeps) and WHICH variables to
    tune (warhead type, concentration range, assay modality...)
  - a self-assessed score per family (used by the portfolio filter unless the
    operator overrides it)

Everything the agent produces is JSON-validated, provenance-recorded (prompt +
response + structure), and constrained by operator limits (max_families,
max_variants_per_family) — autonomy inside guardrails, as everywhere else.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable

from proef.core.contracts import ToolInvocation
from proef.core.hypothesis import (
    Constraint, Hypothesis, HypothesisTracker, ProblemStatement, SuccessCriteria,
)
from proef.core.interfaces import record_for
from proef.protocol_design import sanitize_protocol

PROMPT = """You are the hypothesis lead of a {role}.

PROBLEM
objective: {objective}
context: {context}
constraints: {constraints}
success metric: {success_metric}

CANDIDATE LIBRARY (measured/predicted so far)
{candidates}

Decide the hypothesis structure yourself:
- How many DISTINCT mechanistic families to pursue (each family = one
  falsifiable mechanistic claim; variants = the same claim with different
  parameterizations, e.g. analogs or tuned assay variables).
- For each variant: which variables you tune and why (declare them in
  "variables"), what it predicts, and its rationale.
- Your own confidence score per family (0-1) — be honest.

- "protocol" is optional: the dose ladder (3 to 8 concentrations in uM, 0.001 to 1000) YOU would test this
  variant at. Bracket the gate with doses on both sides of it. The lab validates it and may replace it.

Limits: at most {max_families} families, at most {max_variants_per_family}
variants per family. Prefer few distinct ideas over many similar ones.

Reply ONLY with JSON:
{{"families": [
  {{"family": "short-family-id",
    "parent_statement": "falsifiable mechanistic claim",
    "kind": "mechanistic",
    "mechanism": "the CAUSAL story — {mechanism_hint}; not 'it fits'",
    "refutation_condition": "the ONE observation that kills this family",
    "predicted_observables": ["what else must be true if this holds"],
    "citation": "PMID or DOI grounding the rationale ('' if none — never invent)",
    "rationale": "why this bet",
    "confidence": 0.0-1.0,
    "variables": ["what you tune across variants"],
    "variants": [
      {{"statement": "falsifiable variant prediction",
        "candidate": "the {candidate_kind} or sample name only, e.g. {candidate_example}, with no prefix such as 'proposed'",
        "assay_type": "binding",{instrument_field}{tools_field}
        "predicted": "human-readable prediction",
        "predicted_value": 20.0,
        "rationale": "why this variant",
        "variables": {{"tuned_param": "value"}},
        "protocol": {{"concentrations_uM": [0.1, 0.5, 2, 10, 50], "why": "brackets the gate on both sides"}}}}
    ]}}
]}}"""


from proef.core.contracts.models import coerce_assay_type


def propose_families_with_agent(
    tracker: HypothesisTracker,
    problem: ProblemStatement,
    model: Any,
    candidates: list[dict[str, Any]] | None = None,
    max_families: int = 3,
    max_variants_per_family: int = 3,
    max_total_hypotheses: int | None = None,
    existing_keys: set[tuple[str, str]] | None = None,
    skill_guidance: str | None = None,
    flywheel: Any = None,
    branch_id: str | None = None,
    score_fn: Callable[[dict[str, Any]], float] | None = None,
    notify: Callable[[str], None] | None = None,
    findings: str | None = None,
    modality: str | None = None,
    instruments: dict | None = None,
    predictors: list[str] | None = None,
    registry: Any = None,
) -> tuple[list[Hypothesis], dict[str, float]]:
    """max_total_hypotheses: hard cap on hypotheses this generation may add.
    existing_keys: (candidate, statement) pairs already tried — duplicates the
    agent re-proposes are dropped (no re-running the same experiment).
    skill_guidance: Agent Skills text (e.g. the proef/opentrons skill)
    injected into the prompt so domain procedure shapes the generation."""

    """The agent designs the hypothesis portfolio; returns (variants, scores).

    Enforces operator limits post-hoc (truncates), records the raw generation
    into provenance, and derives family scores from the agent's confidence
    unless `score_fn` is given.
    """
    from proef.core.contracts import Envelope, EventType

    branch = branch_id or f"problem/{problem.problem_id}"
    env = Envelope(experiment_branch_id=branch)
    actor = tracker.actor
    say = notify or (lambda m: None)

    if skill_guidance:
        guidance_block = ("PROCEDURAL GUIDANCE (Agent Skills — follow while "
                          "designing):\n" + skill_guidance[:3000])
    else:
        guidance_block = ""
    from proef.modality import pack_or_default
    pack = pack_or_default(modality)
    menu = (instruments or {}).get("menu") or []
    runs_on = (instruments or {}).get("runs_on")
    if menu:
        from proef.labselect import menu_text
        instrument_block = ("\n\nAVAILABLE INSTRUMENTS (the operator's fence: choose only from these; real and simulated "
                            "are labelled)\n" + menu_text(menu) + "\nFor each variant name the instrument that would run it "
                            "in \"instrument\", or leave it empty if none fits. The lab checks your pick against what each "
                            "instrument declares, and a person sees the result.")
        instrument_field = '\n        "instrument": "name from the list above, or empty",'
    else:
        instrument_block, instrument_field = "", ""
    # prediction tools: the operator enables them, the model proposes which fit, code validates and runs them
    from proef.predictors import DEFAULT as _DEFAULT_REG
    _REG = registry if registry is not None else _DEFAULT_REG     # a replay passes one that plays back what was recorded
    tool_menu = _REG.menu(predictors, modality)
    if tool_menu:
        tools_block = ("\n\nPREDICTION TOOLS (priors the lab computes; never evidence: the measured result judges)\n"
                       + _REG.menu_text(tool_menu) + "\nFor each variant you may list the tools that fit in \"tools\" and give "
                       "the candidate's \"smiles\" when it is a small molecule. Leave both empty if unsure: never guess a SMILES.")
        tools_field = '\n        "tools": ["name from the list above"], "smiles": "SMILES or empty",'
    else:
        tools_block, tools_field = "", ""
    prompt = PROMPT.format(
        instrument_field=instrument_field, tools_field=tools_field,
        role=pack.prompt_role, mechanism_hint=pack.mechanism_hint,
        candidate_kind=pack.candidate_kind, candidate_example=pack.candidate_example,
        objective=problem.objective,
        context=problem.context or "-",
        constraints=json.dumps([c.model_dump() for c in problem.constraints]),
        success_metric=problem.success_metric or "-",
        candidates=json.dumps(candidates or [], indent=1)[:4000],
        max_families=max_families,
        max_variants_per_family=max_variants_per_family,
    ) + instrument_block + tools_block + ("\n\n" + guidance_block if guidance_block else "")
    if findings:
        prompt += ("\n\nRESULTS SO FAR (re-plan from these: do not repeat a refuted idea, "
                   "build on what held up, and pick candidates not yet tested)\n" + findings)
    negatives_injected: list[str] = []
    if flywheel is not None:
        negs = flywheel.prompt_context()
        if negs:
            prompt = prompt + "\n\n" + negs
            negatives_injected = [line.split(" ")[1] for line
                                  in negs.splitlines()[1:] if line.startswith("- ")]
            say("prompt includes previously ruled-out candidates (flywheel)")
    inv = ToolInvocation(envelope=env.model_copy(), tool="c.fm.generate_molecules")
    # every ATTEMPT leaves its flywheel context, written before the call: a generation that fails or is refused writes no
    # hypothesis_generation record, and a replay hands the contexts out one per attempt
    tracker.store.append(record_for(env, EventType.tool_invocation, actor, decision={
        "kind": "generation_attempt", "flywheel_context": (negs[:4000] if flywheel is not None and negs else "")}))
    raw = model.generate(inv, prompt).output["text"]
    # record the raw generation (agent reasoning is provenance too)
    store_append = tracker.store.append
    import hashlib as _hl
    store_append(record_for(env, EventType.tool_invocation, actor,
                            decision={"kind": "hypothesis_generation",
                                      # what the flywheel put in the prompt: a replay has no flywheel, so it replays this
                                      "flywheel_context": (negs[:4000] if flywheel is not None and negs else ""),
                                      "prompt_chars": len(prompt),
                                      "response_chars": len(raw),
                                      # retrieval trace: what shaped this gen
                                      "trace": {
                                          "prompt_sha256":
                                              _hl.sha256(
                                                  prompt.encode()).hexdigest()[:16],
                                          "model": getattr(model, "model",
                                                           "unknown"),
                                          "skill_guidance_chars":
                                              len(skill_guidance or ""),
                                          "flywheel_negatives":
                                              len(negatives_injected)
                                              if "negatives_injected" in dir()
                                              else 0,
                                          "candidates_count":
                                              len(candidates or []),
                                      }}))

    doc = _extract_json(raw)
    families_in = doc.get("families", [])[:max_families]
    family_scores: dict[str, float] = {}
    family_info: dict[str, dict[str, Any]] = {}
    specs: list[dict[str, Any]] = []
    # the program's primary hard constraint is the gate: variant criteria
    # inherit its metric/direction (threshold stays the variant's own
    # predicted value when the agent declared one)
    _con = next((c for c in problem.constraints if c.kind == "hard"
                 and c.limit is not None), None)
    _gate = None
    if _con is not None:
        _gate = {"metric": _con.name,
                 "direction": "max" if ">=" in (_con.description or "")
                 else "min",
                 "threshold": float(_con.limit)}
    for fam in families_in:
        fid = str(fam.get("family", f"fam-{len(family_scores)}"))[:40]
        family_scores[fid] = float(fam.get("confidence", 0.5))
        family_info[fid] = {k: fam.get(k, "") for k in ("parent_statement", "mechanism", "refutation_condition",
                                                         "rationale", "citation")}
        say(f"family '{fid}' (confidence {family_scores[fid]:.2f}): "
            f"{fam.get('parent_statement', '')[:80]}")
        for v in fam.get("variants", [])[:max_variants_per_family]:
            _assay = str(coerce_assay_type(
                v.get("assay_type", "binding")).value)
            # THE GATE IS THE PROGRAM'S, never the model's. A variant's predicted value is a PREDICTION (kept on the
            # hypothesis, shown in the plan, used by acquisition); it must not become the pre-registered criterion,
            # or a model could move the gate by predicting a number. Every variant is judged against the gate a person fixed.
            if _gate is None:
                _spec_criteria = None
            else:
                # which assays are dose-response fits is domain data in the modality pack, shared with the plan
                from proef.autoloop import criteria_for_assay
                _c = criteria_for_assay(_assay, SuccessCriteria(
                    metric=_gate["metric"], direction=_gate["direction"], threshold=float(_gate["threshold"])),
                    modality)
                _spec_criteria = {"metric": _c.metric, "direction": _c.direction, "threshold": _c.threshold}
            spec = {
                "predicted_by": {"source": "llm",
                                 "model": str(getattr(model, "model_version",
                                                     "")
                                              or getattr(model, "name",
                                                         "llm"))},
                "statement": v.get("statement", ""),
                "candidate": clean_candidate(v.get("candidate", "")),
                "assay_type": _assay,
                "criteria": _spec_criteria,
                "predicted": v.get("predicted", v.get("statement", "")),
                "predicted_value": v.get("predicted_value"),
                "rationale": v.get("rationale", ""),
                "variables": v.get("variables", {}),
                "protocol": sanitize_protocol(v.get("protocol")),
            }
            if tool_menu and (v.get("tools") or v.get("smiles")):
                # the SMILES stays local to the prediction: it is the model's claim, so it is not written onto the
                # hypothesis, where structure-keyed logic (series, applicability domain) would trust it
                _preds = _REG.run(v.get("tools") if isinstance(v.get("tools"), list) else [], {"smiles": str(v.get("smiles") or "")},
                                  predictors, modality)
                spec["metadata"] = {**spec.get("metadata", {}), "predictions": _preds}
            if menu:
                # the model's pick is validated on FACTS (in the pool, declares the assay, is where this runs); its
                # reasoning is never read. The result rides in the sealed proposal record.
                from proef.labselect import check_pick
                _chk = check_pick(v.get("instrument"), _assay, menu, runs_on, int((instruments or {}).get("max_bsl", 2)))
                spec["metadata"] = {**spec.get("metadata", {}), "lab_pick": _chk["pick"], "lab_check": _chk}
            for k in ("mechanism", "refutation_condition", "citation",
                      "predicted_observables"):
                if fam.get(k) is not None:
                    spec[k] = fam[k]
                elif v.get(k) is not None:
                    spec[k] = v[k]
            specs.append({"family": fid, "parent_statement":
                          fam.get("parent_statement", ""), **spec})

    # dedup against already-tried hypotheses + global total cap
    existing = existing_keys or set()
    seen: set[tuple[str, str]] = set()
    kept_specs: list[dict[str, Any]] = []
    for s_ in specs:
        key = (s_["candidate"], s_["statement"])
        if key in existing or key in seen:
            say(f"dropped duplicate hypothesis: {s_['candidate']} — "
                "already proposed/tried")
            continue
        if max_total_hypotheses is not None and len(kept_specs) >= max_total_hypotheses:
            say(f"hypothesis budget reached ({max_total_hypotheses}) — "
                "truncating portfolio (recorded)")
            break
        seen.add(key)
        kept_specs.append(s_)
    specs = kept_specs

    # verify every cited reference — the "never invent" clause, enforced
    cited = sorted({s.get("citation") for s in specs if s.get("citation")})
    if cited:
        from proef import citations
        results = {c: citations.verify(c) for c in cited}
        for s in specs:
            c = s.get("citation")
            if c and c in results:
                s["citation_status"] = results[c]["status"]
                s["citation_detail"] = results[c]["detail"]
        summary = ", ".join(f"{c}={results[c]['status']}" for c in cited)
        say(f"citation check: {summary}")
        bad = [c for c in cited if results[c]["status"] == "invalid"]
        if bad:
            say(f"WARNING: {len(bad)} fabricated/unresolvable citation(s) "
                "penalized in portfolio scoring")

    variants = tracker.propose_families(problem, [
        {**{k: f[k] for k in ("family", "parent_statement", "kind", "rationale")
            if k in f}, "variants": [s for s in specs if s["family"] == f.get("family")]}
        for f in families_in
    ])
    # carry per-variant tuned variables into hypothesis metadata
    for h in variants:
        for s in specs:
            if s["candidate"] == h.candidate and s["statement"] == h.statement:
                h.metadata["variables"] = s.get("variables", {})
                if s.get("protocol"):
                    h.metadata["protocol"] = s["protocol"]
    tracker.family_info = family_info        # the plan shown to the person is built from these
    tracker.family_scores = family_scores
    return variants, family_scores


_LABEL_PREFIX = re.compile(r"^\s*(?:proposed|candidate|compound|sample|hypothesis)\s*[:_\-]\s*", re.I)


def clean_candidate(raw: Any) -> str:
    """A model sometimes echoes a label into the name ("proposed: erlotinib", "proposed_osimertinib"). A candidate id is
    the compound or sample name only, because it is what the lab sample, the plate and the verdict are keyed on."""
    name = str(raw or "")
    for _ in range(2):
        name = _LABEL_PREFIX.sub("", name)
    return name.strip()


def _extract_json(raw: str) -> dict[str, Any]:
    text = raw.strip()
    doc = None
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        import re
        m = re.search(r"\{.*\}", text, re.S)
        if m:
            doc = json.loads(m.group(0))
    if not isinstance(doc, dict) or "families" not in doc:
        raise ValueError(
            "agent response must be a JSON object with a 'families' key — "
            "use a model that follows the schema (see PROMPT): " + text[:120])
    return doc


