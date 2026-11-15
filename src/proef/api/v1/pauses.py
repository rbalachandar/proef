"""/api/v1/.../pause: a routine, scoped pause of one program or one campaign.

Not the emergency stop (that is /halt, global, and refuses). A pause HOLDS the scope's lab submissions at the gateway
and releases them on resume, so a campaign never loses rounds to it. Running plates finish. Pausing and resuming are
attributed to the authenticated person and sealed in the ledger like every other human decision."""
from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request

from proef import program as program_cfg
from proef.api.v1 import schemas as S
from proef.api.v1.campaigns import _load, problem_id_for
from proef.api.v1.deps import who
from proef.governance import PauseRegistry

router = APIRouter(tags=["governance"])


def pause_state(entry: dict | None) -> S.PauseState | None:
    if not entry:
        return None
    scope = str(entry.get("scope", ""))
    return S.PauseState(scope=scope, kind="program" if scope.startswith("program:") else "campaign",
                        reason=entry.get("reason") or "", by=entry.get("by") or "someone", ts=entry.get("ts"))


def pauses_for(problem_id: str | None, slug: str | None) -> tuple[S.PauseState | None, S.PauseState | None]:
    """(this campaign's own pause, the pause on its program) for the detail views."""
    act = PauseRegistry().active()
    own = act.get(f"campaign:{problem_id}") if problem_id else None
    prog = act.get(f"program:{slug}") if slug else None
    return pause_state(own), pause_state(prog)


def _seal(branch: str, user: str, decision: dict) -> None:
    from proef.adapters.store.sqlite import get_store
    from proef.core.contracts import Actor, ActorKind, Envelope, EventType
    from proef.core.interfaces import record_for
    from proef.views import ids as _ids
    get_store(str(_ids.PROVENANCE_DB)).append(record_for(
        Envelope(experiment_branch_id=branch), EventType.gate_decision, Actor(kind=ActorKind.human, id=user), decision=decision))


def _apply(scope: str, branch: str, body: S.PauseIn, user: str) -> S.PauseResult:
    reg = PauseRegistry()
    if body.paused:
        entry = reg.pause(scope, body.reason.strip(), user)
        _seal(branch, user, {"kind": "pause", "scope": scope, "reason": entry.get("reason", ""),
                             "note": "new lab submissions are held until resumed; running plates finish"})
    else:
        if reg.resume(scope, user):
            _seal(branch, user, {"kind": "resume", "scope": scope})
    return S.PauseResult(paused=scope in reg.active(), pause=pause_state(reg.active().get(scope)))


@router.get("/pauses", response_model=list[S.PauseState])
def list_pauses() -> list[S.PauseState]:
    return [s for s in (pause_state(e) for e in PauseRegistry().active().values()) if s]


@router.post("/programs/{slug}/pause", response_model=S.PauseResult)
def pause_program(slug: str, body: S.PauseIn, request: Request) -> S.PauseResult:
    if not any(p["slug"] == slug for p in program_cfg.list_programs()):
        raise HTTPException(404, S.Problem(type="program-not-found", title="Program not found", detail=slug).model_dump())
    return _apply(f"program:{slug}", f"programs/{slug}", body, who(request))


@router.post("/campaigns/{eid}/pause", response_model=S.PauseResult)
def pause_campaign(eid: str, body: S.PauseIn, request: Request) -> S.PauseResult:
    _, _, detail, branch, _ = _load(eid)
    pid = problem_id_for(eid, detail)
    if not pid:
        raise HTTPException(409, S.Problem(
            type="not-started", title="This campaign has not started yet",
            detail="It can be paused once it has a record, which happens as soon as it begins.").model_dump())
    # a RUNNING campaign is not linked in the registry yet (no branch), but it has announced its problem id
    return _apply(f"campaign:{pid}", branch or f"problem/{pid}", body, who(request))
