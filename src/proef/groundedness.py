"""Is a claim about outside information backed by something this run actually retrieved?

A model that cites a paper or a database row can cite a real one it never looked at, or an invented one. The engine cannot tell a true
claim from a false one, but it can tell whether the identifier the claim rests on appears in a tool result of THIS run. That is cheap,
mechanical and needs no network. A claim that does not trace is not shown as evidence, and a citation that does not trace is flagged
(it may still be real: a model's memory is not worthless, it is just not what was read).
"""
from __future__ import annotations

import json
import re
from typing import Any

# the identifiers a claim can rest on: PubMed ids, ChEMBL ids, Ensembl gene ids, DOIs
_ID = re.compile(r"CHEMBL\d+|ENSG\d{6,}|10\.\d{4,9}/[^\s;,]+|(?<![\w.])\d{6,9}(?![\w.])", re.I)
KINDS = ("supports", "contradicts", "prior_measurement", "already_known")


def ids_in(text: Any) -> list[str]:
    """The identifiers in a citation or source string, in order, without duplicates."""
    return list(dict.fromkeys(m.group(0).rstrip(".)") for m in _ID.finditer(str(text or ""))))


def haystack(bodies: list[Any]) -> str:
    return json.dumps(bodies, default=str).lower()


def traces(source: Any, bodies: list[Any]) -> bool:
    """True when at least one identifier in `source` appears in what the tools returned. A source with no identifier cannot trace."""
    ids = ids_in(source)
    hay = haystack(bodies)
    return bool(ids) and any(i.lower() in hay for i in ids)


def verify_items(items: Any, bodies: list[Any], max_items: int = 8) -> tuple[list[dict], list[dict]]:
    """Split a model's grounding items into (kept, dropped). Kept: a known kind, a claim, and a source that traces to a result of
    this run. Dropped carry the reason, so a person can see what was refused and why. Text is bounded."""
    kept: list[dict] = []
    dropped: list[dict] = []
    for it in (items if isinstance(items, list) else [])[: max_items * 2]:
        if not isinstance(it, dict):
            dropped.append({"reason": "not an object"})
            continue
        kind, claim, source = str(it.get("kind") or "")[:20], str(it.get("claim") or "")[:300], str(it.get("source") or "")[:120]
        if kind not in KINDS:
            dropped.append({"kind": kind, "claim": claim, "reason": f"kind must be one of {', '.join(KINDS)}"})
        elif not claim:
            dropped.append({"kind": kind, "reason": "no claim"})
        elif not traces(source, bodies):
            dropped.append({"kind": kind, "claim": claim, "source": source, "reason": "its source is not in any result this run retrieved"})
        elif len(kept) < max_items:
            kept.append({"kind": kind, "claim": claim, "source": source})
    return kept, dropped
