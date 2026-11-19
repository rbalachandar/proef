"""ChEMBL lookup for a model's knowledge tool: small, read-only questions about a target or a compound (not the bulk dataset download in
`adapters/data/chembl.py`). The fetch is injectable so tests and replays never touch the network."""
from __future__ import annotations

import json
import urllib.parse
import urllib.request
from typing import Any, Callable

BASE = "https://www.ebi.ac.uk/chembl/api/data"


def _http(url: str) -> dict:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
        return json.loads(resp.read())


class ChemblLookup:
    def __init__(self, fetch: Callable[[str], dict] | None = None):
        self._fetch = fetch or _http

    def target_search(self, query: str, limit: int = 5) -> list[dict[str, Any]]:
        d = self._fetch(f"{BASE}/target/search.json?q={urllib.parse.quote(query)}&limit={int(limit)}")
        return [{"target_chembl_id": t.get("target_chembl_id"), "name": t.get("pref_name"), "type": t.get("target_type"),
                 "organism": t.get("organism")} for t in d.get("targets", [])[:limit]]

    def activities(self, target_chembl_id: str, limit: int = 10) -> list[dict[str, Any]]:
        q = urllib.parse.urlencode({"target_chembl_id": target_chembl_id, "assay_type": "B", "pchembl_value__isnull": "false",
                                    "limit": int(limit)})
        d = self._fetch(f"{BASE}/activity.json?{q}")
        return [{"molecule_chembl_id": a.get("molecule_chembl_id"), "molecule_name": a.get("molecule_pref_name"),
                 "smiles": (a.get("canonical_smiles") or "")[:200] or None, "type": a.get("standard_type"), "value": a.get("standard_value"),
                 "units": a.get("standard_units"), "pchembl": a.get("pchembl_value"), "document": a.get("document_chembl_id")}
                for a in d.get("activities", [])[:limit]]
