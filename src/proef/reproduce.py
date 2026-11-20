"""`proef campaign reproduce`, replay a recorded experiment and diff the verdicts.

Reads a provenance branch, reconstructs the campaign EXACTLY as recorded
(hypotheses, criteria, lab adapter + seed, concentrations, autonomy, dose
design), re-runs it offline, and compares verdict-by-verdict. Reproducibility
as a command, not a promise.

Honest limits: LLM-proposed portfolios and live instruments cannot be replayed
byte-exactly (model drift, real-world noise), those branches report
`not-reproducible: <reason>` rather than faking a match.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any


def reproduce(branch: str, db_path: str = "proef.db",
              inbox: str | Path | None = None) -> dict:
    from proef.adapters.store.sqlite import SqliteProvenanceStore, get_store
    store = get_store(db_path)
    recs = store.branch_records(branch)
    if not recs:
        return {"status": "error", "reason": f"no records for '{branch}'"}

    env: dict = {}
    hypotheses: dict[str, dict] = {}
    recorded: dict[str, dict] = {}
    id_to_cand: dict[str, str] = {}
    removed_ids: set[str] = set()
    plan_declined = False
    replanned = False
    followed_up = False
    responses: list[dict] = []            # sealed model answers, in call order
    plan_decisions: list[dict] = []       # the person's plan reviews, in order
    sealed_tools: list[dict] = []         # knowledge-tool results the original sealed

    gen_contexts: list[str] = []          # what the flywheel put in each generation prompt
    old_gen_contexts: list[str] = []      # branches recorded before attempts were: one per SUCCESSFUL generation
    predictions: list[dict] = []          # every prediction the original sealed, so a replay runs no tool
    first_score: dict[str, float] = {}          # hypothesis id -> the score it was first selected on
    for r in recs:
        d = r.decision or {}
        et = r.event_type.value
        if et == "problem_defined":
            env = d.get("environment") or {}
            problem_doc = d
        elif et == "hypothesis_proposed":
            art = (r.artifact.metadata if r.artifact else {}) or {}
            cand = art.get("candidate")
            predictions.extend(p for p in (d.get("predictions") or []) if isinstance(p, dict))
            if cand and r.artifact:
                id_to_cand[r.artifact.artifact_id] = cand
            if cand:
                hypotheses[cand] = {
                    "statement": d.get("statement", ""),
                    "candidate": cand,
                    "assay_type": art.get("assay", "binding"),
                    "predicted": d.get("predicted", ""),
                    "predicted_value": (d.get("criteria") or {}).get(
                        "threshold"),
                    "mechanism": d.get("mechanism", ""),
                    "refutation_condition": d.get("refutation_condition", ""),
                    "citation": d.get("citation", ""),
                    "citation_status": d.get("citation_status", "unverified"),
                    # the family drives the per-round diversity cap and the flywheel prior: without it the
                    # replay would choose different batches than the original did
                    **({"family": d["family"]} if d.get("family") else {}),
                    **({"smiles": d["smiles"]} if d.get("smiles") else {}),
                    # the validator's verdict on the lab pick: a hypothesis the campaign's lab could not serve was never
                    # submitted, and its replay must not submit it either
                    **({"metadata": {"lab_check": d["lab_check"]}} if d.get("lab_check") else {}),
                }
        elif et == "tool_invocation" and d.get("kind") in ("model_response", "model_error"):
            responses.append(d)                # a call that FAILED is replayed as a failure, so the same fallback runs
        elif et == "tool_invocation" and d.get("kind") == "tool_result":
            sealed_tools.append(d)             # a knowledge result (PubMed, ChEMBL...) the original sealed, played back with no network
        elif et == "tool_invocation" and d.get("kind") == "generation_attempt":
            gen_contexts.append(d.get("flywheel_context") or "")
        elif et == "tool_invocation" and d.get("kind") == "hypothesis_generation":
            old_gen_contexts.append(d.get("flywheel_context") or "")
        elif et == "gate_decision" and d.get("kind") == "followup_proposal" and d.get("executes") and d.get("validation", {}).get("ok"):
            followed_up = True
        elif et == "hypotheses_filtered" and d.get("kind") not in ("plan_review", "acquisition_selection"):
            for dec in d.get("decisions") or []:
                if dec.get("score") is not None:
                    first_score.setdefault(dec.get("hypothesis_id"), float(dec["score"]))
        elif et == "gate_decision" and d.get("kind") == "plan_decision":
            plan_decisions.append(d)
            # the person's review of a model's plan decides WHAT was run: replay the same plan
            if d.get("stage", "initial") != "initial":
                replanned = True
                continue
            plan_declined = plan_declined or not d.get("approved")
            removed_ids |= set(d.get("removed") or [])
        elif et in ("hypothesis_validated", "hypothesis_invalidated",
                    "hypothesis_inconclusive",
                    "hypothesis_tested"):
            art = (r.artifact.metadata if r.artifact else {}) or {}
            cand = art.get("candidate")
            if cand and d.get("verdict"):
                recorded[cand] = d          # last one wins (final state)

    agent_replay = bool(responses) and env.get("hypothesis_source") == "agent"
    if followed_up and not agent_replay and any((r.decision or {}).get("followup_of") for r in recs if r.event_type.value == "hypothesis_proposed"):
        return {"status": "not-reproducible",
                "reason": "the model suggested follow-ups after a round and they were run; that step needs the model and is not replayed"}
    if replanned and not agent_replay:
        return {"status": "not-reproducible",
                "reason": "the model re-planned after a round; that step needs the model and is not replayed"}
    if plan_declined:
        return {"status": "not-reproducible", "reason": "the research plan was declined: nothing was run"}
    for hid in removed_ids:
        hypotheses.pop(id_to_cand.get(hid, ""), None)
    # the original run ranked its hypotheses by the model's own confidences, which a replay does not have:
    # reuse the scores it recorded, so the same batches are chosen in the same order (and the lab's noise,
    # which depends on run order, comes out the same)
    cand_score = {id_to_cand[h]: s for h, s in first_score.items() if h in id_to_cand}

    if not env.get("lab_seed") and env.get("lab") != "simulator":
        return {"status": "not-reproducible",
                "reason": "no recorded lab environment (pre-environment "
                          "provenance) or live lab, re-run once to capture"}
    if not hypotheses and not agent_replay:
        return {"status": "not-reproducible",
                "reason": "no candidate hypotheses in branch"}

    from proef.adapters.lab.simulator import SimulatorLab
    from proef.autoloop import CampaignConfig, run_campaign
    from proef.core.hypothesis import Constraint, ProblemStatement

    labs = {"simulator": SimulatorLab}
    lab_cls = labs.get(env.get("lab", "simulator"))
    if lab_cls is None:
        return {"status": "not-reproducible",
                "reason": f"no replay adapter for lab '{env.get('lab')}' "
                          "(live labs replay via recording playback, not here)"}

    _replay_base = (problem_doc.get("problem_id") or "prob") + "-replay"
    _replay_id, _k = _replay_base, 1
    while store.branch_records(f"problem/{_replay_id}"):
        _k += 1
        _replay_id = f"{_replay_base}-{_k}"
    problem = ProblemStatement(
        objective=problem_doc.get("objective", ""),
        problem_id=_replay_id,
        constraints=[Constraint(**c)
                     for c in problem_doc.get("constraints", [])])
    from proef.core.hypothesis import explanatory_bonus
    from proef.flywheel import Flywheel
    cfg = CampaignConfig(
        problem=problem,
        score_fn=(lambda h: cand_score.get(h.candidate, 0.5) - explanatory_bonus(h)) if cand_score else None,
        hypotheses=list(hypotheses.values()),
        # replay runs the loop the way it was recorded: an approval-gated run did not auto-confirm, so neither
        # does its replay (the person's earlier approvals are stood in for by an auto-approver below).
        # Records from before the rounds were captured replay as one round, as they always did.
        k=env.get("k", 2), rounds=int(env.get("rounds", 1)),
        autonomy=env.get("autonomy") if env.get("autonomy") in ("gate", "act") else "act",
        inbox=str(inbox or ".data/reproduce-inbox"),
        concentrations=tuple(env.get("concentrations",
                                     ["0.5uM", "2uM", "10uM", "40uM"])),
        dose_design=env.get("dose_design", "fixed"),
        poll_secs=0.05,
        # a replay must start from a clean memory and must not feed the user's real flywheel
        flywheel=Flywheel(":memory:"))
    rm = None
    if agent_replay:
        # an agent campaign replays with the model's sealed answers, never a model: re-plans and follow-ups included
        cfg, rm = _agent_cfg(cfg, env, responses, plan_decisions, gen_contexts or old_gen_contexts, first_score, predictions, sealed_tools)
    _seed = env.get("lab_seed")
    if _seed is None:
        _seed = 7                       # pre-seed-capture branches default
    _knobs = (env.get("lab_config") or ({"instruments": env["lab_capabilities"]} if env.get("lab_capabilities") else {})) if agent_replay else {}
    lab = lab_cls(seed=_seed, **_knobs)
    report = run_campaign(cfg, lab, SqliteProvenanceStore(),
                          approver=(lambda title: True) if cfg.autonomy == "gate" else None)
    divergences = list(getattr(rm, "divergences", []) or [])
    if rm is not None and not divergences and rm.remaining():
        divergences.append({"call": len(responses) - rm.remaining() + 1, "why": f"the replay stopped {rm.remaining()} model call(s) short of the original"})

    diffs, matches = [], 0
    for cand, rec in sorted(recorded.items()):
        new_v = report.verdicts.get(cand)
        old_v = rec.get("verdict")
        if new_v == old_v:
            matches += 1
        else:
            diffs.append({"candidate": cand, "recorded": old_v,
                          "replayed": new_v})
    # the other direction: anything the replay judged that the original never did is a difference too. Before this, a
    # replay that ran extra hypotheses was reported as a match, because only the recorded verdicts were compared.
    for cand, new_v in sorted(report.verdicts.items()):
        if cand not in recorded and new_v not in (None, "proposed"):
            diffs.append({"candidate": cand, "recorded": None, "replayed": new_v})
    for d in divergences:
        diffs.append({"candidate": None, "recorded": "model call", "replayed": d["why"], "call": d["call"]})
    n = len(recorded)
    return {
        "status": "match" if not diffs and n else ("differs" if n else
                                                   "no-verdicts"),
        "branch": branch, "recorded_verdicts": n, "matched": matches,
        "diffs": diffs,
        "environment": env,
        "model_pinned": rm is not None,
        "model_calls_replayed": (len(responses) - rm.remaining()) if rm is not None else 0,
        "note": (("simulated replay: identical lab seeds & structures, and every model answer played back from the ledger "
                  "(the model is never asked, and any call that differs from the original is reported)")
                 if rm is not None else
                 ("simulated replay: identical lab seeds & structures; "
                  "the generation model "
                  f"{env.get('model') or 'n/a'} is NOT re-invoked: "
                  "hypotheses are replayed exactly as recorded. "
                  "Reproduced: the EXECUTION path, not the model's "
                  "creative step.")),
    }


def _sealed_tool_results(sealed: list[dict]) -> dict:
    """The knowledge results the original sealed, by (tool, arguments hash): a replay plays them back and never calls the network."""
    return {(d.get("tool"), d.get("args_hash")) + ((d["scope"],) if d.get("scope") else ()): d.get("result") for d in sealed}


class _FrozenFlywheel:
    """What a replay uses in place of the flywheel: the contexts the original put in its prompts, in order, and no priors. The
    original's priors are not reconstructible, so selection scores come from the recorded ones instead (`_agent_cfg`)."""

    def __init__(self, contexts: list[str]):
        self._ctx = list(contexts)
        self._i = 0

    def prompt_context(self) -> str:
        c = self._ctx[self._i] if self._i < len(self._ctx) else ""
        self._i += 1
        return c

    def prior_bonus(self, family: str | None) -> float:
        return 0.0

    def submit(self, *a, **kw) -> None:
        return None


def _agent_cfg(cfg, env: dict, responses: list[dict], plan_decisions: list[dict], gen_contexts: list[str], first_score: dict,
               predictions: list[dict] | None = None, sealed_tools: list[dict] | None = None):
    """Turn the replay config into an AGENT campaign driven by the recorded model answers and the recorded plan reviews."""
    from dataclasses import replace

    from proef.autoloop import ResourceBudget
    from proef.core.hypothesis import explanatory_bonus
    from proef.model_guard import RecordedModel
    from proef.plan import PlanDecision
    rm = RecordedModel(responses)
    pending = iter(plan_decisions)

    def approver(plan: dict, problem_id: str):
        try:
            d = next(pending)
        except StopIteration:
            rm.divergences.append({"call": 0, "why": "the replay asked for a plan review the original did not have"})
            return PlanDecision(approved=False, by="replay")
        return PlanDecision(approved=bool(d.get("approved")), excluded=set(d.get("removed") or []), by=d.get("decided_by") or "replay",
                            note=d.get("note") or "")
    return replace(
        cfg, hypotheses=None, hypothesis_source="agent", model=rm, critic_model=rm if env.get("critic") else None,
        followups=bool(env.get("followups")), replan=bool(env.get("replan")),
        orchestrator="model" if env.get("orchestrator") == "model" else "fixed",
        max_families=int(env.get("max_families", cfg.max_families)),
        max_variants_per_family=int(env.get("max_variants_per_family", cfg.max_variants_per_family)),
        modality=env.get("modality") or None, prediction_tools=list(env.get("prediction_tools") or []) or None,
        prediction_registry=_RecordedPredictors(predictions or [], rm) if env.get("prediction_tools") else None,
        labs=env.get("labs") or None, lab_requirement=env.get("lab_requirement") or None,
        budget=ResourceBudget(**env["budget"]) if env.get("budget") else None,
        plan_approver=approver if env.get("plan_review") else None,
        # the original's selection scores, by hypothesis id (ids are a hash of the statement, candidate and round, so a replay
        # that gets the same answers makes the same ids)
        score_fn=(lambda h: first_score.get(h.hypothesis_id, 0.5) - explanatory_bonus(h)) if first_score else None,
        grounding_tools=tuple(env.get("grounding_tools") or ()), ground_hypotheses=bool(env.get("ground_hypotheses")),
        grounding_public_names=tuple(env.get("grounding_public_names") or ()), grounding_replay=_sealed_tool_results(sealed_tools or []),
        flywheel=_FrozenFlywheel(gen_contexts)), rm


class _RecordedPredictors:
    """A replay's stand-in for the prediction registry: the menu text comes from the real registry (it is part of the prompt, which is
    checked), but a prediction is looked up in what the original sealed, by tool and input. No tool is ever run. A prediction the
    original did not make is a divergence."""

    def __init__(self, recorded: list[dict], rm):
        from proef.predictors import DEFAULT
        self._real, self._rm = DEFAULT, rm
        self._by = {(p.get("tool"), p.get("input") or ""): p for p in recorded}

    def menu(self, enabled, modality):
        return self._real.menu(enabled, modality)

    def menu_text(self, specs):
        return self._real.menu_text(specs)

    def run(self, requested, inputs, enabled, modality):
        out = []
        from proef.predictors import MAX_TOOLS_PER_VARIANT
        for name in list(dict.fromkeys(str(n)[:80] for n in (requested or [])))[:MAX_TOOLS_PER_VARIANT]:
            hit = self._by.get((name, str((inputs or {}).get("smiles") or "")[:120])) or self._by.get((name, ""))
            if hit is None:
                self._rm.divergences.append({"call": 0, "why": f"a prediction by {name} that the original did not make"})
                out.append({"tool": name, "kind": "prediction", "evidence": False, "status": "error", "reason": "not in the recording"})
            else:
                out.append(hit)
        return out
