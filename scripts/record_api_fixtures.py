"""Record REAL responses from the outside services Proef calls, as offline test fixtures (tests/fixtures/api/).

Scripted tests never touch the real services, so they cannot notice that an API changed or that an adapter never worked: the PubMed adapter asked
efetch for JSON (which does not exist), the Open Targets query used fields that had been retired, and a ChEMBL id was read as a PMID. Recording the
real payloads once, and testing the adapters against them offline, catches that class of bug in CI. Re-run this to refresh them (network needed):

    python scripts/record_api_fixtures.py
"""
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "api"
HDR = {"Accept": "application/json", "User-Agent": "proef-fixture-recorder"}


def get(url: str, data: bytes | None = None, headers: dict | None = None) -> tuple[int, bytes]:
    req = urllib.request.Request(url, data=data, headers={**HDR, **(headers or {})})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def save(name: str, body: bytes, limit: int = 60_000) -> None:
    """Never record an ERROR response as a fixture: a fixture that is an error message proves nothing (the first recording of one did exactly that)."""
    if name.endswith(".json"):
        doc = json.loads(body)
        if isinstance(doc, dict) and ("error_message" in doc or "errors" in doc):
            raise SystemExit(f"refusing to record {name}: the service returned an error: {str(doc)[:200]}")
        if len(body) > limit:
            raise SystemExit(f"refusing to record {name}: {len(body)} bytes would be cut mid-JSON (limit {limit}); narrow the request")
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / name).write_bytes(body[:limit])
    print(f"  {name}: {min(len(body), limit)} bytes")


def main() -> None:
    eu = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"
    q = urllib.parse.urlencode({"term": "EGFR T790M resistance", "retmax": "3", "retmode": "json", "db": "pubmed", "tool": "proef"})
    _, body = get(f"{eu}/esearch.fcgi?{q}")
    save("pubmed_esearch.json", body)
    ids = json.loads(body)["esearchresult"]["idlist"][:2]
    q = urllib.parse.urlencode({"id": ",".join(ids), "rettype": "abstract", "retmode": "xml", "db": "pubmed", "tool": "proef"})
    save("pubmed_efetch.xml", get(f"{eu}/efetch.fcgi?{q}")[1], 120_000)
    q = urllib.parse.urlencode({"id": "23528858", "version": "2.0", "db": "pubmed", "retmode": "json"})
    save("pubmed_esummary.json", get(f"{eu}/esummary.fcgi?{q}")[1])
    ch = "https://www.ebi.ac.uk/chembl/api/data"
    save("chembl_target_search.json", get(f"{ch}/target/search.json?q=EGFR&limit=3&only=target_chembl_id,pref_name,target_type,organism")[1])
    save("chembl_activities.json", get(f"{ch}/activity.json?{urllib.parse.urlencode({'target_chembl_id': 'CHEMBL203', 'assay_type': 'B', 'pchembl_value__isnull': 'false', 'limit': 3})}")[1])
    save("chembl_id_lookup_ok.json", get(f"{ch}/chembl_id_lookup/CHEMBL428690.json")[1])
    code, body = get(f"{ch}/chembl_id_lookup/CHEMBL99999999999.json")
    (OUT / "chembl_id_lookup_missing.status").write_text(str(code))
    print(f"  chembl_id_lookup_missing.status: {code}")
    ot = "https://api.platform.opentargets.org/api/v4/graphql"
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
    from proef.adapters.databases.open_targets import QUERY, SEARCH
    for name, query, var in (("opentargets_search.json", SEARCH, {"q": "EGFR"}), ("opentargets_target.json", QUERY, {"ensemblId": "ENSG00000146648"})):
        _, body = get(ot, json.dumps({"query": query, "variables": var}).encode(), {"Content-Type": "application/json"})
        save(name, body)


if __name__ == "__main__":
    main()
