"""/api/v1/lab, what hardware exists (real vs simulated) and what the agents may do."""
from __future__ import annotations

import time

from fastapi import APIRouter, HTTPException, Request

from proef.api.v1 import schemas as S
from proef.api.v1.deps import who
from proef.api.v1.governance import _state as halt_state

router = APIRouter(prefix="/lab", tags=["lab"])


@router.get("/instruments", response_model=list[S.Instrument])
def instruments() -> list[S.Instrument]:
    from proef.labselect import available_instruments
    out = []
    for i in available_instruments():
        out.append(S.Instrument(
            id=i["id"], name=i.get("name") or i["id"], kind=i.get("kind", "?"),
            vendor=i.get("vendor"), simulated=bool(i.get("simulated")),
            hardware_validated=bool(i.get("hardware_validated")),
            installed=bool(i.get("installed", True)),
            requires_network=i.get("requires_network"),
            assay_types=list(i.get("assay_types") or []),
            readouts=list(i.get("readouts") or []), note=i.get("note")))
    # honest ordering: validated hardware first, then real-but-unvalidated, then simulated
    return sorted(out, key=lambda x: (not x.hardware_validated, x.simulated, x.name))


def _check(i: dict) -> S.DriverCheck:
    """Does this instrument's driver start and answer its handshake HERE? Run in simulation mode, so
    the check never moves hardware; which device a campaign addresses is configured in its `labs` setting."""
    import importlib
    import sys

    from proef.labselect import _catalog_entry, _config_args, _resolve_cmd
    cat = _catalog_entry(i["id"])
    ok = lambda detail, tools=(): S.DriverCheck(id=i["id"], status="ready", detail=detail, tools=list(tools), checked_at=time.time())  # noqa: E731
    if cat["kind"] == "simulator":
        return ok("Built in. It always answers, and every result it produces is synthetic.")
    if not i.get("installed"):
        exe = cat.get("availability_executable") or (cat.get("adapter") or "").rsplit(":", 1)[0]
        return S.DriverCheck(id=i["id"], status="not-installed", checked_at=time.time(),
                             detail=f"The driver ({exe}) is not installed on this machine.")
    if cat["kind"] == "mcp":
        from proef.mcp_instrument import McpLabAdapter
        cmd = _resolve_cmd([c.replace("{python}", sys.executable) for c in cat["command"]])
        has_sim = any(f.get("key") == "simulate" for f in cat.get("config_schema") or [])
        cmd += _config_args(cat, {"simulate": True} if has_sim else {})
        try:
            lab = McpLabAdapter(command=cmd, timeout=10.0, profile=dict(cat["profile"]))
        except Exception as exc:  # noqa: BLE001 - any failure to start or speak MCP is a failed check
            return S.DriverCheck(id=i["id"], status="failed", checked_at=time.time(), detail=f"{type(exc).__name__}: {exc}"[:300])
        try:
            return ok("The driver started and answered its handshake.", [t.get("name", "?") for t in lab._tools])
        finally:
            lab.close()
    try:
        importlib.import_module(cat["adapter"].rsplit(":", 1)[0])
    except Exception as exc:  # noqa: BLE001
        return S.DriverCheck(id=i["id"], status="failed", checked_at=time.time(), detail=f"{type(exc).__name__}: {exc}"[:300])
    if cat.get("requires_network"):
        return S.DriverCheck(id=i["id"], status="needs-configuration", checked_at=time.time(),
                             detail="The driver loads. Reaching the device needs its address, which is set in a campaign's "
                                    "`labs` configuration; this check does not touch the network.")
    return ok("The driver loads.")


@router.post("/instruments/{instrument_id}/check", response_model=S.DriverCheck)
def check_instrument(instrument_id: str, request: Request) -> S.DriverCheck:
    """Check a driver, on request. Recorded as an action by the person; it can never mark an instrument validated."""
    from proef.labselect import available_instruments
    i = next((x for x in available_instruments() if x["id"] == instrument_id), None)
    if i is None:
        raise HTTPException(404, S.Problem(type="instrument-not-found", title="Instrument not found", detail=instrument_id).model_dump())
    res = _check(i)
    from proef.adapters.store.sqlite import get_store
    from proef.core.contracts import Actor, ActorKind, Envelope, EventType
    from proef.core.interfaces import record_for
    from proef.views import ids as _ids
    get_store(str(_ids.PROVENANCE_DB)).append(record_for(
        Envelope(experiment_branch_id=f"lab/{instrument_id}"), EventType.gate_decision,
        Actor(kind=ActorKind.human, id=who(request)),
        decision={"kind": "driver_check", "instrument": instrument_id, "status": res.status, "detail": res.detail,
                  "simulation_only": True}))
    return res


@router.get("/authority", response_model=S.Authority)
def authority() -> S.Authority:
    from proef.adapters.bio.tox_alerts import RULES_VERSION
    from proef.governance import AuthorizationMatrix, SopRegistry
    return S.Authority(
        rules=[S.AuthorityRule(**r) for r in AuthorizationMatrix().describe()],
        sops_pending=[S.Sop(id=p["id"], objective=p.get("objective", ""),
                            assay=p.get("assay")) for p in SopRegistry().pending()],
        halt=halt_state(), safety_rules=str(RULES_VERSION))


@router.post("/sops/{sop_id}/approve", response_model=S.Authority)
def approve_sop(sop_id: str, request: Request) -> S.Authority:
    """Sign off a recurring procedure: from now on it may run unattended.
    Attributed to the authenticated person."""
    from proef.governance import SopRegistry
    if not SopRegistry().approve(sop_id, by=who(request)):
        raise HTTPException(404, S.Problem(
            type="sop-not-found", title="SOP not found", detail=sop_id).model_dump())
    return authority()
