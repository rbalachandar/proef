"""The adapters against the REAL services. Opt-in (PROEF_LIVE=1, `make test-live`), because it needs the network and the services' goodwill.

Two jobs: (1) the adapters work end to end today, and (2) the recorded fixtures in tests/fixtures/api still have the shape the services return now,
so API drift is noticed here, in one place, and the fixtures are refreshed with `make record-fixtures`."""
import json
import os
from pathlib import Path

import pytest

pytestmark = [pytest.mark.live, pytest.mark.skipif(os.environ.get("PROEF_LIVE") != "1", reason="live service tests: set PROEF_LIVE=1")]
FX = Path(__file__).parents[1] / "fixtures" / "api"


def _keys(x, depth=2):
    if isinstance(x, dict) and depth:
        return {k: _keys(v, depth - 1) for k, v in x.items()}
    if isinstance(x, list) and x and depth:
        return [_keys(x[0], depth - 1)]
    return type(x).__name__


def test_pubmed_returns_abstracts_for_a_real_query():
    from proef.adapters.databases.pubmed import PubMedAdapter
    rows = PubMedAdapter().search_and_fetch("EGFR T790M resistance", 2)
    assert rows and all(r["pmid"] and r["title"] for r in rows) and any(r["abstract"] for r in rows)


def test_chembl_finds_egfr_and_lists_published_activities_with_structures():
    from proef.adapters.databases.chembl_lookup import ChemblLookup
    c = ChemblLookup()
    assert "CHEMBL203" in {t["target_chembl_id"] for t in c.target_search("EGFR", 10)}
    acts = c.activities("CHEMBL203", 3)
    assert acts and all(a["smiles"] for a in acts)


def test_open_targets_resolves_a_symbol_and_summarises_it():
    from proef.adapters.databases.open_targets import OpenTargetsAdapter
    s = OpenTargetsAdapter().summary(symbol="EGFR")
    assert s["ensembl_id"] == "ENSG00000146648" and s["known_drugs"] and s["top_diseases"]


def test_citations_resolve_against_pubmed_and_chembl_and_a_made_up_one_does_not(tmp_path, monkeypatch):
    from proef import citations
    monkeypatch.setattr(citations, "CACHE", tmp_path / "c.json")
    assert citations.verify_each("PMID 23528858; CHEMBL428690")["status"] == "verified"
    assert citations.verify_each("PMID 99999999")["status"] == "invalid"
    assert citations.verify_each("CHEMBL99999999999")["status"] == "invalid"


def test_the_recorded_fixtures_still_have_the_shape_the_services_return_now():
    """Drift detector: if a service changed its response shape, the offline tests would keep passing on stale recordings. This is what notices."""
    import urllib.parse
    import urllib.request
    drift = []

    def live(url, data=None, headers=None):
        req = urllib.request.Request(url, data=data, headers={"Accept": "application/json", "User-Agent": "proef-live-test", **(headers or {})})
        return json.loads(urllib.request.urlopen(req, timeout=60).read())
    ch = "https://www.ebi.ac.uk/chembl/api/data"
    checks = {
        "chembl_activities.json": live(f"{ch}/activity.json?" + urllib.parse.urlencode({"target_chembl_id": "CHEMBL203", "assay_type": "B", "pchembl_value__isnull": "false", "limit": 3})),
        "chembl_id_lookup_ok.json": live(f"{ch}/chembl_id_lookup/CHEMBL428690.json"),
        "pubmed_esummary.json": live("https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi?id=23528858&version=2.0&db=pubmed&retmode=json"),
    }
    for name, now in checks.items():
        rec = json.loads((FX / name).read_text())
        if set(rec) != set(now) and not set(rec) <= set(now):
            drift.append(f"{name}: top-level keys {sorted(set(rec) ^ set(now))}")
    assert not drift, "the services' responses changed shape; run `make record-fixtures` and fix the adapters: " + "; ".join(drift)
