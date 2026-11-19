"""Open Targets adapter — target identification and validation queries.

Queries the Open Targets GraphQL API for target-disease associations,
tractability, and genetic evidence. Used by the Target Research Agent.
"""
from __future__ import annotations

import json
import urllib.request
from typing import Any

GRAPHQL_URL = "https://api.platform.opentargets.org/api/v4/graphql"

QUERY = """
query targetDetails($ensemblId: String!) {
  target(ensemblId: $ensemblId) {
    id
    approvedSymbol
    approvedName
    biotype
    tractability { label modality value }
    geneticConstraint { constraintType score oe }
    drugAndClinicalCandidates { count }
    associatedDiseases(page: {index: 0, size: 10}) { rows { score disease { id name } } }
  }
}
"""

SEARCH = """
query findTarget($q: String!) {
  search(queryString: $q, entityNames: ["target"], page: {index: 0, size: 5}) { hits { id name entity } }
}
"""


class OpenTargetsAdapter:
    """Adapter for Open Targets Platform GraphQL API (stdlib only)."""

    name = "open-targets"

    def __init__(self, base_url: str = GRAPHQL_URL):
        self.base_url = base_url

    def _graphql(self, query: str, variables: dict) -> dict:
        body = json.dumps({"query": query, "variables": variables}).encode()
        req = urllib.request.Request(
            self.base_url, data=body,
            headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
            data = json.loads(resp.read())
        if "errors" in data:
            raise RuntimeError(f"OpenTargets GraphQL error: {data['errors']}")
        return data.get("data", {})

    def target_by_ensembl(self, ensembl_id: str) -> dict[str, Any]:
        """Fetch target details, tractability, and disease associations."""
        data = self._graphql(QUERY, {"ensemblId": ensembl_id})
        return data.get("target", {})

    def find_target(self, symbol: str) -> str | None:
        """The Ensembl id of the target whose name is exactly this symbol (case-insensitive), else None."""
        hits = self._graphql(SEARCH, {"q": symbol}).get("search", {}).get("hits", [])
        return next((h["id"] for h in hits if str(h.get("name", "")).lower() == symbol.strip().lower()), None)

    def summary(self, ensembl_id: str | None = None, symbol: str | None = None) -> dict[str, Any]:
        """Compact summary for agent consumption. Give an Ensembl gene id, or a gene symbol to look up first."""
        if not ensembl_id and symbol:
            ensembl_id = self.find_target(symbol)
            if not ensembl_id:
                return {"error": f"no Open Targets target named {symbol!r}"}
        if not ensembl_id:
            return {"error": "give an ensembl_id or a symbol"}
        t = self.target_by_ensembl(ensembl_id)
        if not t:
            return {"error": "target not found"}
        diseases = [{"name": d["disease"]["name"], "score": round(d["score"], 3)}
                    for d in (t.get("associatedDiseases") or {}).get("rows", [])[:10]]
        tractable = sorted({f"{x['modality']}: {x['label']}" for x in t.get("tractability") or [] if x.get("value")})
        return {
            "ensembl_id": t.get("id"),
            "symbol": t.get("approvedSymbol"),
            "name": t.get("approvedName"),
            "biotype": t.get("biotype"),
            "known_drugs": (t.get("drugAndClinicalCandidates") or {}).get("count"),
            "genetic_constraint": t.get("geneticConstraint"),
            "tractability_modalities": tractable,
            "top_diseases": diseases,
        }


def validate_target(ensembl_id: str) -> dict[str, Any]:
    """Standalone helper: is this target druggable and genetically validated?"""
    adapter = OpenTargetsAdapter()
    s = adapter.summary(ensembl_id)
    if "error" in s:
        return s
    return {
        "symbol": s.get("symbol"),
        "known_drugs": s.get("known_drugs", 0),
        "tractability": s.get("tractability_modalities", []),
        "top_disease_score": (s.get("top_diseases") or [{}])[0].get("score", 0),
    }
