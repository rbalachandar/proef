"""AgentToolkit — wires Proef capabilities as tools for an LLM tool-calling loop.

Each tool is a named function with an Anthropic-compatible schema. The toolkit
connects to the real Proef layer: LabGateway (safety), ADMET panel,
retrosynthesis, provenance store, and TPP. Agents cannot bypass these — the
gateway is the only path to the lab.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Callable

from proef.core.contracts import Envelope, EventType, ProtocolOp, \
    ProtocolStep, ProtocolSubmission, ReagentSpec, SampleKind, SampleSpec
from proef.core.interfaces import record_for

TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "name": "rank_variants",
        "description": "Score and rank candidate genetic variants by "
                       "AlphaGenome Atlas Variant Impact (AVI) score with "
                       "feature attributions (splicing, chromatin, expression, "
                       "conservation, protein impact). Input: list of "
                       "{chromosome, position, ref, alt}.",
        "input_schema": {
            "type": "object",
            "properties": {
                "variants": {
                    "type": "array",
                    "items": {"type": "object",
                              "properties": {"chromosome": {"type": "string"},
                                             "position": {"type": "integer"},
                                             "ref": {"type": "string"},
                                             "alt": {"type": "string"}},
                              "required": ["chromosome", "position", "ref", "alt"]},
                },
            },
            "required": ["variants"],
        },
    },
    {
        "name": "predict_admet",
        "description": "Predict ADMET properties (MW, LogP, TPSA, HBD/HBA, "
                       "rotatable bonds, Lipinski/Veber/PAINS) for a SMILES string.",
        "input_schema": {
            "type": "object",
            "properties": {"smiles": {"type": "string"}},
            "required": ["smiles"],
        },
    },
    {
        "name": "check_synthesis",
        "description": "Makeability gate for a SMILES string: can we make "
                       "this, how many routes, lead-time (days) and cost "
                       "(USD)? Modeled from named-reaction rules; numbers "
                       "are simulated and stamped. Blocks campaigns at Make.",
        "input_schema": {
            "type": "object",
            "properties": {"smiles": {"type": "string"}},
            "required": ["smiles"],
        },
    },
    {
        "name": "propose_hypothesis",
        "description": "Record a falsifiable hypothesis into the provenance chain. "
                       "Must be called BEFORE the corresponding experiment.",
        "input_schema": {
            "type": "object",
            "properties": {
                "statement": {"type": "string"},
                "candidate": {"type": "string"},
                "assay_type": {"type": "string"},
                "predicted": {"type": "string"},
            },
            "required": ["statement", "candidate", "assay_type", "predicted"],
        },
    },
    {
        "name": "submit_assay",
        "description": "Submit compounds for potency testing through the safety "
                       "gate. Returns a run_id; call check_results later. "
                       "In gate autonomy, submission waits for operator approval.",
        "input_schema": {
            "type": "object",
            "properties": {
                "compounds": {"type": "array", "items": {"type": "string"}},
                "assay_type": {"type": "string",
                               "enum": ["binding", "enzymatic", "cytotoxicity",
                                        "counterscreen"]},
                "concentrations": {"type": "array", "items": {"type": "string"}},
            },
            "required": ["compounds", "assay_type"],
        },
    },
    {
        "name": "check_results",
        "description": "Check if results are ready for a previously submitted "
                       "run. Returns measurements if done, or status if pending.",
        "input_schema": {
            "type": "object",
            "properties": {"run_id": {"type": "string"}},
            "required": ["run_id"],
        },
    },
    {
        "name": "request_analysis",
        "description": "Ask the engine to judge a finished run for a hypothesis. The engine applies the pre-registered "
                       "criteria to the measured data and records the verdict itself. The model cannot supply or edit "
                       "a verdict. Refused when the hypothesis has no registered gate or the run is not its own.",
        "input_schema": {
            "type": "object",
            "properties": {"candidate": {"type": "string"}, "run_id": {"type": "string"}},
            "required": ["candidate", "run_id"],
        },
    },
    {
        "name": "query_tpp",
        "description": "Query the current TPP criteria for a phase. Returns the "
                       "criteria list so you know what thresholds to target.",
        "input_schema": {
            "type": "object",
            "properties": {"phase": {"type": "string",
                                     "enum": ["hit", "lead", "candidate"]}},
        },
    },
]


class AgentToolkit:
    """Builds tool executors bound to live Proef services.

    Usage:
        toolkit = AgentToolkit(lab=SimulatorLab(), store=store, actor=actor)
        schemas, executors = AgentToolkit.schemas(), toolkit.executors()
    """

    def __init__(self, lab_gateway, store, actor, tpp=None,
                 branch_id: str = "agentic-toolkit", gate=None, approved=(), modality=None, autonomy: str = "observe"):
        """`gate`: the program's pre-registered SuccessCriteria (a person fixed it). `approved`: candidates a person
        approved in a plan. Both default to nothing, so the lab tools fail closed until a person supplies them. `autonomy`
        defaults to "observe", which offers and runs no lab tool: a caller has to ask for "gate" or "act" on purpose."""
        self.gate = gate
        self.autonomy = autonomy      # observe (the default): the lab tools are not offered and refuse if named anyway
        self.modality = modality      # the program's modality: which assays are dose-response is the pack's data
        self.approved = set(approved)
        self._hyps: dict[str, Any] = {}
        self._tracker = None
        self.gateway = lab_gateway
        self.store = store
        self.actor = actor
        self.tpp = tpp
        self.branch = branch_id
        self._runs: dict[str, dict] = {}
        self._admet = None
        self._retro = None
        self._variant_scorer = None

    @property
    def admet(self):
        if self._admet is None:
            from proef.adapters.admet import get_admet_predictor
            self._admet = get_admet_predictor()
        return self._admet

    @property
    def variant_scorer(self):
        if self._variant_scorer is None:
            from proef.adapters.genomics.alphagenome import get_variant_scorer
            self._variant_scorer = get_variant_scorer()
        return self._variant_scorer

    @property
    def retro(self):
        if self._retro is None:
            from proef.adapters.retrosynthesis import \
                get_retrosynthesis_adapter
            self._retro = get_retrosynthesis_adapter("rule-based")
        return self._retro

    def executors(self) -> dict[str, Callable]:
        return {name: self._recorded(name, fn) for name, fn in self._raw_executors().items()}

    def tool_schemas(self) -> list[dict]:
        """The tools to offer THIS toolkit's model: the autonomy level is the toolkit's own, not an argument a caller can forget."""
        return self.schemas(self.autonomy)

    def _observe_refusal(self, tool: str) -> dict | None:
        """Under observe a lab tool is refused (and the refusal recorded by the executor wrapper), even if the model names it
        although it was never offered."""
        if self.autonomy == "observe" and tool in self.LAB_TOOLS:
            self._log_refusal(tool, "observe: this autonomy level submits nothing and judges nothing")
            return self._refuse(tool, "observe: this autonomy level submits nothing and judges nothing")
        return None

    def _recorded(self, name: str, fn: Callable) -> Callable:
        """Every tool call is a ledger record: tool, argument hash, result hash, actor. Refusals are recorded too."""
        def call(*args, **kwargs):
            logged = {"args": list(args), **kwargs}
            try:
                out = fn(*args, **kwargs)
            except Exception as exc:  # noqa: BLE001 - recorded, then re-raised to the caller
                self._log(name, logged, {"error": str(exc)[:200]})
                raise
            self._log(name, logged, out)
            return out
        return call

    def _log(self, name: str, args: dict, out: Any) -> None:
        import hashlib
        if self.store is None:
            return

        def h(x: Any) -> str:
            return hashlib.sha256(json.dumps(x, sort_keys=True, default=str).encode()).hexdigest()[:16]

        status = out.get("status") if isinstance(out, dict) else None
        self.store.append(record_for(
            Envelope(experiment_branch_id=self.branch), EventType.tool_invocation, self.actor,
            decision={"kind": "tool_call", "tool": name, "args_hash": h(args), "result_hash": h(out),
                      "status": status, "refused": status in ("refused", "rejected")}))

    def _raw_executors(self) -> dict[str, Callable]:
        return {
            "predict_admet": self._tool_admet,
            "check_synthesis": self._tool_synthesis,
            "propose_hypothesis": self._tool_hypothesis,
            "submit_assay": self._tool_submit,
            "check_results": self._tool_results,
            "request_analysis": self._tool_analysis,
            "query_tpp": self._tool_tpp,
            "rank_variants": self._tool_rank_variants,
        }

    # tools that reach the lab or write a verdict: not offered at all under `observe` ("reads, fits, recommends, submits nothing")
    LAB_TOOLS = frozenset({"submit_assay", "request_analysis"})

    @staticmethod
    def schemas(autonomy: str | None = None) -> list[dict]:
        """The tools a model is offered. Under `observe` the lab tools are not in the list at all, so a model cannot ask for
        what it was never shown (and `executors` refuses them too, in case it names one anyway)."""
        if autonomy == "observe":
            return [t for t in TOOL_SCHEMAS if t["name"] not in AgentToolkit.LAB_TOOLS]
        return TOOL_SCHEMAS

    # ---- implementations ------------------------------------------------

    def _tool_rank_variants(self, variants: list[dict]) -> dict:
        """variants: [{chromosome, position, ref, alt}, ...] — ranked by
        AlphaGenome Atlas AVI score (real API when configured, else seeded
        simulation flagged as such)."""
        scored = self.variant_scorer.rank(variants)
        return {"source": scored[0].source if scored else "none",
                "ranked": [v.summary() for v in scored]}

    def _tool_admet(self, smiles: str) -> dict:
        out = self.admet.predict(smiles).summary()
        from proef.interpret import explain_admet
        out["why"] = explain_admet(out)   # attribution beside every prediction
        return out

    def _tool_synthesis(self, smiles: str) -> dict:
        plans = self.retro.plan_routes(smiles)
        viable = [p for p in plans if p.feasible]
        best = max(plans, key=lambda p: (p.feasible, p.yield_pct))
        return {"smiles": smiles, "makeable": bool(viable),
                "num_routes": len(plans), "viable_routes": len(viable),
                "best_route": best.summary(),
                "routes": [p.summary() for p in plans],
                "answer": (f"{'can make' if viable else 'CANNOT MAKE at acceptable yield/lead-time'}"
                           f" via {len(viable)}/{len(plans)} modeled route(s): "
                           f"{best.lead_time_days} d, ~${best.cost_usd:.0f}"),
                "data_origin": best.data_origin}

    def _refuse(self, tool: str, reason: str, **extra) -> dict:
        return {"status": "refused", "tool": tool, "reason": reason, **extra}

    def _tool_hypothesis(self, statement: str, candidate: str,
                         assay_type: str, predicted: str) -> dict:
        """A model's `predicted` is a prediction. The criterion is the program's gate, never the model's number."""
        if self.gate is None:
            return self._refuse("propose_hypothesis", "no program gate is registered, so a verdict could not be judged "
                                "by a pre-registered rule. A person registers the gate first")
        from proef.autoloop import criteria_for_assay
        from proef.core.hypothesis import HypothesisTracker
        from proef.hypothesis_agent import clean_candidate
        if self._tracker is None:
            self._tracker = HypothesisTracker(self.store, self.actor)
        name = clean_candidate(candidate)
        h = self._tracker.propose(statement=statement, candidate=name, assay_type=assay_type, predicted=predicted,
                                  criteria=criteria_for_assay(assay_type, self.gate, self.modality), branch_id=self.branch)
        self._hyps[name] = h
        return {"hypothesis_id": h.hypothesis_id, "status": "recorded", "candidate": name,
                "gate": {"metric": h.criteria.metric, "threshold": h.criteria.threshold}}

    def _tool_submit(self, compounds: list[str], assay_type: str,
                     concentrations: list[str] | None = None) -> dict:
        """Lab submission. Only candidates a person approved in a plan may go, the ladder is bounded, and the safety
        gateway still checks every submission."""
        from proef.hypothesis_agent import clean_candidate
        refused = self._observe_refusal("submit_assay")
        if refused:
            return refused
        if getattr(self.gateway, "governance", None) is None:
            self._log_refusal("submit_assay", "the gateway has no governance (halt, authorization)")
            return self._refuse("submit_assay", "the lab gateway has no governance bundle, so the halt switch and "
                                "authorization would not apply. Build it with governance=execution_governance()")
        names = [clean_candidate(c) for c in compounds]
        unapproved = [c for c in names if c not in self.approved]
        if not names or unapproved:
            self._log_refusal("submit_assay", f"not in an approved plan: {', '.join(unapproved) or 'no compounds'}")
            return self._refuse("submit_assay", "not in a plan a person approved", candidates=unapproved)
        from proef.protocol_design import sanitize_protocol
        ladder = sanitize_protocol({"concentrations": concentrations or []})
        concs = ladder["concentrations"] if ladder else ["0.5uM", "2uM", "10uM", "40uM"]
        protocol = ProtocolSubmission(
            envelope=Envelope(experiment_branch_id=self.branch),
            title=f"agent assay ({assay_type})",
            assay_type=assay_type,
            steps=[ProtocolStep(seq=1, op=ProtocolOp.dispense, params={"volume_ul": 50})],
            samples=[SampleSpec(sample_id=c, kind=SampleKind.compound, concentration=cc)
                     for c in names for cc in concs]
            + [SampleSpec(sample_id="ctrl-neg", kind=SampleKind.buffer, concentration="10uM")],
            reagents=[ReagentSpec(name="assay-buffer", amount="5 mL", bsl_level=1)],
        )
        run_id, rejection = self.gateway.submit(protocol)
        if rejection:
            return {"status": "rejected", "reason": rejection}
        self._runs[run_id] = protocol
        self.store.append(record_for(
            protocol.envelope, EventType.gate_decision, self.actor,
            decision={"kind": "covered_by_plan", "title": protocol.title, "candidate": ", ".join(names),
                      "reason": "within the plan a person approved"}))
        return {"run_id": run_id, "status": "submitted",
                "compounds": names, "next": "call check_results when ready"}

    def _log_refusal(self, tool: str, reason: str) -> None:
        self.store.append(record_for(
            Envelope(experiment_branch_id=self.branch), EventType.gate_decision, self.actor,
            decision={"kind": "tool_refused", "tool": tool, "reason": reason}))

    def _tool_results(self, run_id: str) -> dict:
        if run_id not in self._runs:
            return {"status": "unknown_run", "run_id": run_id}
        protocol = self._runs[run_id]
        result = self.gateway.results(
            Envelope(experiment_branch_id=self.branch), run_id)
        measurements = [
            {"sample_id": m.sample_id, "value": m.value, "unit": m.unit}
            for m in result.measurements
        ]
        return {"run_id": run_id, "status": result.status.value,
                "z_prime": result.qc.z_prime, "measurements": measurements}

    _TERMINAL = ("validated", "invalidated", "inconclusive", "confirmed", "refuted")

    def _tool_analysis(self, candidate: str, run_id: str) -> dict:
        """The engine judges. The model names a candidate and a run and reads the outcome. It cannot write one, judge a
        hypothesis twice, judge a run that is not finished, or get a verdict from a point estimate."""
        from proef.hypothesis_agent import clean_candidate
        refused = self._observe_refusal("request_analysis")
        if refused:
            return refused
        name = clean_candidate(candidate)
        h = self._hyps.get(name)
        if h is None or h.criteria is None:
            return self._refuse("request_analysis", "no hypothesis with a registered gate for this candidate")
        if h.status in self._TERMINAL:
            # a model could otherwise resubmit an approved candidate and re-judge it until a favourable run appears
            return self._refuse("request_analysis", f"this hypothesis already has a verdict ({h.status}); it is judged once")
        protocol = self._runs.get(run_id)
        if protocol is None or name not in {sp.sample_id for sp in protocol.samples}:
            return self._refuse("request_analysis", "that run is not this candidate's own")
        result = self.gateway.results(Envelope(experiment_branch_id=self.branch), run_id)
        if getattr(result.status, "value", result.status) != "completed":
            return self._refuse("request_analysis", f"the run is not complete ({getattr(result.status, 'value', result.status)})")
        result, source = self._with_interval(h, name, result)
        self._tracker.attach_test(h, protocol, run_id)
        self._tracker.verdict(h, result)
        return {"status": "judged", "candidate": name, "verdict": h.status, "observed": h.observed_value,
                "ci": list(h.observed_ci) if h.observed_ci else None, "interval_source": source, "recorded_by": "engine",
                "gate": {"metric": h.criteria.metric, "threshold": h.criteria.threshold}}

    def _with_interval(self, h, name: str, result):
        """The verdict rule abstains when the 95% interval straddles the gate, and with no interval a point value would be
        judged as if it were certain. So the raw wells are turned into a value WITH an interval here (a Hill fit with a
        bootstrap interval for dose-response, replicate readings for a property), and a run that cannot give one is
        judged with no usable evidence, which abstains."""
        from proef.adapters.lab.simulator import parse_concentration_um
        from proef.fitting import fit_ic50_ci, replicate_ci
        rows = [m for m in result.measurements if m.sample_id == name and "control" not in [f.value for f in m.flags]]
        others = [m for m in result.measurements if m.sample_id != name]

        def conc(m):
            c = m.derived_metrics.get("conc_um")
            return float(c) if isinstance(c, (int, float)) else parse_concentration_um((m.well or "").split("-")[-1])

        def keep(new_rows, source):
            return result.model_copy(update={"measurements": others + new_rows}), source

        if h.criteria.metric == "ic50_um":
            # a four-parameter Hill curve is underdetermined below four distinct doses: no fit, so no interval, so no call
            fit = fit_ic50_ci([(conc(m), m.value) for m in rows]) if len({conc(m) for m in rows}) >= 4 else None
            if not fit:
                return keep([], "none")
            first = rows[0]
            dm = {k: v for k, v in first.derived_metrics.items() if k != "ic50_um"}
            dm.update({"ic50_um": fit["ic50"], "ic50_ci_low": fit["ci_low"], "ic50_ci_high": fit["ci_high"]})
            return keep([first.model_copy(update={"derived_metrics": dm})], "hill fit with bootstrap interval")
        top = max((conc(m) for m in rows), default=None)
        vals = [m.value for m in rows if conc(m) == top]
        ci = replicate_ci(vals)
        if not ci:
            return keep([], "none")       # fewer than 3 readings at the top dose: no measurable spread, so no call
        mean = sum(vals) / len(vals)
        first = next(m for m in rows if conc(m) == top)
        dm = {**first.derived_metrics, h.criteria.metric: round(mean, 4),
              f"{h.criteria.metric}_ci_low": ci[0], f"{h.criteria.metric}_ci_high": ci[1]}
        return keep([first.model_copy(update={"value": round(mean, 4), "derived_metrics": dm})], "replicate readings")

    def _tool_tpp(self, phase: str = "hit") -> dict:
        if not self.tpp:
            return {"error": "no TPP configured"}
        from proef.tpp import CriterionPhase
        criteria = self.tpp.criteria_for_phase(CriterionPhase(phase))
        return [{"name": c.name, "threshold": c.threshold,
                 "direction": c.direction, "unit": c.unit,
                 "required": c.required} for c in criteria]
