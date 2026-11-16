"""API v1 response models — the single source of truth for the workbench
contract. The TypeScript client is GENERATED from the OpenAPI these produce."""
from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field, field_validator

State = Literal["confirmed", "validated", "abstained", "refuted", "refused",
                "in_lab", "selected", "not_selected", "proposed"]
VerdictStatus = Literal["confirmed", "validated", "abstained", "refuted"]


# a 2-element [low, high] pair; a plain list keeps the generated TS type simple
Interval = Annotated[list[float], Field(min_length=2, max_length=2)]


class Problem(BaseModel):
    """RFC 7807-style error body."""
    type: str = "about:blank"
    title: str
    detail: str | None = None


class ProgramRef(BaseModel):
    slug: str
    name: str


class Gate(BaseModel):
    """The pre-registered success criterion the campaign is judged against."""
    metric: str | None = None
    direction: Literal["min", "max"] | None = None
    threshold: float | None = None
    unit: str | None = None
    label: str | None = Field(None, description="e.g. 'IC50 (µM) ≤ 2'")


class Budget(BaseModel):
    runs_used: int
    runs_max: int | None = None


class Funnel(BaseModel):
    proposed: int
    selected: int = Field(description="chosen for testing (incl. refused/in lab)")
    tested: int = Field(description="reached the lab")
    judged: int = Field(description="has a verdict")
    claimed: int = Field(description="validated or confirmed")
    refuted: int
    abstained: int = Field(description="interval straddles the gate")


class Verdict(BaseModel):
    status: VerdictStatus
    observed: float | None = None
    ci: Interval | None = None
    unit_label: str | None = None
    margin: str | None = None
    extrapolation: bool = False


class Review(BaseModel):
    """A scientist's recorded decision on a hypothesis (sealed in provenance)."""
    decision: Literal["follow_up", "accept", "drop"]
    note: str = ""
    by: str
    ts: float | None = None
    follow_up_campaign_id: str | None = Field(None, description="the campaign started to follow up")


class HypothesisSummary(BaseModel):
    id: str
    candidate: str | None = None
    family: str | None = Field(None, description="the mechanistic family this hypothesis belongs to")
    head: str
    statement: str
    state: State
    stage: str = Field(description="one-line, human-readable status")
    round: int | None = None
    verdict: Verdict | None = None
    gate_label: str | None = None
    selected: bool = False
    review: Review | None = None


class Pick(BaseModel):
    candidate: str
    reason: str
    score: float | None = Field(None, description="EI value or portfolio score")
    mean: float | None = None
    sd: float | None = None


class Refusal(BaseModel):
    candidate: str
    reason: str


class Why(BaseModel):
    """'Why this plate': how a round's batch was chosen."""
    policy: str | None = None
    policy_version: str | None = None
    k: int | None = None
    target: float | None = None
    direction: str | None = None
    picks: list[Pick] = []
    refusals: list[Refusal] = []
    note: str = ""


class Round(BaseModel):
    n: int
    candidates: list[str]
    verdicts: dict[str, str] = {}
    why: Why | None = None


class ReviewFinding(BaseModel):
    severity: str
    kind: str
    candidate: str | None = None
    detail: str


class Reviewer(BaseModel):
    critical: int = 0
    warnings: int = 0
    findings: list[ReviewFinding] = []


class ChainStatus(BaseModel):
    verified: bool
    records: int
    problems: list[dict[str, Any]] = []


class ActivityItem(BaseModel):
    ts: float | None = None
    kind: str
    text: str
    candidate: str | None = None
    actor: str | None = Field(None, description="who did it (agent / service / person)")
    event: str | None = Field(None, description="the ledger event type, e.g. lab_run, gate_decision")
    hash: str | None = Field(None, description="first characters of the record's hash in the ledger chain")


class CampaignSummary(BaseModel):
    id: str
    title: str
    status: str
    autonomy: str | None = None
    program: ProgramRef | None = None
    updated_at: float | None = None
    problem_id: str | None = None
    paused: bool = Field(False, description="held by a pause on this campaign or on its program")


class PlanVariant(BaseModel):
    hypothesis_id: str
    candidate: str = ""
    statement: str = ""
    predicted: str = ""
    rationale: str = ""
    assay: str = ""
    protocol: str = Field("", description="the dose ladder the model designed, as one line")


class PlanFamily(BaseModel):
    """One mechanistic bet a model proposed, with the variants that test it."""
    id: str
    claim: str = ""
    mechanism: str = ""
    refuted_if: str = Field("", description="the one observation that would kill this family")
    confidence: float | None = None
    citation: str = ""
    citation_status: str = ""
    variants: list[PlanVariant] = []


class PlanNote(BaseModel):
    target: str = "plan"
    severity: Literal["concern", "note"] = "note"
    text: str


class PlanCritique(BaseModel):
    """A second model's review of the plan. Advisory: it cannot change or approve anything."""
    model: str = ""
    notes: list[PlanNote] = []


class PlanWarning(BaseModel):
    """A deterministic check on the plan, shown before approval."""
    kind: Literal["duplicate_compound"]
    candidate: str
    families: list[str] = []
    hypothesis_ids: list[str] = []


class Plan(BaseModel):
    """What a model proposed, shown to a person before anything reaches the lab."""
    warnings: list[PlanWarning] = []
    families: list[PlanFamily] = []
    n_hypotheses: int = 0
    label: str | None = Field(None, description="e.g. 'after round 1' for a re-plan")
    critique: PlanCritique | None = None


class ApprovalRef(BaseModel):
    id: str
    title: str
    detail: str = ""
    ts: float | None = None
    plan: Plan | None = Field(None, description="set when this approval is a research plan to review")


class LlmUse(BaseModel):
    """Where a language model took part. Absent = every step of this campaign was deterministic."""
    models: list[str] = []
    proposed_hypotheses: int = Field(0, description="hypotheses whose wording, mechanism and refuted-if clause a model wrote")
    reviewed: bool = Field(False, description="an LLM reviewer also read the verdicts")


class PlanReview(BaseModel):
    """The person's decision on the model's research plan, as sealed in the ledger."""
    approved: bool
    by: str
    proposed: int
    kept: int
    removed: int = 0
    note: str | None = None
    ts: float | None = None


class CampaignDetail(CampaignSummary):
    pause: PauseState | None = Field(None, description="this campaign is paused")
    paused_by_program: PauseState | None = Field(None, description="its program is paused, which holds it too")
    llm: LlmUse | None = None
    plan_review: PlanReview | None = None
    awaiting_approvals: list[ApprovalRef] = []
    objective: str | None = None
    branch: str | None = None
    gate: Gate = Gate()
    budget: Budget
    funnel: Funnel
    rounds: list[Round] = []
    hypotheses: list[HypothesisSummary] = []
    eliminated: list[Refusal] = []
    reviewer: Reviewer | None = None
    chain: ChainStatus


class HypothesisDetail(HypothesisSummary):
    rationale: str | None = None
    predicted: str | None = None
    predicted_by: dict[str, Any] | None = None
    mechanism: str | None = None
    refutation_condition: str | None = None
    citation: str | None = None
    citation_status: str | None = None
    evidence: dict[str, Any] | None = None
    derivation: str | None = Field(None, description="how the verdict was derived")
    evidence_runs: list[str] = []
    events: list[ActivityItem] = []


class ProvenanceRecord(BaseModel):
    ts: float | None = None
    event: str
    summary: str
    actor: str | None = None
    actor_kind: str | None = None
    model_version: str | None = None
    detail: dict[str, Any] | None = Field(None, description="the recorded decision, verbatim")


class ProvenanceView(BaseModel):
    chain: ChainStatus
    records: list[ProvenanceRecord]


class EvalSummary(BaseModel):
    mean: float
    tasks: int
    all_pass: bool


class AnchorStatus(BaseModel):
    records: int
    age_s: float | None = Field(None, description="seconds since the last anchor")
    published: bool | None = None


class ModelStatus(BaseModel):
    """Is a real language model configured for LLM-assisted campaigns? Never includes the key."""
    configured: bool
    model: str | None = None
    api: str | None = None


class SystemStatus(BaseModel):
    """What the top bar and trust strip show on every page."""
    model: ModelStatus
    halted: bool
    halt_reason: str | None = None
    pending_approvals: int
    chain: ChainStatus
    anchor: AnchorStatus | None = None
    eval: EvalSummary | None = None


class CurvePoint(BaseModel):
    conc_um: float
    value: float


class CurveFit(BaseModel):
    ic50_um: float | None = None
    ci: Interval | None = None
    top: float | None = Field(None, description="known or fitted plateau")
    method: str | None = None
    model: str | None = Field(None, description="what the curve model is (slope/bottom fixed)")
    right_censored: bool = False
    left_censored: bool = False
    line: list[CurvePoint] = Field([], description="the fitted curve, sampled for plotting")


class Curve(BaseModel):
    run_id: str
    label: str
    points: list[CurvePoint]
    fit: CurveFit | None = None
    z_prime: float | None = None
    z_kind: str | None = None
    unit: str | None = None


class CurveSet(BaseModel):
    candidate: str | None = None
    curves: list[Curve] = []
    note: str | None = None


class BestResult(BaseModel):
    campaign_id: str
    campaign_title: str
    candidate: str
    state: str
    observed: float | None = None
    ci: Interval | None = None


class Goal(BaseModel):
    """Progress against the program's pre-registered gate, across ALL its campaigns."""
    met: bool = Field(description="at least one candidate is claimed (validated/confirmed)")
    best: BestResult | None = Field(None, description="best claimed result, else the closest judged one")
    claimed: int = 0
    refuted: int = 0
    abstained: int = 0
    judged: int = 0


class CampaignDigest(BaseModel):
    id: str
    title: str
    status: str
    updated_at: float | None = None
    funnel: Funnel
    best: BestResult | None = None


class Limits(BaseModel):
    """Hard caps a program puts on every campaign it runs. A campaign may ask for less, never more."""
    max_rounds: int = Field(3, ge=1, le=8, description="sequential rounds; each round's results feed the next")
    max_lab_runs: int = Field(20, ge=1, le=500, description="lab runs, including confirmation assays")
    max_hypotheses: int = Field(12, ge=1, le=200, description="hypotheses across all rounds")
    max_families: int = Field(3, ge=1, le=8, description="distinct mechanistic bets a model may propose")
    max_variants_per_family: int = Field(3, ge=1, le=8)
    breadth: int = Field(3, ge=1, le=12, description="hypotheses taken to the lab per round")
    max_depth: int = Field(2, ge=1, le=3, description="how many levels an objective may be split into sub-problems")
    max_subproblems: int = Field(8, ge=2, le=16, description="sub-problems in one research tree, all levels together")


class PauseState(BaseModel):
    scope: str
    kind: Literal["program", "campaign"]
    reason: str = ""
    by: str
    ts: float | None = None


class PauseIn(BaseModel):
    paused: bool
    reason: str = Field("", max_length=300)


class PauseResult(BaseModel):
    paused: bool
    pause: PauseState | None = None


class ProgramSummary(BaseModel):
    pause: PauseState | None = Field(None, description="the program is paused: no new lab submissions until resumed")
    slug: str
    name: str
    objective: str | None = None
    modality: str | None = None
    mode: str | None = None
    limits: Limits = Limits()
    gate: Gate
    goal: Goal
    campaigns: int
    running: int
    updated_at: float | None = None


class ProgramResult(BaseModel):
    hypothesis_id: str
    campaign_id: str
    campaign_title: str
    candidate: str
    state: str
    observed: float | None = None
    ci: Interval | None = None


class NextStep(BaseModel):
    """A rule-based recommendation — deterministic, always available, no model needed."""
    kind: Literal["inbox", "follow_up", "start", "expand", "milestone", "submit_results"]
    title: str
    detail: str = ""
    action: Literal["inbox", "launch", "new_program", "open_campaign"]
    candidates: list[str] = []
    campaign_id: str | None = None


class ProgramDetail(ProgramSummary):
    campaign_list: list[CampaignDigest] = []
    results: list[ProgramResult] = []
    next_steps: list[NextStep] = []


class AbstentionNote(BaseModel):
    campaign_id: str
    campaign_title: str
    candidates: list[str]


class Attention(BaseModel):
    """What needs the scientist right now."""
    pending_approvals: int
    abstentions: list[AbstentionNote]
    running: int
    halted: bool


class Today(BaseModel):
    attention: Attention
    programs: list[ProgramSummary]
    standalone: list[CampaignDigest]


class AgentSummary(BaseModel):
    id: str
    kind: str
    role: str
    model_version: str | None = None
    events: int
    last_ts: float | None = None
    last_text: str = ""


class ReviewIn(BaseModel):
    decision: Literal["follow_up", "accept", "drop"]
    note: str = ""
    follow_up_campaign_id: str | None = None


InboxKind = Literal["approval", "review"]


class InboxItem(BaseModel):
    id: str
    kind: InboxKind
    title: str
    detail: str = ""
    ts: float | None = None
    status: str = "pending"
    decided_by: str | None = None
    campaign_id: str | None = None
    campaign_title: str | None = None
    hypothesis_id: str | None = None
    candidate: str | None = None
    decision: str | None = None
    follow_up_campaign_id: str | None = None
    plan: Plan | None = None


class DecisionIn(BaseModel):
    approve: bool
    note: str = ""
    exclude: list[str] = Field([], description="for a research plan: hypothesis ids the person removed")


class ProgramCreate(BaseModel):
    limits: Limits = Limits()
    name: str
    objective: str = ""
    modality: str = "small molecule"
    metric: str = "ic50_um"
    direction: Literal["min", "max"] = "min"
    gate_value: float = 20.0
    unit: str = "µM"

    @field_validator("unit", "name", "metric", "modality", mode="before")
    @classmethod
    def _tidy(cls, v):
        """A unit typed as 'mS/cm.' must not carry the sentence's full stop into every gate label."""
        return v.strip().rstrip(".").strip() if isinstance(v, str) else v

    @field_validator("modality")
    @classmethod
    def _known_modality(cls, v):
        """An unknown modality must not fall back to small molecule silently: a typo ("materials") would give a
        materials program drug-discovery wording and dose-response rules. Case-insensitive, stored as the pack's name."""
        from proef.modality import PACKS, get_pack
        pack = get_pack(v)
        if pack is None:
            raise ValueError(f"unknown modality {v!r}. Choose one of: {', '.join(PACKS)}")
        return pack.name


class SubProblem(BaseModel):
    """One independently testable question a model split the objective into."""
    objective: str = Field(..., min_length=3, max_length=200)
    metric: str | None = None
    direction: Literal["min", "max"] = "min"
    gate: float | None = None
    unit: str | None = None
    priority: float = 1.0
    rationale: str = ""


class TreeNode(BaseModel):
    """One sub-problem in a research tree. `parent` is another node's id, or null for a direct child of the objective."""
    id: str = Field(..., min_length=1, max_length=40)
    parent: str | None = None
    objective: str = Field(..., min_length=3, max_length=200)
    metric: str | None = None
    direction: Literal["min", "max"] = "min"
    gate: float | None = None
    unit: str | None = None
    priority: float = Field(1.0, gt=0, le=100)
    rationale: str = ""
    depth: int = 1


class Decomposition(BaseModel):
    objective: str
    nodes: list[TreeNode]
    model: str
    max_depth: int
    max_subproblems: int


class TreeCreate(BaseModel):
    objective: str = Field(..., min_length=3, max_length=300)
    nodes: list[TreeNode] = Field(..., min_length=1, max_length=40)
    autonomy: Literal["observe", "gate", "act"] = "gate"
    rounds: int | None = Field(None, ge=1, le=8)


class TreeNodeView(TreeNode):
    status: str = Field("open", description="open | in lab | achieved | partial | failed | inconclusive")
    runs: int | None = Field(None, description="lab runs allocated to this leaf")
    campaign_id: str | None = None
    verdicts: dict[str, str] = {}


class TreeView(BaseModel):
    id: str
    objective: str
    by: str
    ts: float | None = None
    status: str
    nodes: list[TreeNodeView]
    run_budget: int


class CampaignCreate(BaseModel):
    sub_problem: SubProblem | None = Field(None, description="judged against this sub-problem's own gate instead of the program's")
    objective: str | None = None
    candidates: list[str] = []
    mode: Literal["agent", "hand"] = Field(
        "agent", description="'hand': the plate is run by hand and submitted; candidates are required")
    autonomy: Literal["observe", "gate", "act"] = "gate"
    k: int | None = Field(None, ge=1, le=12, description="per round; defaults to, and is capped by, the program's limit")
    rounds: int | None = Field(None, ge=1, le=8, description="defaults to 2, and is capped by the program's limit")
    replan: bool = Field(False, description="after each round the model proposes the next hypotheses from the results; they require approval")


class HaltState(BaseModel):
    halted: bool
    reason: str | None = None
    history: list[dict[str, Any]] = []


class HaltIn(BaseModel):
    halted: bool
    reason: str = ""


class BranchInfo(BaseModel):
    id: str
    kind: Literal["campaign", "program", "other"]
    records: int
    verified: bool
    campaign_id: str | None = None
    campaign_title: str | None = None


class LedgerView(BaseModel):
    chain: ChainStatus
    branches: list[BranchInfo]
    verified_in_ms: int = Field(description="how long the full re-hash took")
    checked_at: float


class AnchorEntry(BaseModel):
    ts: float
    records: int
    merkle_root: str
    published: bool | None = None


class AnchorCheck(BaseModel):
    """The ledger measured against the latest anchor."""
    match: bool = Field(description="everything the anchor committed to is unchanged")
    appended_since_anchor: int
    content_tampered: bool
    current_root: str
    anchored_root: str | None = None


class AnchorView(BaseModel):
    anchors: list[AnchorEntry]
    check: AnchorCheck | None = None
    note: str = ""


class Instrument(BaseModel):
    id: str
    name: str
    kind: str
    vendor: str | None = None
    simulated: bool = Field(description="ALL output is synthetic")
    hardware_validated: bool = Field(description="verified against a physical device")
    installed: bool = True
    requires_network: bool | None = None
    assay_types: list[str] = []
    readouts: list[str] = []
    note: str | None = None


class DriverCheck(BaseModel):
    """What a driver check found. It never changes an instrument's validation status: only verification
    against a physical device does that."""
    id: str
    status: Literal["ready", "not-installed", "needs-configuration", "failed"]
    detail: str
    tools: list[str] = []
    checked_at: float
    simulation_only: bool = Field(True, description="the check ran the driver in simulation mode; no physical device was addressed")


class AuthorityRule(BaseModel):
    column: Literal["autonomous", "human", "never"]
    when: str
    custom: bool = False


class Sop(BaseModel):
    id: str
    objective: str
    assay: str | None = None


class Authority(BaseModel):
    rules: list[AuthorityRule]
    sops_pending: list[Sop]
    halt: HaltState
    safety_rules: str = Field("", description="the toxicophore rule-set version the gate cites")


# ------------------------------------------------------------------ analyze
class AnalysisDiagnostic(BaseModel):
    severity: Literal["info", "warning", "blocker"]
    code: str
    message: str


class MeasurementColumns(BaseModel):
    sample: str | None = None
    value: str | None = None
    candidates: list[str] = []
    all: list[str] = []


class AnalysisPreview(BaseModel):
    """What the system understood from the file, before any fit."""
    kind: Literal["plate", "measurements"] = "plate"
    columns: MeasurementColumns | None = Field(None, description="measurements tables: the columns understood")
    n_measurements: int = 0
    format: str | None = None
    n_wells: int = 0
    n_controls: int = 0
    z_prime: float | None = None
    z_kind: str | None = None
    samples: list[str] = []
    has_doses: bool = False
    evidence_origin: str = "unknown"
    needs_confirmation: list[str] = []
    diagnostics: list[AnalysisDiagnostic] = []


class AnalysisCompound(BaseModel):
    entity: str
    call: Literal["validated", "invalidated", "inconclusive", "no_call"] | None = None
    reason: str | None = None
    ic50_um: float | None = None
    ci: Interval | None = None
    n_points: int | None = None
    estimate: float | None = Field(None, description="measurements: the mean")
    n: int | None = None
    sd: float | None = None
    values: list[float] = []
    top_known: bool | None = Field(None, description="False: the plateau was inferred from the data, so the call depends on that assumption")
    curve: Curve | None = None


class AnalysisResult(BaseModel):
    kind: Literal["plate", "measurements"] = "plate"
    metric: str | None = None
    unit: str | None = None
    method: str | None = None
    state: str
    z_prime: float | None = None
    z_kind: str | None = None
    z_note: str | None = None
    qc_hold: bool = False
    refused: bool = Field(False, description="regime or normalization refusal: no numbers are claimed")
    note: str | None = None
    gate: Gate | None = None
    compounds: list[AnalysisCompound] = []


class AnalysisReviewEntry(BaseModel):
    action: Literal["acknowledge", "comment", "override"]
    reason: str = ""
    target: str = ""
    by: str
    ts: float


class AnalysisAttachment(BaseModel):
    campaign_id: str
    candidate: str | None = None
    by: str
    ts: float


class AnalysisSummary(BaseModel):
    id: str
    filename: str | None = None
    state: str
    updated_at: float | None = None


class AnalysisView(BaseModel):
    profile: str = "plate_assay"
    id: str
    filename: str | None = None
    sha256: str | None = None
    state: str
    revision: int
    preview: AnalysisPreview
    result: AnalysisResult | None = None
    reviews: list[AnalysisReviewEntry] = []
    attachments: list[AnalysisAttachment] = []


class AnalysisRun(BaseModel):
    """What the scientist confirms before the fit. Without a threshold the run
    is estimate-only: intervals are drawn, no verdict is claimed."""
    metric: str | None = Field(None, description="measurements tables: what the values are, e.g. ionic_conductivity_mS_cm")
    unit: str | None = None
    sample_column: str | None = None
    value_column: str | None = None
    threshold: float | None = Field(None, description="the gate: IC50 in the data's concentration unit for a plate; the value itself for a measurements table")
    direction: Literal["min", "max"] = "min"
    max_response: float | None = Field(None, description="known assay ceiling, if the top plateau is known")
    evidence_origin: Literal["measured", "historical", "simulated", "predicted", "unknown"] | None = None


class AnalysisReviewIn(BaseModel):
    action: Literal["acknowledge", "comment", "override"]
    reason: str = Field("", max_length=1000)
    target: str = ""


class AnalysisAttachIn(BaseModel):
    campaign_id: str
    candidate: str | None = None
    note: str = Field("", max_length=500)


# ------------------------------------------------------------ submit results
class ResultMatch(BaseModel):
    hypothesis_id: str
    candidate: str
    statement: str
    gate: str = Field(description="the pre-registered criterion this plate will be judged against")


class ResultsPreview(BaseModel):
    """What submitting this file to the campaign WOULD do. Nothing is written."""
    kind: Literal["plate", "measurements"] = "plate"
    run_id: str
    filename: str
    sha256: str
    n_wells: int
    z_prime: float | None = None
    z_kind: str | None = None
    qc_ok: bool = True
    failure: str | None = Field(None, description="a physical failure mode: no verdicts will be derived")
    matches: list[ResultMatch] = []
    in_file_only: list[str] = Field([], description="samples in the file with no open hypothesis")
    waiting_for_data: list[str] = Field([], description="open hypotheses this file has no data for")
    already_judged: list[str] = Field([], description="candidates that already have a verdict and are left alone")
    already_submitted: bool = False


class ResultVerdict(BaseModel):
    hypothesis_id: str
    candidate: str
    status: str
    observed: float | None = None


class ResultsOutcome(BaseModel):
    run_id: str
    verdicts: list[ResultVerdict] = []
    failure: str | None = None
    messages: list[str] = []
