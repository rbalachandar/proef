"""The dose ladder a model may design for one hypothesis — validated, bounded, never trusted as-is.

A model may propose WHICH concentrations to test (a ladder that brackets the gate resolves a potency
curve far better than a default one). It cannot propose anything the lab would not accept: the ladder is
cleaned here (finite, positive, de-duplicated, sorted, in range, between MIN and MAX doses) and anything
that fails is dropped, so the campaign falls back to the default ladder instead of running a bad one.
Replicates are a property of the lab (the instrument runs them), not a model choice, so they are not here.
"""
from __future__ import annotations

import math
from typing import Any

MIN_DOSES, MAX_DOSES = 3, 8
LOW_UM, HIGH_UM = 0.001, 1000.0


def sanitize_protocol(raw: Any) -> dict | None:
    """{"concentrations": ["0.5uM", ...], "why": str} or None when the proposal is unusable."""
    if not isinstance(raw, dict):
        return None
    vals: list[float] = []
    for v in raw.get("concentrations_uM") or raw.get("concentrations") or []:
        try:
            x = float(str(v).lower().replace("um", "").replace("µm", "").strip())
        except (TypeError, ValueError):
            continue
        if math.isfinite(x) and LOW_UM <= x <= HIGH_UM:
            vals.append(x)
    ladder = sorted(set(round(x, 6) for x in vals))
    if len(ladder) < MIN_DOSES:
        return None
    ladder = ladder[:MAX_DOSES]
    return {"concentrations": [f"{x:g}uM" for x in ladder], "why": str(raw.get("why") or raw.get("rationale") or "")[:200]}


def describe(protocol: dict | None) -> str:
    """One line for the plan the person reviews."""
    if not protocol:
        return ""
    c = protocol["concentrations"]
    return f"{len(c)} doses, {c[0].replace('uM', '')} to {c[-1].replace('uM', '')} µM" + (f": {protocol['why']}" if protocol.get("why") else "")
