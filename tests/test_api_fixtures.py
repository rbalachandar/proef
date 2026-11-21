"""The adapters against REAL recorded responses (tests/fixtures/api/, refreshed by scripts/record_api_fixtures.py), offline.

Scripted tests cannot notice that an outside API differs from what an adapter assumes. Three bugs came from exactly that, found only by a real run:
the PubMed adapter asked efetch for JSON (efetch has no JSON mode), the Open Targets query used fields that had been retired, and a ChEMBL id was
resolved as a PMID. Each of these tests would have failed on the day the adapter was written."""
import json
import urllib.error
from pathlib import Path

import pytest

from proef import citations
from proef.adapters.databases.chembl_lookup import ChemblLookup
from proef.adapters.databases.open_targets import QUERY, SEARCH, OpenTargetsAdapter
from proef.adapters.databases.pubmed import PubMedAdapter, parse_efetch

FX = Path(__file__).parent / "fixtures" / "api"


def raw(name: str) -> bytes:
    return (FX / name).read_bytes()


def js(name: str):
    return json.loads(raw(name))


# ---- PubMed ----

def test_pubmed_search_and_fetch_parse_the_real_esearch_and_efetch_payloads(monkeypatch):
    ids = js("pubmed_esearch.json")["esearchresult"]["idlist"]
    a = PubMedAdapter(delay_secs=0)
    seen = []

    def fake_get(endpoint, params):
        seen.append((endpoint, dict(params)))
        return raw("pubmed_esearch.json") if endpoint == "esearch.fcgi" else raw("pubmed_efetch.xml")
    monkeypatch.setattr(a, "_get", fake_get)
    assert a.search("EGFR T790M resistance", 3) == ids
    rows = a.search_and_fetch("EGFR T790M resistance", 3)
    assert rows and all(r["pmid"] and r["title"] for r in rows) and any(r["abstract"] for r in rows)
    fetch_call = next(p for e, p in seen if e == "efetch.fcgi")
    assert fetch_call["retmode"] == "xml", "efetch has no JSON mode: asking for it returned nothing usable and the adapter never worked live"


def test_the_real_efetch_xml_parses_and_an_entity_declaration_is_still_refused():
    arts = parse_efetch(raw("pubmed_efetch.xml"))
    assert len(arts) >= 1 and all(a["pmid"].isdigit() and a["title"] for a in arts)
    with pytest.raises(ValueError):
        parse_efetch(b'<!DOCTYPE x [<!ENTITY a "b">]><PubmedArticleSet/>')


# ---- ChEMBL ----

def test_chembl_target_search_and_activities_parse_the_real_payloads():
    def fetch(url):
        return js("chembl_target_search.json") if "target/search" in url else js("chembl_activities.json")
    c = ChemblLookup(fetch=fetch)
    t = c.target_search("EGFR", 3)
    assert t and all(x["target_chembl_id"].startswith("CHEMBL") for x in t) and any("EGFR" in str(x["name"]).upper() or "epidermal" in str(x["name"]).lower() for x in t)
    acts = c.activities("CHEMBL203", 3)
    assert acts and all(a["molecule_chembl_id"].startswith("CHEMBL") for a in acts)
    assert all({"molecule_name", "smiles", "pchembl"} <= set(a) for a in acts), "the fields a model needs to propose a compound are present"
    assert all(a["smiles"] for a in acts), "ChEMBL often has no preferred NAME for a molecule (molecule_name can be None), so the structure is what a model can rely on"


def test_chembl_ids_resolve_against_the_real_lookup_payload_and_a_real_404_is_invalid_not_unreachable(tmp_path, monkeypatch):
    monkeypatch.setattr(citations, "CACHE", tmp_path / "c.json")
    assert citations.verify("CHEMBL428690", lambda url: raw("chembl_id_lookup_ok.json"))["status"] == "verified"
    code = int((FX / "chembl_id_lookup_missing.status").read_text())
    assert code == 404, "the service says 404 for an id that does not exist"

    def missing(url):
        raise urllib.error.HTTPError(url, code, "Not Found", None, None)
    assert citations.verify("CHEMBL99999999999", missing)["status"] == "invalid"


def test_a_real_pubmed_esummary_resolves_a_pmid_and_a_chembl_id_is_never_read_as_one(tmp_path, monkeypatch):
    monkeypatch.setattr(citations, "CACHE", tmp_path / "c.json")
    assert citations.verify("PMID 23528858", lambda url: raw("pubmed_esummary.json"))["status"] == "verified"
    assert citations.parse("CHEMBL428690; PMID 1") == ("chembl", "CHEMBL428690")


# ---- Open Targets ----

def test_open_targets_summarises_the_real_graphql_payload_with_the_current_schema():
    class A(OpenTargetsAdapter):
        def _graphql(self, query, variables):
            if "targetDetails" in query:
                assert "knownDrugs" not in query and "approvedSymbol" in query, "fields the live API no longer has must not be asked for"
            return js("opentargets_search.json")["data"] if "search(" in query else js("opentargets_target.json")["data"]
    s = A().summary(symbol="EGFR")
    assert s["symbol"] == "EGFR" and s["ensembl_id"].startswith("ENSG") and s["known_drugs"] and s["top_diseases"] and s["tractability_modalities"]


def test_the_recorded_open_targets_responses_carry_no_graphql_errors():
    for name in ("opentargets_search.json", "opentargets_target.json"):
        assert "errors" not in js(name) and js(name)["data"], f"{name}: the query the adapter sends must be valid against the live schema"
