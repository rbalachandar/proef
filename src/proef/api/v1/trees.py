"""/api/v1/programs/{slug}/decompose and /trees, a research tree: an objective split into sub-problems.

A model PROPOSES the split (nothing is recorded). The person keeps the sub-problems that are real questions and
may change each gate; starting the tree seals their decision in the ledger and launches one budgeted campaign per
leaf. Every runaway guard is the engine's (`problem_tree`): depth, children per node, total nodes, and a run budget
that flows DOWN the tree so a leaf can never spend more than its share. The roll-up (a node is achieved only when
its required children are) is computed from the leaves' recorded verdicts each time it is read.
"""
from __future__ import annotations

import time
import uuid

from fastapi import APIRouter, HTTPException, Request

from proef import program as program_cfg
from proef.api.launch import spawn_campaign
from proef.api.v1 import schemas as S
from proef.api.v1.deps import who
from proef.api.v1.programs import _digest, _gate, _limits, campaign_doc
from proef.problem_tree import ProblemTree, TreeLimits

MIN_MODEL_CALLS_PER_LEAF = 3        # a proposal, a critic note and a review is the least a leaf campaign uses

router = APIRouter(tags=["programs"])

_RUNNING = ("running", "executing", "awaiting_results")
_MAX_CHILDREN = 4


def _program_or_404(slug: str) -> dict:
    p = next((x for x in program_cfg.list_programs() if x["slug"] == slug), None)
    if p is None:
        raise HTTPException(404, S.Problem(type="program-not-found", title="Program not found", detail=slug).model_dump())
    return p


def _tree_limits(lim: S.Limits) -> TreeLimits:
    return TreeLimits(max_depth=lim.max_depth, max_children=_MAX_CHILDREN, max_nodes=lim.max_subproblems + 1)


def _nodes_of(tree: ProblemTree, alloc: dict[str, int] | None = None) -> list[S.TreeNode]:
    """Depth-first, parents before children; the root (the objective itself) is not a node."""
    out: list[S.TreeNode] = []

    def walk(nid: str) -> None:
        for k in sorted(tree.children(nid), key=lambda n: n.id):
            out.append(S.TreeNode(id=k.id, parent=None if k.parent == tree.root.id else k.parent,
                                  objective=k.objective, metric=k.metric, direction=k.direction, gate=k.gate,
                                  priority=k.priority, rationale=k.rationale, depth=tree.depth_of(k.id)))
            walk(k.id)
    walk(tree.root.id)
    return out


def _build(objective: str, nodes: list[S.TreeNode], lim: S.Limits) -> ProblemTree:
    """Rebuild a tree from what the client sent, through the engine's own guardrails: whatever the browser
    claims, a tree deeper or wider than the program's limits is refused."""
    tree = ProblemTree.create(objective, limits=_tree_limits(lim), run_budget=lim.max_lab_runs)
    ids = {n.id for n in nodes}
    if len(ids) != len(nodes):
        raise HTTPException(422, S.Problem(type="bad-tree", title="Duplicate sub-problem ids").model_dump())
    pending = list(nodes)
    placed = {None}
    while pending:
        ready = [n for n in pending if n.parent in placed]
        if not ready:
            raise HTTPException(422, S.Problem(type="bad-tree", title="A sub-problem points at a parent that is not in the tree",
                                               detail=", ".join(n.id for n in pending)).model_dump())
        for n in ready:
            try:
                tree.add(tree.root.id if n.parent is None else n.parent, id=n.id, objective=n.objective, metric=n.metric,
                         direction=n.direction, gate=n.gate, priority=n.priority, rationale=n.rationale)
            except ValueError as exc:      # depth / breadth / node caps
                raise HTTPException(422, S.Problem(type="tree-over-limit", title="This tree exceeds the program's limits",
                                                   detail=str(exc)).model_dump()) from exc
            placed.add(n.id)
            pending.remove(n)
    return tree


@router.post("/programs/{slug}/decompose", response_model=S.Decomposition)
def decompose_program(slug: str) -> S.Decomposition:
    """A model proposes how to split the program's objective (recursively, within the program's depth and size
    limits). A proposal only: nothing is recorded or run until the person starts the tree."""
    p = _program_or_404(slug)
    from proef.adapters.models.from_env import model_from_env, model_status
    if not model_status()["configured"]:
        raise HTTPException(409, S.Problem(type="model-not-configured", title="No model is configured",
                                           detail="Splitting an objective needs a model. See docs/MODELS.md.").model_dump())
    from proef.problem_tree import decompose_with_agent
    doc = p.get("program") or {}
    lim = _limits(doc)
    objective = doc.get("objective") or p["name"]
    try:
        tree = decompose_with_agent(model_from_env(), objective, context=f"program gate: {_gate(doc).label}",
                                    limits=_tree_limits(lim))
    except Exception as exc:  # noqa: BLE001 - the model's reply is untrusted
        raise HTTPException(502, S.Problem(type="decomposition-failed", title="The model's split could not be read",
                                           detail=f"{type(exc).__name__}: {exc}"[:300]).model_dump()) from exc
    nodes = [n for n in _nodes_of(tree) if n.objective.strip()]
    return S.Decomposition(objective=objective, nodes=nodes, model=model_status()["model"],
                           max_depth=lim.max_depth, max_subproblems=lim.max_subproblems)


@router.post("/programs/{slug}/trees", response_model=S.TreeView, status_code=201)
def start_tree(slug: str, body: S.TreeCreate, request: Request) -> S.TreeView:
    p = _program_or_404(slug)
    lim = _limits(p.get("program") or {})
    tree = _build(body.objective, body.nodes, lim)
    alloc = tree.allocation()
    leaves = tree.leaves() if len(tree.nodes) > 1 else []
    if not leaves:
        raise HTTPException(422, S.Problem(type="bad-tree", title="Keep at least one sub-problem").model_dump())
    from proef.adapters.models.from_env import model_status
    if not model_status()["configured"]:
        raise HTTPException(409, S.Problem(type="model-not-configured", title="No model is configured",
                                           detail="Each sub-problem is researched by a model.").model_dump())
    if lim.max_model_calls // len(leaves) < MIN_MODEL_CALLS_PER_LEAF:
        # a floor here would let the leaves together exceed the program's cap, so the tree is refused instead
        raise HTTPException(422, S.Problem(
            type="model-call-cap-too-small", title="Not enough model calls for this many sub-problems",
            detail=f"This program allows {lim.max_model_calls} model calls: {lim.max_model_calls // len(leaves)} each for "
                   f"{len(leaves)} sub-problems, and each needs at least {MIN_MODEL_CALLS_PER_LEAF}. Keep fewer sub-problems "
                   "or raise the program's model-call limit.").model_dump())
    by = who(request)
    tree_id = f"tree-{uuid.uuid4().hex[:8]}"
    share_h = max(1, lim.max_hypotheses // len(leaves))
    share_m = lim.max_model_calls // len(leaves)               # model calls are shared across leaves like lab runs, never rounded up
    campaigns: dict[str, str] = {}
    failed: list[str] = []
    for leaf in leaves:
        c = S.CampaignCreate(
            sub_problem=S.SubProblem(objective=leaf.objective, metric=leaf.metric, direction=leaf.direction, gate=leaf.gate,
                                     priority=leaf.priority),
            autonomy=body.autonomy, rounds=body.rounds, replan=False)
        cid = spawn_campaign(campaign_doc(p, c, runs=alloc.get(leaf.id), hypotheses=share_h, model_calls=share_m), p["name"])
        (campaigns.__setitem__(leaf.id, cid) if cid else failed.append(leaf.id))
    from proef.adapters.store.sqlite import get_store
    from proef.core.contracts import Actor, ActorKind, Envelope, EventType
    from proef.core.interfaces import record_for
    from proef.views import ids as _ids
    get_store(str(_ids.PROVENANCE_DB)).append(record_for(
        Envelope(experiment_branch_id=f"programs/{slug}"), EventType.gate_decision,
        Actor(kind=ActorKind.human, id=by),
        decision={"kind": "research_tree", "tree_id": tree_id, "objective": body.objective, "by": by,
                  "nodes": [n.model_dump() for n in _nodes_of(tree)], "campaigns": campaigns,
                  "allocation": alloc, "run_budget": lim.max_lab_runs,
                  "limits": {"max_depth": lim.max_depth, "max_subproblems": lim.max_subproblems}}))
    if failed:
        raise HTTPException(500, S.Problem(type="launch-failed", title="Some sub-problems did not start",
                                           detail=f"{len(campaigns)} started, {len(failed)} did not").model_dump())
    return _view(tree_id, body.objective, by, time.time(), tree, campaigns, alloc)


def _leaf_statuses(cid: str) -> tuple[str, dict[str, str]]:
    """(running?|done, {candidate: verdict}) for one leaf's campaign."""
    try:
        dg, hyps = _digest(cid)
    except HTTPException:
        return "gone", {}
    if dg.status in _RUNNING:
        return "running", {}
    verdicts = {(h.candidate or h.head): h.state for h in hyps
                if h.state in ("confirmed", "validated", "refuted", "abstained")}
    return "done", verdicts


def _view(tree_id, objective, by, ts, tree: ProblemTree, campaigns: dict[str, str], alloc: dict[str, int]) -> S.TreeView:
    pending: set[str] = set()      # leaves still being worked on, and everything above them
    for leaf_id, cid in campaigns.items():
        state, verdicts = _leaf_statuses(cid)
        if state == "done":
            tree.set_leaf_status(leaf_id, verdicts)
        else:                       # running, or not registered yet (just launched)
            nid = leaf_id
            while nid:
                pending.add(nid)
                nid = tree.nodes[nid].parent
    roll = tree.rollup()
    nodes = [S.TreeNodeView(**n.model_dump(), status="in lab" if n.id in pending else roll.get(n.id, "open"),
                            runs=alloc.get(n.id), campaign_id=campaigns.get(n.id), verdicts=tree.nodes[n.id].verdicts)
             for n in _nodes_of(tree)]
    return S.TreeView(id=tree_id, objective=objective, by=by, ts=ts,
                      status="in lab" if tree.root.id in pending else roll.get(tree.root.id, "open"),
                      nodes=nodes, run_budget=tree.run_budget)


@router.get("/programs/{slug}/trees", response_model=list[S.TreeView])
def list_trees(slug: str) -> list[S.TreeView]:
    _program_or_404(slug)
    from proef.adapters.store.sqlite import get_store
    from proef.views import ids as _ids
    lim = _limits(_program_or_404(slug).get("program") or {})
    out: list[S.TreeView] = []
    for r in get_store(str(_ids.PROVENANCE_DB)).branch_records(f"programs/{slug}"):
        d = r.decision or {}
        if d.get("kind") != "research_tree":
            continue
        try:
            tree = _build(d["objective"], [S.TreeNode(**n) for n in d["nodes"]],
                          lim.model_copy(update={"max_depth": max(lim.max_depth, d["limits"]["max_depth"]),
                                                 "max_subproblems": max(lim.max_subproblems, d["limits"]["max_subproblems"]),
                                                 "max_lab_runs": d.get("run_budget", lim.max_lab_runs)}))
        except HTTPException:
            continue
        out.append(_view(d["tree_id"], d["objective"], d.get("by", "someone"),
                         r.ts.timestamp() if getattr(r, "ts", None) else None,
                         tree, d.get("campaigns", {}), {k: int(v) for k, v in (d.get("allocation") or {}).items()}))
    return list(reversed(out))
