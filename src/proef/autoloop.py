"""One-call campaign loop: problem in, verdicts out.

`run_campaign()` assembles the full flow, problem, portfolio, filter,
design/test, lab awareness, verdicts, confirmation, so it can be started
manually by a scientist (one function call / `proef campaign auto`) or kicked off by an
agent (the same function, called programmatically or via MCP).

autonomy levels:
  "act"     , agent runs everything, incl. auto-submitted confirmation assays
  "gate"    , agent runs everything, but every lab submission needs the
               operator's approver callback to return True first
  "observe" , agent only reads, fits and recommends; submits nothing
"""
from __future__ import annotations

import json
import logging
import os
import time
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Callable

from proef.core.contracts import (
    Artifact, ArtifactKind, Envelope, EventType, ProtocolOp, ProtocolStep,
    ProtocolSubmission, ReagentSpec, SampleKind, SampleSpec,
)
from proef.core.hypothesis import (
    Constraint, ExperimentDesign, HypothesisTracker, PortfolioDecision,
    ProblemStatement, SuccessCriteria,
)
from proef.core.interfaces import ProvenanceStore, record_for, system_actor
from proef.core.safety import LabGateway, SafetyEngine
from proef.watch import AutoAnalysisAgent, DeviceMonitor

Autonomy = str  # "act" | "gate" | "observe"


from proef.fitting import hill_response  # noqa: E402

_log = logging.getLogger(__name__)


@dataclass
class ResourceBudget:
    """Hard caps on what a campaign may consume. Enforced at submission time
    (no new lab runs past the limit) and at generation time (no hypothesis
    explosion). Exhaustion is provenance-recorded, never silent."""
    max_total_runs: int = 50             # lab runs (incl. confirmations)
    max_hypotheses_total: int = 30       # across all rounds
    max_confirmations: int = 10
    max_model_calls: int = 200           # model calls across proposals, re-plans, the critic and the reviewer
    wall_clock_secs: float | None = None

    def lab_runs_used(self, store: ProvenanceStore, problem_id: str) -> int:
        # fail CLOSED: if the store cannot be read the budget must not
        # quietly report "0 runs used" and let a campaign overspend
        recs = store.branch_records(f"problem/{problem_id}")
        return sum(1 for r in recs if r.event_type.value == "lab_run")


@dataclass
class CampaignConfig:
    problem: ProblemStatement
    hypotheses: list[dict[str, Any]] | None = None   # config-defined portfolio...
    hypothesis_source: str = "config"         # ...or "agent": the model decides
    model: Any = None                         # required when hypothesis_source == "agent"
    candidates: list[dict[str, Any]] | None = None   # library shown to the agent
    skill_guidance: str | None = None         # Agent Skills text injected into
                                              # the hypothesis prompt (P: skills)
    max_families: int = 3
    max_variants_per_family: int = 3
    prediction_registry: Any = None        # a replay's stand-in that plays recorded predictions back instead of running tools
    prediction_tools: list[str] | None = None   # prediction tools the operator enables (priors, never evidence)
    grounding_tools: tuple[str, ...] = ()  # knowledge tools (pubmed, chembl, opentargets) a model may use BEFORE it proposes; () = off
    knowledge_services: dict[str, Any] = field(default_factory=dict)   # the adapters behind them, e.g. {'pubmed': PubMedAdapter()}
    grounding_replay: dict | None = None   # a replay's sealed knowledge results {(tool, args_hash): result}: played back, no network
    modality: str | None = None            # selects the domain pack (assays, prompt wording); None = small molecule
    orchestrator: Any = "fixed"           # 'fixed' (the configured order), 'model' (a model chooses among the allowed actions) or an object with choose()
    followups: bool = False                # after each round the model suggests what to do with each abstained hypothesis
    replan: bool = False                   # after each round the model proposes the next hypotheses from the results
    critic_model: Any = None               # a second model that critiques the plan before the person reads it (advisory)
    plan_covers_submissions: bool = True   # an approved plan also approves its own lab submissions
    plan_approver: Callable[[dict, str], Any] | None = None   # gate autonomy + model-proposed hypotheses:
                                                              # (plan, problem_id) -> PlanDecision
    score_fn: Callable[[Any], float] | None = None   # portfolio filter scoring
    k: int = 2                                # hypotheses taken to wet-lab
    k_schedule: tuple[int, ...] = ()          # per-round k, e.g. (4, 2, 1):
                                              # screen wide, then narrow.
                                              # falls back to k per round
    budget: ResourceBudget | None = None      # None = uncapped ( discouraged)
    concentrations: tuple[str, ...] = ("0.5uM", "2uM", "10uM", "40uM")
    dose_design: str = "fixed"          # "fixed" | "adaptive" (Bayesian
                                        # sequential dosing, 3 wells)
    noise_variant: int = 0              # measurement-noise offset: varies
                                        # noise, never the ground truth
    rounds: int = 1                     # sequential rounds; each round's
                                        # results feed the next selection
    policy: Any = None                  # assaybench-style ranker (e.g.
                                        # AssayLoopRanker): from round 2 it
                                        # picks the batch, gates still decide
    acquisition: str = ""               # "bo-ei": EI batch selection every
                                        # round, AD-gated (docs/ACQUISITION-
                                        # IMPLEMENTATION-PLAN.md); "" = today's
                                        # score/policy behaviour
    seed: int | None = None             # acquisition determinism seed
    inbox: str | Path = ".data/inbox"
    flywheel: Any = None                # shared Flywheel (default: local db)
    synthesis_retries: int = 1      # extra Make attempts on a refused
    synthesis_backend: str = "rule-based"  # named-reaction route gate:
    #   "rule-based" | "simulated" (planning only) | "none" (skip Make gate)
                                    # batch (process re-optimization);
                                    # each attempt is provenance-recorded
    autonomy: Autonomy = "gate"
    poll_secs: float = 0.5
    actor_name: str = "campaign"

    # S-T5: operator-declared instrument pool + the experiment's needs.
    # labs entries: {simulator:{...}} | {adapter:"pkg:Class",options:{}}
    # | {mcp:{command:[...],profile:"ezo|mettler|ika|{...}"}}
    labs: list[dict[str, Any]] | None = None
    lab_requirement: dict[str, Any] | None = None


@dataclass
class CampaignReport:
    problem_id: str
    selected: list[str]
    verdicts: dict[str, str] = field(default_factory=dict)   # candidate -> status
    recommendations: list[str] = field(default_factory=list)
    rounds_run: int = 1
    history: list[dict] = field(default_factory=list)
    stopped: str | None = None            # why the campaign ended before its rounds did: halt, wall_clock or orchestrator


from proef.core.contracts.models import coerce_assay_type


def announce_branch(eid: str, problem_id: str) -> None:
    """Map a workbench experiment id to the problem branch a campaign is
    about to write, so the API watcher can stream records incrementally
    instead of resolving the branch from logs only after the run ends.
    Best-effort: never breaks a campaign."""
    try:
        if not eid or not problem_id:
            return
        from proef.paths import CAMPAIGN_DIR
        CAMPAIGN_DIR.mkdir(parents=True, exist_ok=True)
        f = CAMPAIGN_DIR / "branch-map.json"
        m = {}
        if f.exists():
            try:
                m = json.loads(f.read_text())
            except Exception:
                _log.warning("branch-map unreadable; rebuilding", exc_info=True)
                m = {}
        if m.get(eid) == problem_id:
            return
        m[eid] = problem_id
        f.write_text(json.dumps(m))
    except Exception:
        _log.warning("could not announce branch %s -> %s", eid, problem_id,
                     exc_info=True)


def announced_branches() -> dict:
    """Read the campaign-id -> problem-branch map (empty if absent)."""
    try:
        from proef.paths import CAMPAIGN_DIR
        f = CAMPAIGN_DIR / "branch-map.json"
        return json.loads(f.read_text()) if f.exists() else {}
    except Exception:
        _log.warning("branch-map unreadable", exc_info=True)
        return {}


def _select_lab(cfg, lab, store, actor, approver, say):
    """S-T5: pick the lab from the operator-declared pool (no-op without one).
    Returns (lab, menu, runs_on): the instruments a model may choose from, and the one this campaign runs on."""
    # S-T5: the pool is the operator's fence; this matcher only picks
    # WITHIN it, and the decision — with every refusal reason — is
    # sealed before any lab acts.
    menu: list[dict] = []
    runs_on = getattr(lab, "name", None) or "lab"
    from proef.labselect import build_menu

    def _menu_of(pool_) -> list[dict]:
        # only the agent path reads the menu, and a lab whose capabilities are not ready must not stop a campaign that
        # never needed them
        if cfg.hypothesis_source != "agent":
            return []
        try:
            return build_menu(pool_)
        except Exception as exc:  # noqa: BLE001
            say(f"instrument menu unavailable ({type(exc).__name__}): picks will not be checked")
            return []
    def _warn_dropped() -> None:
        for m in menu:
            if m.get("dropped_names"):
                say(f"instrument '{m['name']}' declared {len(m['dropped_names'])} capability name(s) that are not safe to show a model, "
                    f"so they were left out: {', '.join(m['dropped_names'][:3])}")
    if not cfg.labs:
        menu = _menu_of([(runs_on, lab)]) if lab is not None else []
    if cfg.labs:
        from proef.labselect import LabRequirement, build_pool, select_lab
        pool = build_pool(cfg.labs)
        menu = _menu_of(pool)
        req = LabRequirement.build(cfg.lab_requirement)
        if not req.assay_types and not req.readouts and cfg.hypotheses:
            req = LabRequirement(
                assay_types=frozenset(
                    {h.get("assay_type", "binding")
                     for h in cfg.hypotheses}),
                max_bsl=req.max_bsl, physical=req.physical)
        sel, refusals = select_lab(pool, req)
        sbranch = f"problem/{cfg.problem.problem_id or 'pending'}"
        if sel is None:
            store.append(record_for(
                Envelope(experiment_branch_id=sbranch),
                EventType.gate_decision, actor,
                decision={"kind": "lab_refusal", "pool": [n for n, _ in pool],
                          "refusals": refusals,
                          "action": "campaign refused: no permitted "
                                    "instrument serves the declared need"}))
            raise ValueError(
                "lab selection refused, no instrument in the operator-"
                f"declared pool serves {sorted(req.assay_types)}"
                f"{sorted(req.readouts) and ['+ readouts: ' + str(sorted(req.readouts))]};"
                f" refusals: {refusals}")
        lab = sel.lab
        store.append(record_for(
            Envelope(experiment_branch_id=sbranch),
            EventType.gate_decision, actor,
            decision={"kind": "lab_selected", **sel.rationale}))
        # gate-mode autonomy extends to the instrument itself: when the
        # fence leaves a real CHOICE (2+ eligible), the human confirms
        # the machine's ranking before any lab is built around it
        if cfg.autonomy == "gate" and len(sel.rationale["eligible"]) > 1:
            ok = bool(approver and approver(
                f"LAB [problem/{cfg.problem.problem_id or 'pending'}]: use "
                f"'{sel.name}' for this campaign? eligible: "
                f"{sel.rationale['eligible']}"))
            store.append(record_for(
                Envelope(experiment_branch_id=sbranch),
                EventType.gate_decision, actor,
                decision={"kind": "lab_confirmed" if ok
                          else "lab_confirmation_declined",
                          "chosen": sel.name,
                          "eligible": sel.rationale["eligible"]}))
            if not ok:
                raise ValueError(
                    f"lab choice '{sel.name}' declined by the approver, "
                    f"campaign refused (eligible were "
                    f"{sel.rationale['eligible']})")
        runs_on = sel.name
        say(f"lab: '{sel.name}' selected from a pool of {len(pool)} "
            f"(simulated={sel.rationale['chosen']['simulated']}); "
            f"decision sealed")
    _warn_dropped()
    return lab, menu, runs_on


def _build_gateway(cfg, lab, safety, store, actor, approver, say, gov):
    """The submission gate: human-approved in gate mode, else advisory-reviewed."""
    if cfg.autonomy == "gate":
        if approver is None:
            raise ValueError("autonomy='gate' requires an approver callback")
        branch_hint = f"[problem/{cfg.problem.problem_id or 'pending'}]"
        gateway = _GatedGateway(
            lab, safety, store, actor,
            lambda t: approver(f"{t} {branch_hint}"), say, governance=gov)
    else:
        from proef.advisory import AdvisoryReviewer
        gateway = LabGateway(lab, safety, store, actor,
                             advisory=AdvisoryReviewer(), governance=gov,
                             approver=approver)
    return gateway


def _durc_gate(cfg, store, actor) -> None:
    """Refuse (and record) a campaign naming a select agent / regulated toxin."""
    from proef.adapters.bio.tox_alerts import durc_screen
    durc_hits = durc_screen(" ".join([cfg.problem.objective,
                                      cfg.problem.context or ""]))
    if durc_hits:
        store.append(record_for(
            Envelope(experiment_branch_id=f"problem/{cfg.problem.problem_id or 'unnamed'}"),
            EventType.gate_decision, actor,
            decision={"kind": "durc_refusal", "agents": durc_hits,
                      "action": "campaign refused before hypothesis work"}))
        raise ValueError(
            f"DURC: campaign names a select agent / regulated toxin family "
            f"({', '.join(durc_hits)}), refused and recorded; requires "
            f"institutional review before Proef will run it")


def _unique_branch(cfg, store, say) -> None:
    # every run gets its own branch: an explicit problem_id reused from a
    # previous campaign is auto-suffixed rather than merging records
    _base_pid = cfg.problem.problem_id
    _n = 1
    while store.branch_records(f"problem/{cfg.problem.problem_id}"):
        _n += 1
        cfg.problem.problem_id = f"{_base_pid}-{_n}"
    if cfg.problem.problem_id != _base_pid:
        say(f"branch '{_base_pid}' already holds a previous run, this "
            f"campaign recorded separately as '{cfg.problem.problem_id}'")


def _propose_and_score(cfg, tracker, problem, store, flywheel, say, lab_ctx=None):
    """Hypothesis portfolio + the scoring function ranking it each round."""
    family_scores: dict[str, float] = {}
    if cfg.hypothesis_source == "agent":
        from proef.hypothesis_agent import propose_families_with_agent
        budget = cfg.budget or ResourceBudget()
        used = budget.lab_runs_used(store, cfg.problem.problem_id or "")
        remaining_runs = max(0, budget.max_total_runs - used)
        grounding_text = None
        if cfg.grounding_tools:
            from proef.grounding import gather
            g = gather(cfg.model, cfg.problem, store, cfg.knowledge_services, cfg.grounding_tools,
                       protected={str(c.get("id") or c.get("candidate") or "") for c in (cfg.candidates or []) if isinstance(c, dict)}
                       | {cfg.problem.problem_id or ""}, replay=cfg.grounding_replay)
            grounding_text = g.text or None
            say(f"grounding: {len(g.calls)} knowledge call(s), {len(g.used)} answered" + (f" (stopped: {g.stopped})" if g.stopped else ""))
        portfolio, family_scores = propose_families_with_agent(
            tracker, cfg.problem, cfg.model, candidates=cfg.candidates, grounding=grounding_text,
            modality=cfg.modality,
            max_families=cfg.max_families,
            max_variants_per_family=cfg.max_variants_per_family,
            max_total_hypotheses=max(0, budget.max_hypotheses_total
                                     - len(tracker.hypotheses)),
            existing_keys={(h.candidate, h.statement) for h in tracker.hypotheses.values()},
            skill_guidance=cfg.skill_guidance,
            flywheel=flywheel,
            notify=say, instruments=lab_ctx, predictors=cfg.prediction_tools,
            registry=cfg.prediction_registry)
        say(f"resource budget: {remaining_runs}/{budget.max_total_runs} lab runs remaining")
        say(f"agent proposed {len(portfolio)} variant hypotheses")
        _base = cfg.score_fn or (
            lambda h: family_scores.get(h.family or "", 0.5))
        score_fn = lambda h: _base(h) + flywheel.prior_bonus(h.family)
    else:
        portfolio = tracker.propose_portfolio(problem, cfg.hypotheses or [])
        # surrogate prior: rank config hypotheses by their own predicted
        # value under the gate direction (lower predicted is better under a
        # min-gate). Activates only when predictions differ — uniform
        # predictions keep the old constant, so explanatory-bonus and
        # flywheel tie-breaks are untouched.
        _pvs = {getattr(h, "predicted_value", None) for h in portfolio}
        _pvs.discard(None)
        if cfg.score_fn is not None:
            base_fn = cfg.score_fn
        elif len(_pvs) > 1:
            _con0 = next((c for c in problem.constraints if c.kind == "hard"
                          and c.limit is not None), None)
            _max_dir = ">=" in (_con0.description or "")
            def base_fn(h, _md=_max_dir):
                pv = getattr(h, "predicted_value", None)
                if pv is None:
                    return 0.5
                pv = max(0.0, float(pv))
                return (pv / (1.0 + pv)) if _md else (1.0 / (1.0 + pv))
        else:
            base_fn = lambda h: 0.5
        score_fn = lambda h: base_fn(h) + flywheel.prior_bonus(h.family)
    return portfolio, score_fn


def _declare_criteria(problem, lab, portfolio, modality=None):
    """The pre-registered gate (SuccessCriteria) + the acquisition log-axis flag."""
    # metric-aware criteria: the problem's primary hard constraint is the
    # pre-registered gate — thread it into every hypothesis so verdicts
    # read the program's metric/direction, never a hardcoded IC50
    _con = next((c for c in problem.constraints if c.kind == "hard"
                 and c.limit is not None), None)
    _crit = None
    if _con is not None:
        from proef.core.hypothesis import SuccessCriteria
        _crit = SuccessCriteria(
            metric=_con.name,
            direction="max" if ">=" in (_con.description or "") else "min",
            threshold=float(_con.limit))
        if _crit.metric not in ("ic50_um", "value") and lab is not None \
                and hasattr(lab, "set_property_gate"):
            # the lab switches to dose-independent property readout ONLY
            # when the campaign really measures properties. A constraint
            # NAMED "potency" over dose-response assays is still an IC50
            # campaign — the per-round criteria below override it to
            # ic50_um; forcing property mode here would export flat
            # plates that Hill-fits then decode as fake nano-molar hits
            # (Oct-2026 review: eval drift caught exactly this)
            from proef.modality import metric_for_assay
            _dose_campaign = any(metric_for_assay(h.assay_type or "binding", modality)
                                 for h in portfolio)
            if not _dose_campaign:
                lab.set_property_gate(_crit.metric, _crit.direction,
                                      _crit.threshold)

    # acquisition axis: in a dose-response campaign the declared µM values
    # live on the LOG scale (predicted_sd is a fold uncertainty, not µM) —
    # the constraint NAME ("potency") tells you nothing about the scale,
    # the assay type does. Property campaigns rank linearly.
    from proef.modality import metric_for_assay as _mfa
    _acq_log = (any(_mfa(h.assay_type or "binding", modality) for h in portfolio)
                or (_crit is not None and _crit.metric == "ic50_um"))
    return _crit, _acq_log


def _install_budget_gate(gateway, cfg, store, actor, say) -> None:
    """Route every submission (incl. confirmations) through the run budget."""
    _submit = gateway.submit

    def gateway_submit(protocol):  # budget-enforced submission
        if cfg.budget is not None:
            used = cfg.budget.lab_runs_used(store, cfg.problem.problem_id or "")
            if used >= cfg.budget.max_total_runs:
                say(f"BUDGET EXHAUSTED: {used}/{cfg.budget.max_total_runs} lab "
                    "runs used, submission refused (recorded)")
                store.append(record_for(
                    Envelope(experiment_branch_id=f"problem/{cfg.problem.problem_id}"),
                    EventType.tool_invocation, actor,
                    decision={"kind": "budget_exhausted",
                              "lab_runs": used,
                              "cap": cfg.budget.max_total_runs}))
                return None, f"budget exhausted ({used} runs >= cap)"
        return _submit(protocol)

    gateway.submit = gateway_submit  # confirmations flow through the same gate


def _doses(h, cfg) -> list[str]:
    """The dose ladder for one hypothesis: the model's validated design when it gave one, else the campaign's default."""
    proto = (h.metadata or {}).get("protocol")
    return list(proto["concentrations"]) if proto and proto.get("concentrations") else list(cfg.concentrations)


def _design_protocol(h, cfg, problem, adaptive, screen_conc):
    """Build the lab protocol for one hypothesis and attach its design."""
    protocol = ProtocolSubmission(
        envelope=Envelope(experiment_branch_id=f"problem/{problem.problem_id}"),
        title=f"screen-{h.candidate}" if adaptive else f"test-{h.candidate}",
        assay_type=str(coerce_assay_type(h.assay_type).value),
        steps=[ProtocolStep(seq=1, op=ProtocolOp.dispense,
                            params={"volume_ul": 50})],
        samples=[SampleSpec(
            sample_id=h.candidate, kind=SampleKind.compound,
            concentration=c,
            metadata={"smiles": (h.metadata or {}).get("smiles", ""),
                      "sequence": (h.metadata or {}).get("sequence", ""),
                      "description": f"{h.candidate} construct",
                      "intent": ("covalent" if "covalent" in
                                 (h.mechanism or "").lower() else "")})
                 for c in ([screen_conc] if adaptive
                           else _doses(h, cfg))]
        + [SampleSpec(sample_id=f"ctrl-neg{i}", kind=SampleKind.buffer,
                      concentration="10uM") for i in (1, 2)],
        reagents=[ReagentSpec(name="assay-buffer", amount="5 mL",
                              bsl_level=1)],
        # a follow-up may ask for its own replicate count: a request, honored only by a lab that declares support
        constraints=({"replicates": int(h.metadata["replicates"])} if (h.metadata or {}).get("replicates") else {}),
    )
    h.design = ExperimentDesign(
        assay_type=str(coerce_assay_type(h.assay_type).value), concentrations=list(_doses(h, cfg)),
        replicates=int((h.metadata or {}).get("replicates") or 3), controls=["negative"],
        protocol_id=protocol.protocol_id)
    return protocol


def _make_candidate(h, protocol, cfg, store, say, synth_cache) -> bool:
    """Make: route planning, toxicophore pre-screen, synthesis (+ biologic
    stability QC) BEFORE Test. False = the batch never reaches the lab; the
    refusal is already on the chain."""
    # Make: plan routes, then synthesize & purify before Test — a
    # compound with NO viable route never burns a batch, and a failed
    # batch never reaches the lab (deterministic per candidate)
    synth = synth_cache.get(h.candidate)
    if synth is None:
        from proef.adapters.lab.synthesis import \
            VirtualSynthesizer
        vs = VirtualSynthesizer()
        best_route = None
        smi = (h.metadata or {}).get("smiles")
        mod = str((h.metadata or {}).get("modality", "")).lower()
        # Gate-ordering debt (agent-tier finding, Oct 2026): the
        # toxicophore screen ran only at SUBMISSION — after Make had
        # already synthesized a batch that could never be assayed.
        # Pre-screen the declared structure at Make entry with the
        # same rules (intent respected: a declared covalent warhead
        # still passes); the gateway rule remains the backstop.
        # Refusal semantics unchanged; waste eliminated.
        if smi and mod not in ("biologic", "cell therapy", "mrna"):
            from proef.adapters.bio.tox_alerts import (
                RULES_VERSION, screen_smiles)
            _intent = ("covalent" if "covalent" in
                       (h.mechanism or "").lower() else "")
            _hits = screen_smiles(smi, _intent)["refuse"]
            if _hits:
                say(f"{h.candidate}: REFUSED at Make entry, "
                    f"toxicophore pre-screen (never synthesized)")
                store.append(record_for(
                    protocol.envelope, EventType.gate_decision,
                    system_actor("safety"),
                    decision={
                        "kind": "safety_refusal",
                        "stage": "make_prescreen",
                        "candidate": h.candidate,
                        "samples": [h.candidate],
                        "reason": (
                            f"sample '{h.candidate}' carries "
                            f"{len(_hits)} toxicophore alert(s) "
                            f"[rules {RULES_VERSION}]: "
                            + "; ".join(f"{x['alert']} ({x['note']})"
                                        for x in _hits[:3])
                            + ", screened before synthesis "
                              "(gateway rule remains the backstop)"),
                        "action": "Make skipped; batch never "
                                  "spent"})
                )
                return False
        if (smi and mod not in ("biologic", "cell therapy", "mrna")
                and cfg.synthesis_backend != "none"):
            from proef.adapters.retrosynthesis import \
                get_retrosynthesis_adapter
            plans = get_retrosynthesis_adapter(
                cfg.synthesis_backend).plan_routes(smi)
            viable = [p for p in plans if p.feasible]
            if not viable:
                say(f"{h.candidate}: NO VIABLE SYNTHESIS ROUTE, "
                    f"blocked at Make (never reaches Test)")
                store.append(record_for(
                    protocol.envelope, EventType.gate_decision,
                    system_actor("synthesis"),
                    decision={
                        "kind": "no_viable_route",
                        "candidate": h.candidate,
                        "reason": "rule-corpus retrosynthesis found "
                                  "no route meeting the yield/"
                                  "lead-time floors",
                        "routes_planned": len(plans),
                        "best_modeled_yield_pct": max(
                            (p.yield_pct for p in plans), default=None),
                        "smiles": smi,
                        "data_origin": plans[0].data_origin,
                        "samples": [h.candidate]}))
                return False
            best_route = max(viable,
                             key=lambda p: p.yield_pct).summary()
        for att in range(1 + max(0, cfg.synthesis_retries)):
            synth = vs.synthesize(
                h.candidate, smi,
                modality=mod,
                attempt=att + 1, route=best_route)
            store.append(record_for(
                protocol.envelope, EventType.synthesis_complete,
                system_actor("synthesis"),
                artifact=Artifact(artifact_id=f"syn-{synth['lot']}",
                                  kind=ArtifactKind.other, uri="",
                                  metadata=dict(synth)),
                decision={"candidate": h.candidate,
                          "yield_pct": synth["yield_pct"],
                          "purity": synth["purity"],
                          "ok": synth["ok"],
                          "lot": synth["lot"],
                          "attempt": att + 1,
                          **({"route": synth["route"],
                              "route_lead_time_days":
                                  synth["route_lead_time_days"],
                              "route_cost_usd":
                                  synth["route_cost_usd"],
                              "data_origin": synth["data_origin"]}
                             if "route" in synth else {})}))
            say(f"{synth.get('action', 'synthesized')} "
                f"{h.candidate}: lot {synth['lot']} "
                f"yield {synth['yield_pct']}% purity "
                f"{synth['purity']}%"
                + (f" (attempt {att + 1})" if att else ""))
            if synth["ok"]:
                break
        synth_cache[h.candidate] = synth
    if not synth["ok"]:
        say(f"batch for {h.candidate} refused into Test: "
            f"{synth['note']}")
        store.append(record_for(
            protocol.envelope, EventType.gate_decision,
            system_actor("synthesis"),
            decision={"kind": "synthesis_failed",
                      "candidate": h.candidate,
                      "lot": synth["lot"],
                      "reason": synth["note"],
                      "samples": [h.candidate]}))
        return False
    # biologic Make has a second QC: stability / aggregation.
    # A batch that would aggregate in the vial never reaches an
    # assay — the refusal is honest, not a missing verdict
    if synth.get("modality", "").lower() in ("biologic",
                                             "cell therapy",
                                             "mrna"):
        from proef.adapters.lab.bioprocess import bioprocess_qc
        qc = bioprocess_qc(h.candidate,
                           (h.metadata or {}).get("sequence", ""),
                           synth["lot"])
        store.append(record_for(
            protocol.envelope, EventType.stage_transition,
            system_actor("bioprocess"),
            decision={"kind": "bioprocess_qc", "candidate":
                      h.candidate, **qc}))
        if not qc["ok"]:
            say(f"batch for {h.candidate} refused into Test: "
                f"{qc['note']}")
            store.append(record_for(
                protocol.envelope, EventType.gate_decision,
                system_actor("bioprocess"),
                decision={"kind": "stability_failed",
                          "candidate": h.candidate,
                          "lot": synth["lot"],
                          "reason": qc["note"],
                          "samples": [h.candidate]}))
            return False
    return True


def _review_pass(cfg, store, problem, say, use_model: bool = True) -> None:
    """Independent audit (and the flags-only LLM advisory layer) before
    results are finalized; recorded as provenance."""
    # reviewer pass: independent audit before results are finalized.
    # Findings are recorded as provenance and surfaced in the workbench;
    # the reviewer demands revision, it never clears a gate
    from proef.reviewer import review_campaign
    review = review_campaign(store.branch_records(
        f"problem/{problem.problem_id or 'unnamed'}"))
    store.append(record_for(
        Envelope(experiment_branch_id=
                 f"problem/{problem.problem_id or 'unnamed'}"),
        EventType.stage_transition, system_actor("reviewer"),
        decision={"kind": "review_report", "review": review["review"],
                  "critical": review["critical"],
                  "warnings": review["warnings"],
                  "verdicts_reviewed": review["verdicts_reviewed"],
                  "findings": review["findings"]}))
    if review["findings"]:
        say(f"reviewer: {review['critical']} critical / "
            f"{review['warnings']} warning finding(s), "
            + "; ".join(f["detail"] for f in
                        review["findings"][:3]))
    else:
        say("reviewer: no findings, analysis consistent")

    # LLM advisory layer: only when a model was part of this campaign.
    # Its output is flags-only (schema-enforced) — advisory by construction
    if cfg.model is not None and use_model:
        from proef.reviewer import review_llm
        llm_review = review_llm(
            store.branch_records(
                f"problem/{problem.problem_id or 'unnamed'}"), cfg.model)
        store.append(record_for(
            Envelope(experiment_branch_id=
                     f"problem/{problem.problem_id or 'unnamed'}"),
            EventType.stage_transition, system_actor("llm-reviewer"),
            decision={"kind": "review_report_llm", **llm_review}))
        if llm_review.get("unavailable"):
            say(f"llm reviewer unavailable: "
                f"{llm_review['unavailable']}")
        elif llm_review["flags"]:
            say(f"llm reviewer ({llm_review['model']}): "
                f"{len(llm_review['flags'])} advisory flag(s)")
        else:
            say(f"llm reviewer ({llm_review['model']}): no advisory "
                "flags")


def _resolve_series(store, problem, actor, flywheel, say) -> None:
    # S-T1.2: series-level claims derive their verdicts from members'
    # CI-aware verdicts — never tested directly, abstention dominates,
    # and resolutions feed the flywheel's family prior.
    from proef.series import resolve_series_claims
    branch = f"problem/{problem.problem_id}"
    _claim_ev = {"confirmed": EventType.hypothesis_validated,
                 "refuted": EventType.hypothesis_invalidated,
                 "inconclusive": EventType.hypothesis_inconclusive}
    for c in resolve_series_claims(store.branch_records(branch)):
        if c["status"] == "open":
            say(f"series claim {c['family_id']}: open, {c['reason']}")
            continue
        store.append(record_for(
            Envelope(experiment_branch_id=branch), _claim_ev[c["status"]],
            actor,
            decision={"hypothesis_id": c["family_id"],
                      "family_id": c["family_id"],
                      "verdict": c["status"],
                      "claim_kind": "series-derived", "derived": True,
                      "family_kind": c["kind"],
                      "retain_fold": c["retain_fold"],
                      "parent_statement": c["parent_statement"],
                      "reason": c["reason"],
                      "derived_from": [list(x) for x in c["derived_from"]]}))
        flywheel.submit(f"series:{c['family_id']}", c["family_id"],
                        "series-claim", c["status"])
        say(f"series claim {c['family_id']}: {c['status'].upper()}, "
            f"{c['reason']}")


def criteria_for_assay(assay_type: str, crit, modality: str | None = None):
    """The pre-registered criterion a hypothesis on this assay is judged by: the program's gate, in the assay's own metric.
    One place, so a hypothesis created any way (agent, plan, toolkit, MCP) is judged by the same rule. Which assays are
    dose-response fits is domain data in the modality pack, not a list in this code."""
    from proef.core.hypothesis import SuccessCriteria
    from proef.modality import metric_for_assay
    if metric_for_assay(assay_type, modality):
        # IC50-style assay: the Hill fit produces ic50_um and lower-is-better, whatever the constraint is named
        return SuccessCriteria(metric="ic50_um", direction="min", threshold=float(crit.threshold))
    return crit.model_copy()


def _assign_criteria(selected, _crit, modality: str | None = None) -> None:
    """Thread the pre-registered gate into each hypothesis of the round."""
    if _crit is not None:
        for h in selected:
            h.criteria = criteria_for_assay(h.assay_type, _crit, modality)
            h.metadata = {**(h.metadata or {}), "criteria_source": "gate"}


def _collect_round(monitor, agent, selected, adaptive, cfg, terminal) -> None:
    """Feed this round's instrument outputs to the analysis agent until every
    selected hypothesis is terminal (or the monitor goes quiet)."""
    # analyze this round's outputs
    n_expected = sum(1 for h in selected if h.design and h.design.run_id)
    time.sleep(0.2)
    cap = n_expected * (4 if adaptive else 2) + 1
    for i, ev in enumerate(monitor.events(
            stop_after=cap,
            idle_timeout=max(5.0, cfg.poll_secs * 30))):
        agent.handle(ev)
        if all(h.status in terminal for h in selected) and i + 1 >= n_expected:
            break


def _plan_gate(cfg, tracker, portfolio, store, actor, say, label=None, tested=None):
    """Pause for the person's decision on the proposed plan. Returns (portfolio to run, declined?).
    Every outcome is recorded: approval, removals (with a reason) and decline."""
    from proef.plan import build_plan
    pid = cfg.problem.problem_id or "unnamed"
    plan = build_plan(portfolio, getattr(tracker, "family_info", {}), getattr(tracker, "family_scores", {}), tested=tested)
    if label:
        plan["label"] = label
    critic = cfg.critic_model
    if critic is not None:
        from proef.critic import critique_plan
        gate_txt = "; ".join(c.description for c in cfg.problem.constraints if c.kind == "hard") or ""
        notes = critique_plan(critic, plan, cfg.problem.objective, gate_txt)
        who_ = f"{getattr(critic, 'name', 'critic')}/{getattr(critic, 'model_version', '?')}"
        plan["critique"] = {"model": who_, "notes": notes}
        store.append(record_for(Envelope(experiment_branch_id=f"problem/{pid}"), EventType.gate_decision, actor, decision={
            "kind": "plan_critique", "model": who_, "notes": notes, "advisory": True,
            "stage": label or "initial"}))
        say(f"critic ({who_}): {len(notes)} note(s) on the plan")
    decision = cfg.plan_approver(plan, pid)
    env = Envelope(experiment_branch_id=f"problem/{pid}")
    removed = [h for h in portfolio if h.hypothesis_id in decision.excluded]
    kept = [h for h in portfolio if h.hypothesis_id not in decision.excluded]
    approved = bool(decision.approved and kept)
    store.append(record_for(env, EventType.gate_decision, actor, decision={
        "kind": "plan_decision", "approved": approved, "decided_by": decision.by, "note": decision.note,
        "n_proposed": len(portfolio), "n_kept": len(kept) if approved else 0,
        "removed": [h.hypothesis_id for h in removed], "stage": label or "initial",
        "reason": None if approved else ("every hypothesis was removed" if decision.approved else "the plan was declined")}))
    if removed and decision.approved:
        from proef.core.hypothesis import PortfolioDecision
        store.append(record_for(env, EventType.hypotheses_filtered, actor, decision={
            "problem_id": pid, "kind": "plan_review", "decisions": [PortfolioDecision(
                hypothesis_id=h.hypothesis_id, selected=False, score=0.0,
                reason=f"removed from the plan by {decision.by}").model_dump() for h in removed]}))
    if not approved:
        say(f"plan {'declined' if not decision.approved else 'emptied'} by {decision.by}: nothing was submitted to the lab")
        return [], True
    say(f"plan approved by {decision.by}: {len(kept)} of {len(portfolio)} hypotheses go ahead"
        + (f" ({len(removed)} removed)" if removed else ""))
    return kept, False


def run_campaign(cfg: CampaignConfig, lab: Any,
                 store: ProvenanceStore,
                 approver: Callable[[str], bool] | None = None,
                 notify: Callable[[str], None] | None = None,
                 sandbox: Any = None) -> CampaignReport:
    """Start-to-finish: problem -> portfolio -> filter -> test -> verdict -> confirm."""
    clock = _Clock()                     # the wall clock a budget's wall_clock_secs is measured against, minus waits on people
    approver = clock.timed(approver)
    cfg = replace(cfg, plan_approver=clock.timed(cfg.plan_approver)) if cfg.plan_approver is not None else cfg
    if cfg.modality:
        from proef.modality import PACKS, get_pack
        if get_pack(cfg.modality) is None:   # never a silent fall-back to small molecule
            raise ValueError(f"unknown modality {cfg.modality!r}. Choose one of: {', '.join(PACKS)}")
    actor = system_actor(cfg.actor_name)
    from proef.flywheel import Flywheel
    flywheel = cfg.flywheel if cfg.flywheel is not None else Flywheel()

    def _on_verdict(h) -> None:
        flywheel.submit(h.candidate, h.family, h.assay_type, h.status,
                        h.observed_value, h.observed_ci,
                        problem_id=(cfg.problem.problem_id or None),
                        threshold=(h.criteria.threshold if h.criteria else None),
                        evidence_run=h.evidence_run_id)

    tracker = HypothesisTracker(store, actor, on_verdict=_on_verdict)

    safety = SafetyEngine()
    from proef.governance import AuthorizationMatrix, HaltSwitch, PauseRegistry
    gov = {"matrix": AuthorizationMatrix(), "halt": HaltSwitch(), "pause": PauseRegistry()}
    say = notify or (lambda m: print(f"[campaign] {m}"))

    # resolve the branch name FIRST: lab selection (below) already writes
    # records onto problem/<id>, which would otherwise make every campaign
    # with a lab pool look like a re-run of itself and get a '-2' suffix
    _unique_branch(cfg, store, say)
    lab, _menu, _runs_on = _select_lab(cfg, lab, store, actor, approver, say)
    lab_ctx = {"menu": _menu, "runs_on": _runs_on,
               "max_bsl": int((cfg.lab_requirement or {}).get("max_bsl", 2))}   # the campaign's own ceiling, as select_lab uses

    gateway = _build_gateway(cfg, lab, safety, store, actor, approver, say, gov)
    getattr(gateway, "_gw", gateway).on_wait = clock.add_wait       # a pause that holds a submission does not use the campaign's time

    # 0. problem + portfolio + filter
    _durc_gate(cfg, store, actor)
    if sandbox is not None:
        from proef.sandbox_session import SandboxedLab
        sandbox.start(f"problem/{cfg.problem.problem_id}")
        lab = SandboxedLab(sandbox, lab=lab)
        say(f"sandbox: lab ops execute inside the experiment sandbox "
            f"({lab.name} @ problem/{cfg.problem.problem_id}), every op "
            f"is a sealed sandbox_exec")
    problem = tracker.define_problem(cfg.problem, environment={
        "lab": getattr(lab, "name", "unknown"),
        "lab_version": getattr(lab, "version", None),
        "lab_seed": getattr(lab, "seed", None),
        "concentrations": list(cfg.concentrations),
        "autonomy": cfg.autonomy,
        "dose_design": cfg.dose_design,
        "k": cfg.k,
        "rounds": cfg.rounds,
        "model": getattr(cfg.model, "model", None),
        # what a replay needs to rebuild the same campaign without a model
        "hypothesis_source": cfg.hypothesis_source, "followups": cfg.followups, "replan": cfg.replan,
        "max_families": cfg.max_families, "max_variants_per_family": cfg.max_variants_per_family,
        "modality": cfg.modality, "prediction_tools": list(cfg.prediction_tools or []),
        "grounding_tools": list(cfg.grounding_tools or []),
        "critic": cfg.critic_model is not None, "plan_review": cfg.plan_approver is not None,
        "orchestrator": cfg.orchestrator if isinstance(cfg.orchestrator, str) else "custom",
        "lab_config": _json_safe(lambda: lab.replay_config()),       # the lab's own knobs, so a replay measures the same way
        "lab_capabilities": _json_safe(lambda: lab.list_capabilities()),
        "labs": _json_safe(lambda: cfg.labs), "lab_requirement": _json_safe(lambda: cfg.lab_requirement),
        "budget": ({"max_total_runs": cfg.budget.max_total_runs, "max_hypotheses_total": cfg.budget.max_hypotheses_total,
                    "max_confirmations": cfg.budget.max_confirmations, "max_model_calls": cfg.budget.max_model_calls}
                   if cfg.budget else None),
    })
    from proef.model_guard import govern_models
    cfg = govern_models(cfg, store, actor, gov, say, on_wait=clock.add_wait)     # model calls obey halt, pause and the call budget, like lab submissions
    from proef.campaign_fsm import FixedOrchestrator, ModelOrchestrator
    orch = (cfg.orchestrator if hasattr(cfg.orchestrator, "choose") else
            ModelOrchestrator(cfg.model) if cfg.orchestrator == "model" and cfg.model is not None else FixedOrchestrator())
    portfolio, score_fn = _propose_and_score(
        cfg, tracker, problem, store, flywheel, say, lab_ctx)
    _crit, _acq_log = _declare_criteria(problem, lab, portfolio, cfg.modality)

    # announce the branch for live streaming: the API watcher maps the
    # campaign id to this problem branch the moment it exists, so the
    # workbench streams records incrementally instead of at the end
    _cid = os.environ.get("PROEF_CAMPAIGN_ID")
    if _cid:
        announce_branch(_cid, problem.problem_id)

    # 0b. a model-proposed plan is shown to a person BEFORE anything is submitted
    if cfg.plan_approver is not None and cfg.hypothesis_source == "agent":
        portfolio, declined = _plan_gate(cfg, tracker, portfolio, store, actor, say)
        if not declined and cfg.plan_covers_submissions and isinstance(gateway, _GatedGateway):
            gateway.covered = {h.candidate for h in portfolio}
        if declined:
            return CampaignReport(problem_id=problem.problem_id or "", selected=[], verdicts={}, rounds_run=0)

    # 1. rounds: select -> design+submit -> analyze; each round feeds the next
    synth_cache: dict[str, dict] = {}
    followed_up: set[str] = set()       # candidates a follow-up has already been made for (one each)
    _install_budget_gate(gateway, cfg, store, actor, say)

    # per-experiment outbox: two campaigns on one inbox would collide on
    # run ids (sim-N-001) and the monitor's seen-set would swallow the second
    inbox = Path(cfg.inbox) / problem.problem_id
    inbox.mkdir(parents=True, exist_ok=True)
    adaptive = cfg.dose_design == "adaptive"
    screen_conc = cfg.concentrations[len(cfg.concentrations) // 2] \
        if adaptive else None

    def _export(run_id: str, candidate: str,
                concs: list[str] | None = None, replicates: int | None = None) -> None:
        _export_instrument_file(inbox, run_id, candidate,
                                concs or list(cfg.concentrations), lab=lab,
                                noise_variant=cfg.noise_variant, replicates=replicates)

    messages: list[str] = []
    agent = AutoAnalysisAgent(notify=(lambda m: (messages.append(m), say(f"[agent] {m}"))),
                              tracker=tracker, gateway=gateway,
                              branch_id=f"problem/{problem.problem_id}",
                              auto_confirm=cfg.autonomy == "act" and not adaptive,
                              export_fn=_export,
                              adaptive=adaptive,
                              dose_ladder=tuple(
                                  float(str(c).rstrip("uM"))
                                  for c in cfg.concentrations) + (80.0,))
    monitor = DeviceMonitor(inbox, poll_secs=cfg.poll_secs)
    # "validated" is PRELIMINARY in fixed mode (confirmation pending), so it
    # must NOT count as terminal or the loop exits before confirmations run.
    terminal = {"confirmed", "refuted", "invalidated", "inconclusive"}

    remaining = list(portfolio)
    history: list[dict] = []
    tested: list = []
    stopped: str | None = None
    for round_no in range(1, max(1, cfg.rounds) + 1):
        stopped = _limit_reason(cfg, gov, clock, store, actor, say, round_no)
        if stopped:
            break
        if not remaining:
            say(f"round {round_no}: no untested candidates remain, stopping")
            break
        k_round = (cfg.k_schedule[min(round_no - 1, len(cfg.k_schedule) - 1)]
                   if cfg.k_schedule else cfg.k)
        acq_sel = None
        if cfg.acquisition == "bo-ei":
            selected, acq_sel = _acquisition_select(
                remaining, k_round, _crit, tracker, say, round_no,
                seed=cfg.seed if cfg.seed is not None else round_no,
                metric_log=_acq_log)
        elif cfg.policy is not None and round_no > 1:
            selected = _policy_select(cfg, remaining, tracker, say)
        else:
            selected = tracker.select_portfolio(problem, remaining, score_fn,
                                                k=k_round,
                                                max_per_family=1)
        remaining = [h for h in remaining if h not in selected]
        if not selected:
            # even an empty batch is an honest outcome — record WHY before
            # stopping, so the refusals are on the chain, not just in stdout
            if acq_sel is not None:
                store.append(record_for(
                    Envelope(experiment_branch_id=
                             f"problem/{problem.problem_id or 'unnamed'}"),
                    EventType.hypotheses_filtered, actor,
                    decision=acq_sel.decision_payload()))
            say(f"round {round_no}: nothing selected, stopping")
            break
        _assign_criteria(selected, _crit, cfg.modality)
        if _crit is not None:
            # the gate that replaced each proposal's criterion is itself a record, so a rebuild from the ledger (a plate
            # submitted later) judges against the gate and not the placeholder the proposal was recorded with
            for h in selected:
                store.append(record_for(
                    Envelope(experiment_branch_id=f"problem/{problem.problem_id}"), EventType.gate_decision, actor,
                    decision={"kind": "criteria_assigned", "hypothesis_id": h.hypothesis_id, "candidate": h.candidate,
                              "criteria": h.criteria.model_dump(), "source": "gate"}))
        say(f"round {round_no}: testing "
            f"{[h.candidate for h in selected]} "
            f"({len(remaining)} candidates remain)")
        # round boundary in provenance: the DMTA strip groups events
        # per round from these markers
        store.append(record_for(
            Envelope(experiment_branch_id=
                     f"problem/{problem.problem_id or 'unnamed'}"),
            EventType.stage_transition, actor,
            decision={"kind": "round_start", "round": round_no,
                      "candidates": [h.candidate for h in selected]}))
        if acq_sel is not None:
            # "why this plate" rides the chain beside the round marker —
            # picks with their EI arithmetic, refusals with their gate
            store.append(record_for(
                Envelope(experiment_branch_id=
                         f"problem/{problem.problem_id or 'unnamed'}"),
                EventType.hypotheses_filtered, actor,
                decision=acq_sel.decision_payload()))
        if cfg.autonomy == "observe":
            say("observe-only: nothing submitted")
            return CampaignReport(problem_id=problem.problem_id,
                                  selected=[h.candidate for h in selected])

        # design + submit this round. ONE measurement per (candidate, assay): the same compound proposed under two
        # families is measured once and every hypothesis on it is judged from that plate, not run twice with the same
        # sample name and a second plate that nothing reads.
        measured: dict[tuple[str, str], tuple[Any, str, str]] = {}
        for h in selected:
            _lc = (h.metadata or {}).get("lab_check") or {}
            if _lc.get("status") == "cannot_run":
                # the plan said so before approval. A person can still approve the rest, but this one never reaches a lab
                # that declares no such assay: refused and recorded, not run on a wrong instrument
                store.append(record_for(
                    Envelope(experiment_branch_id=f"problem/{problem.problem_id}"), EventType.gate_decision, actor,
                    decision={"kind": "lab_cannot_serve", "candidate": h.candidate, "assay_type": h.assay_type,
                              "runs_on": _lc.get("runs_on"), "eligible": _lc.get("eligible"),
                              "reasons": _lc.get("reasons")}))
                say(f"{h.candidate}: not submitted. {', '.join(_lc.get('reasons') or [])}")
                continue
            key = (h.candidate, h.assay_type or "binding")
            # what the hypothesis asks to be measured: its designed dose ladder and its tuned variables
            sig = json.dumps({"protocol": (h.metadata or {}).get("protocol"), "variables": (h.metadata or {}).get("variables")},
                             sort_keys=True, default=str)
            if key in measured and measured[key][2] != sig:
                # a variant that tunes the test differently is NOT answered by the first one's run, so it is not judged from
                # it. It stays proposed (recorded), and a later round can test it under its own conditions.
                store.append(record_for(
                    Envelope(experiment_branch_id=f"problem/{problem.problem_id}"), EventType.gate_decision, actor,
                    decision={"kind": "shared_measurement_declined", "candidate": h.candidate, "hypothesis_id": h.hypothesis_id,
                              "run_id": measured[key][1],
                              "reason": "its designed test differs from the run already made for this compound, so that run "
                                        "cannot answer it"}))
                say(f"{h.candidate}: a different designed test from the one already run, so it waits for its own run")
                continue
            if key in measured:
                first_protocol, first_run, _sig = measured[key]
                tracker.attach_test(h, first_protocol, first_run)
                h.design.run_id = first_run
                store.append(record_for(
                    Envelope(experiment_branch_id=f"problem/{problem.problem_id}"),
                    EventType.gate_decision, actor,
                    decision={"kind": "shared_measurement", "candidate": h.candidate, "run_id": first_run,
                              "hypotheses": [x.hypothesis_id for x in selected
                                             if (x.candidate, x.assay_type or "binding") == key],
                              "reason": "one compound, one measurement: every hypothesis on it is judged from this run"}))
                say(f"{h.candidate} is proposed more than once: sharing run {first_run}, no second lab run")
                continue
            protocol = _design_protocol(h, cfg, problem, adaptive, screen_conc)
            if not _make_candidate(h, protocol, cfg, store, say, synth_cache):
                continue
            if isinstance(gateway, _GatedGateway):
                gateway.current = h.candidate
            run_id, rejection = gateway.submit(protocol)
            if rejection:
                say(f"submission for {h.candidate} rejected: {rejection}")
                continue
            tracker.attach_test(h, protocol, run_id)
            h.design.run_id = run_id
            measured[key] = (protocol, run_id, sig)
            say(f"started lab run {run_id} for {h.candidate} on "
                f"{getattr(lab, 'name', 'instrument')} "
                f"({len(_doses(h, cfg)) if not adaptive else 1} dose"
                f"{'s' if (len(_doses(h, cfg)) if not adaptive else 1) > 1 else ''}"
                f" × {int((h.metadata or {}).get('replicates') or getattr(lab, 'replicates', 3) or 3)} replicates)")
            _export(run_id, h.candidate,
                    [screen_conc] if adaptive else _doses(h, cfg),
                    replicates=int((h.metadata or {}).get("replicates") or 0) or None)

        _collect_round(monitor, agent, selected, adaptive, cfg, terminal)
        verdicts_r = {h.candidate: h.status for h in selected}
        store.append(record_for(
            Envelope(experiment_branch_id=f"problem/{problem.problem_id}"),
            EventType.stage_transition, actor,
            decision={"stage": f"round-{round_no}-complete",
                      "picked": sorted(verdicts_r), "verdicts": verdicts_r}))
        history.append({"round": round_no,
                        "picked": sorted(verdicts_r),
                        "verdicts": verdicts_r})
        tested.extend(selected)
        # the decision point: what the campaign does with a round's results. The allowed actions are computed here from what the
        # operator permits and what remains, an orchestrator (fixed or a model) chooses among them, and every choice is recorded
        from proef.campaign_fsm import Action, Choice, State, View, allowed_actions
        done: set[str] = set()
        while True:
            stopped = _limit_reason(cfg, gov, clock, store, actor, say, round_no, final=round_no >= max(1, cfg.rounds))
            if stopped == "final":
                stopped = None            # the last round's work is done: carry on to the review, do not mark it stopped
                break
            if stopped:
                break
            view = View(round_no=round_no, rounds=max(1, cfg.rounds), verdicts={h.candidate: h.status for h in tested},
                        abstained=[h.candidate for h in selected if h.status == "inconclusive"], queued=len(remaining),
                        runs_left=_runs_left(cfg, store, problem), done=set(done))
            allowed = allowed_actions(State.ROUND_JUDGED, view=view, followups_permitted=cfg.followups,
                                      replan_permitted=cfg.replan, has_model=cfg.model is not None,
                                      agent_source=cfg.hypothesis_source == "agent")
            choice = orch.choose(State.ROUND_JUDGED, allowed, view)
            if choice.action not in allowed:      # whatever an orchestrator returns, it cannot leave the allowed list
                choice = Choice(Action.NEXT_ROUND.value, "the choice was not an allowed action", "fixed-fallback", False,
                                [f"'{choice.action}' is not allowed here ({', '.join(allowed)})"])
            store.append(record_for(
                Envelope(experiment_branch_id=f"problem/{problem.problem_id}"), EventType.gate_decision, actor,
                decision={"kind": "orchestrator_choice", "state": State.ROUND_JUDGED.value, "round": round_no, "allowed": allowed,
                          "chosen": choice.action, "source": choice.source, "valid": choice.valid, "reasons": choice.reasons,
                          "why": choice.why[:300]}))
            if choice.action == Action.FOLLOW_UP.value:
                done.add(choice.action)
                # a follow-up must not crowd out what is already queued: runs the remaining hypotheses are likely to need come first
                _queued = min(len(remaining), cfg.k * max(0, cfg.rounds - round_no))
                new_f = _followups(cfg, tracker, selected, store, actor, say, lab, _crit, round_no,
                                   followed_up, runs_left=max(0, _runs_left(cfg, store, problem) - _queued),
                                   last_round=round_no >= max(1, cfg.rounds), tested=tested)
                remaining.extend(new_f)
                _install_score_for_new(new_f, tracker)
                if new_f and cfg.plan_approver is not None and cfg.plan_covers_submissions and isinstance(gateway, _GatedGateway):
                    gateway.covered |= {h.candidate for h in new_f}
            elif choice.action == Action.REPLAN.value:
                done.add(choice.action)
                new = _replan(cfg, tracker, problem, store, actor, flywheel, tested, round_no, say, lab_ctx)
                remaining.extend(new)
                _install_score_for_new(new, tracker)
                if cfg.plan_approver is not None and cfg.plan_covers_submissions and isinstance(gateway, _GatedGateway):
                    gateway.covered |= {h.candidate for h in new}
            elif choice.action == Action.STOP.value:
                stopped = "orchestrator"
                store.append(record_for(
                    Envelope(experiment_branch_id=f"problem/{problem.problem_id}"), EventType.gate_decision, actor,
                    decision={"kind": "campaign_stopped", "why": "orchestrator", "round": round_no, "detail": choice.why[:300]}))
                say(f"round {round_no}: the orchestrator ended the campaign ({choice.why[:120] or 'no reason given'})")
                break
            else:
                break
        if stopped:
            break

    verdicts = {h.candidate: h.status for h in tested}
    # a campaign ended by the stop switch or the wall clock spends no more model calls, not even the advisory reviewer's
    _review_pass(cfg, store, problem, say, use_model=stopped not in ("halt", "wall_clock"))

    _resolve_series(store, problem, actor, flywheel, say)

    say(f"CAMPAIGN COMPLETE {problem.problem_id}: "
        + (", ".join(f"{c}={s}" for c, s in verdicts.items()) or "nothing tested"))
    return CampaignReport(problem_id=problem.problem_id, stopped=stopped,
                          selected=[h.candidate for h in tested],
                          verdicts=verdicts,
                          recommendations=[m for m in messages
                                           if "recommendation" in m],
                          rounds_run=len(history), history=history)


class _Clock:
    """The campaign's wall clock: elapsed time MINUS time it spent waiting on a person (a plan or a submission approval) or on a
    pause, because those waits use no compute and are the operator's, not the campaign's."""

    def __init__(self) -> None:
        self.t0, self.waited = time.monotonic(), 0.0

    def add_wait(self, seconds: float) -> None:
        self.waited += max(0.0, seconds)

    def elapsed(self) -> float:
        return time.monotonic() - self.t0 - self.waited

    def timed(self, fn):
        """A callable that reports how long it blocked (an approver waits for a person)."""
        if fn is None:
            return None

        def wrapper(*a, **kw):
            t = time.monotonic()
            try:
                return fn(*a, **kw)
            finally:
                self.add_wait(time.monotonic() - t)
        return wrapper


def _limit_reason(cfg, gov, clock, store, actor, say, round_no, final: bool = False) -> str | None:
    """Checked at every transition. The halt switch and the wall clock end a campaign, and the reason is recorded. Neither is
    reachable by a tool or a model: the switch is a file only a person or the operator API writes, and the clock is the budget's."""
    why = None
    halt = gov["halt"].engaged() if gov.get("halt") else None
    if halt:
        why = ("halt", f"operator stop-condition engaged: {halt.get('reason', '')}")
    else:
        b = cfg.budget
        if b is not None and b.wall_clock_secs is not None and clock.elapsed() >= b.wall_clock_secs:
            why = ("wall_clock", f"the {b.wall_clock_secs:g}s wall-clock budget is spent")
    if not why:
        return None
    if final:
        # every round has already run: there is nothing left to end, so it is not an early stop and nothing is recorded as one
        return "final"
    store.append(record_for(
        Envelope(experiment_branch_id=f"problem/{cfg.problem.problem_id}"), EventType.gate_decision, actor,
        decision={"kind": "campaign_stopped", "why": why[0], "round": round_no, "detail": why[1]}))
    say(f"round {round_no}: campaign stopped ({why[1]})")
    return why[0]


def _json_safe(get):
    """A value for the recorded environment, or None when it is not plain JSON (a live handle must not poison the record)."""
    try:
        v = get()
        json.dumps(v)
        return v
    except Exception:  # noqa: BLE001
        return None


def _runs_left(cfg, store, problem) -> int:
    budget = cfg.budget or ResourceBudget()
    return max(0, budget.max_total_runs - budget.lab_runs_used(store, problem.problem_id or ""))


def _followups(cfg, tracker, round_hyps, store, actor, say, lab, crit, round_no, followed_up, runs_left, last_round, tested):
    """The model SUGGESTS what to do with each abstained hypothesis; code validates and records every suggestion. Only a
    validated follow-up can run: under `act` directly, under `gate` after a person approves it in plan review, never under
    `observe`. Accept and drop are a person's recorded decision and are never executed here. Returns the new hypotheses."""
    from proef.followup import propose_followups, validate_followup
    abstained = {h.hypothesis_id: h for h in round_hyps if h.status == "inconclusive"}
    if not abstained:
        return []
    gate_txt = "; ".join(c.description for c in cfg.problem.constraints if c.kind == "hard")
    try:
        raw = propose_followups(cfg.model, list(abstained.values()), gate_txt)
    except Exception as exc:  # noqa: BLE001 - a refused or failed suggestion must not stop the campaign
        say(f"follow-up suggestions unavailable ({type(exc).__name__}): the abstentions wait for a person")
        return []
    _budget = cfg.budget or ResourceBudget()
    ctx = {"abstained": abstained, "gate": crit, "runs_left": runs_left, "followed_up": followed_up, "seen": set(),
           "hyp_room": max(0, _budget.max_hypotheses_total - len(tracker.hypotheses)),
           "supports_replicates": bool(getattr(lab, "supports_replicates", False))}
    env = Envelope(experiment_branch_id=f"problem/{cfg.problem.problem_id}")
    proposals = [validate_followup(r, ctx) for r in raw]
    for p in proposals:
        store.append(record_for(env, EventType.gate_decision, actor, decision={**p, "kind": "followup_proposal", "proposal_kind": p["kind"], "round": round_no}))
    ok = [p for p in proposals if p["validation"]["ok"]]
    say(f"model suggested {len(proposals)} follow-up action(s) on {len(abstained)} abstained hypothes"
        f"{'is' if len(abstained) == 1 else 'es'}: {len(ok)} valid")
    runnable = [p for p in ok if p["executes"]]
    if cfg.autonomy == "observe" or last_round or not runnable:
        return []        # observe runs nothing, and a last-round follow-up has no round left to run in: suggestions only
    new = []
    for p in runnable:
        h = abstained[p["hypothesis_id"]]
        pr = p["params"]
        # what the follow-up CHANGES, in the words a person approves: only what the lab will actually do
        changes = ([f"{pr['replicates']} replicates"] if pr.get("replicates") else []) + (["a designed dose ladder"] if pr.get("concentrations") else [])
        # the parent's structure and prior travel with it (smiles, sequence, modality, prediction, posterior): without them the
        # gateway's structure checks and an acquisition policy see a blank candidate. Only what must change is left out.
        inherited = {k: v for k, v in (h.metadata or {}).items()
                     if k not in ("protocol", "replicates", "followup_of", "criteria_source", "variables")}
        fu = tracker.propose(
            statement=f"{h.candidate}: follow-up with {' and '.join(changes)} (after round {round_no})",
            candidate=h.candidate, assay_type=h.assay_type, predicted=h.predicted, predicted_value=h.predicted_value,
            criteria=h.criteria.model_copy(), rationale=p["rationale"], mechanism=h.mechanism,
            refutation_condition=h.refutation_condition, round_no=round_no + 1, branch_id=h.branch_id,
            **{**inherited, "family": h.family or "", "followup_of": h.hypothesis_id},
            **({"replicates": pr["replicates"]} if pr.get("replicates") else {}),
            **({"protocol": {"concentrations": pr["concentrations"], "why": pr.get("why", "")}} if pr.get("concentrations") else {}))
        fu.problem_id, fu.family = h.problem_id, h.family
        new.append(fu)
    if cfg.autonomy == "gate" and cfg.plan_approver is not None:
        new, declined = _plan_gate(cfg, tracker, new, store, actor, say, label=f"follow-ups after round {round_no}", tested=tested)
        if declined:
            return []
    # the candidate's one follow-up is spent only by a follow-up that survived the person's review
    followed_up.update(h.candidate for h in new)
    return new


def _install_score_for_new(new, tracker) -> None:
    """New hypotheses are ranked by the family confidence the model gave them."""
    scores = getattr(tracker, "family_scores", {}) or {}
    for h in new:
        scores.setdefault(h.family or "", 0.5)


def _replan(cfg, tracker, problem, store, actor, flywheel, tested, round_no, say, lab_ctx=None):
    """After a round, the model proposes what to try next FROM the results; a person approves it
    (the same plan gate) before anything is added. Returns the approved new hypotheses."""
    from proef.hypothesis_agent import propose_families_with_agent
    budget = cfg.budget or ResourceBudget()
    room = budget.max_hypotheses_total - len(tracker.hypotheses)
    if room <= 0:
        say("re-plan skipped: the hypothesis limit is reached")
        return []
    findings = "\n".join(
        f"- {h.candidate}: {h.status}"
        + (f", observed {h.observed_value:g}" if h.observed_value is not None else "")
        + f", {h.statement}" for h in tested)
    before = set(tracker.hypotheses)
    try:
        new, _ = propose_families_with_agent(
            tracker, cfg.problem, cfg.model, candidates=cfg.candidates,
            max_families=cfg.max_families, max_variants_per_family=cfg.max_variants_per_family,
            max_total_hypotheses=room,
            existing_keys={(h.candidate, h.statement) for h in tracker.hypotheses.values()},
            skill_guidance=cfg.skill_guidance, flywheel=flywheel, notify=say, findings=findings,
            modality=cfg.modality, instruments=lab_ctx, predictors=cfg.prediction_tools,
            registry=cfg.prediction_registry)
    except Exception as exc:  # noqa: BLE001 - a failed re-plan must not abort the campaign
        say(f"re-plan failed ({type(exc).__name__}): continuing with the existing plan")
        return []
    new = [h for h in new if h.hypothesis_id not in before]
    if not new:
        say("re-plan: the model had nothing new to add")
        return []
    say(f"re-plan after round {round_no}: the model proposes {len(new)} more hypotheses")
    if cfg.plan_approver is not None:
        new, declined = _plan_gate(cfg, tracker, new, store, actor, say, label=f"after round {round_no}", tested=tested)
        if declined:
            return []
    return new


def _acquisition_select(remaining, k, crit, tracker, say, round_no, seed=0,
                        metric_log=False):
    """BO-EI batch pick over the round's candidates. Posteriors are DECLARED
    by whoever produced the hypotheses (predicted_value + optional
    predicted_sd + AD stamp in metadata), never fabricated here. Every
    refusal (no posterior, AD gate, diversity, budget) is returned for the
    provenance record, so 'why this plate' is answerable months later.

    metric_log: True in dose-response campaigns, µM values live on the log
    scale and predicted_sd is a FOLD uncertainty, not an absolute µM
    spread. The constraint NAME never determines this; the assay type does.

    Returns (selected Hypotheses, Selection)."""
    from proef.acquisition import Posterior, select_batch
    by_cand = {h.candidate: h for h in remaining}
    posteriors, no_post = [], []
    for h in remaining:
        if h.predicted_value is None:
            no_post.append(h.candidate)
            continue
        meta = h.metadata or {}
        posteriors.append(Posterior(
            candidate=h.candidate, mean=float(h.predicted_value),
            sd=meta.get("predicted_sd"), family=h.family,
            in_domain=not meta.get("extrapolation", False),
            ad_note=str(meta.get("ad_note", "")),
            source=str(meta.get("predicted_source", "declared"))))
    direction = crit.direction if crit is not None else "min"
    target = float(crit.threshold) if crit is not None else None
    # reference point = best CONFIRMED hit (never a QC-failed/inconclusive
    # number: 'improve on best' means the best result we actually trust)
    obs = [h.observed_value for h in tracker.hypotheses.values()
           if h.observed_value is not None
           and h.status in ("validated", "confirmed")]
    best = (min(obs) if direction == "min" else max(obs)) if obs else None
    sel = select_batch(posteriors, k=k, target=target, best_observed=best,
                       direction=direction, metric_log=metric_log,
                       seed=seed, round_no=round_no, max_per_family=1)
    for c in no_post:
        sel.refusals.append(_refusal_obj(
            c, "no declared posterior (predicted_value missing), "
               "acquisition needs a mean, never invents one"))
    selected = [by_cand[p.candidate] for p in sel.picks if p.candidate in by_cand]
    say(f"acquisition[{sel.policy}]: picked "
        f"{[h.candidate for h in selected]} · refused {len(sel.refusals)} "
        f"(AD-veto {sum(1 for r in sel.refusals if 'extrapolation' in r.reason)})")
    return selected, sel


def _refusal_obj(candidate, reason):
    from proef.acquisition import Refusal
    return Refusal(candidate=candidate, reason=reason)


def _policy_select(cfg, remaining, tracker, say):
    """Round r>1 batch choice by an assaybench-style policy. The policy ranks
    candidates from prior observations; Proef keeps every veto: diversity
    cap here, safety + budget + approval at the gate."""
    obs = [{"candidate": h.candidate,
            "hit": h.status in ("validated", "confirmed"),
            "relevance_score": (-(h.observed_value or 0.0))
            if h.observed_value is not None else 0.0}
           for h in tracker.hypotheses.values()
           if h.status not in ("proposed", "designed")]
    cands = [h.candidate for h in remaining]
    if hasattr(cfg.policy, "pick_batch"):
        picks = list(cfg.policy.pick_batch(cands, obs, cfg.k))
    elif hasattr(cfg.policy, "rank"):
        picks = [c for c, _ in cfg.policy.rank(cands, obs)[:cfg.k]]
    else:
        say("policy has neither pick_batch nor rank, falling back to scores")
        return tracker.select_portfolio(cfg.problem, remaining,
                                        cfg.score_fn or (lambda h: 0.5),
                                        k=cfg.k, max_per_family=1)
    by_cand = {h.candidate: h for h in remaining}
    out, fam = [], set()
    for c in picks:
        h = by_cand.get(c)
        if h is None or (h.family or "") in fam and h.family:
            continue
        if h.family:
            fam.add(h.family)
        out.append(h)
    say(f"policy picked {[h.candidate for h in out]} from "
        f"{len(obs)} observations")
    return out

def _export_instrument_file(inbox: Path, run_id: str, candidate: str,
                            concentrations: tuple[str, ...], lab: Any = None,
                            noise_variant: int = 0, replicates: int | None = None) -> None:
    """Stand-in for the instrument finishing: it drops its export in the
    outbox. Uses the lab's per-compound ground truth when available so the
    fitted IC50 reflects simulated pharmacology, not a fixed pattern."""
    import random

    time.sleep(0.1)
    rng = random.Random(f"{run_id}:{candidate}:v{noise_variant}")
    n_rep = replicates or (getattr(lab, "replicates", 3) if lab is not None else 3)
    lines = ["Well ID,Compound,Conc (uM),RFU", "A1,ctrl,10,100", "A2,ctrl,10,104"]
    # property-gate campaigns: the export reads the lab's property true
    # value (dose-independent, straddles the gate) — NOT the IC50 curve
    gate = getattr(lab, "_property_gate", None) if lab is not None else None
    prop_mode = bool(gate)
    true_prop = lab._property_true_value(run_id, candidate) \
        if prop_mode else None
    true_ic50 = None
    if lab is not None and hasattr(lab, "apparent_ic50_um"):
        true_ic50 = lab.apparent_ic50_um(run_id, candidate)     # includes any method-specific bias of this run's assay
    elif lab is not None and hasattr(lab, "true_ic50_um"):
        true_ic50 = lab.true_ic50_um(run_id, candidate)
    well = 3
    for c in concentrations:
        conc = float(str(c).rstrip("uM"))
        if prop_mode:
            base = true_prop
        else:
            base = hill_response(conc, true_ic50, 500.0, eps=1e-6) if true_ic50 \
                else 110 + concentrations.index(c) * 90
        for _ in range(n_rep):          # real plates have replicate wells
            noise = rng.gauss(0.0, max(4.0, base * 0.05))
            lines.append(f"A{well},{candidate},{c},{max(0.0, base + noise):.1f}")
            well += 1
    (inbox / f"{run_id}.csv").write_text("\n".join(lines) + "\n")


class _GatedGateway:
    """LabGateway wrapper where every submission needs operator approval first
    (autonomy='gate'). Rejection by the human is final; SafetyEngine still runs."""

    def __init__(self, lab: Any, safety: SafetyEngine, store: ProvenanceStore,
                 actor: Any, approver: Callable[[str], bool], say: Callable[[str], None],
                 governance: Any = None):
        # operator already approved at THIS layer; inner human-column checks
        # are satisfied; never-column and halts still bind below
        self._gw = LabGateway(lab, safety, store, actor,
                              governance=governance,
                              approver=(lambda t: True) if governance else None)
        self._approver = approver
        self._say = say
        self.covered: set[str] = set()   # candidates a person already approved in the plan review
        self.current: str | None = None  # candidate whose protocol is being submitted

    def submit(self, protocol: ProtocolSubmission):
        title = getattr(protocol, "title", str(protocol))
        if self.current in self.covered:
            # one human decision (the plan) covers its own submissions; the safety engine still runs
            self._gw.store.append(record_for(
                protocol.envelope, EventType.gate_decision, self._gw.actor,
                decision={"kind": "covered_by_plan", "title": title, "candidate": self.current,
                          "reason": "within the plan a person approved"}))
        elif not self._approver(title):
            self._say(f"operator declined: '{title}', submission cancelled")
            self._gw.store.append(record_for(
                protocol.envelope, EventType.gate_decision, self._gw.actor,
                decision={"kind": "operator_declined", "title": title,
                          "reason": "declined by operator",
                          "samples": sorted({sp.sample_id
                                             for sp in protocol.samples})}))
            return None, "declined by operator"
        return self._gw.submit(protocol)

    def results(self, *a, **kw):
        return self._gw.results(*a, **kw)

    def capabilities(self, *a, **kw):
        return self._gw.capabilities(*a, **kw)
