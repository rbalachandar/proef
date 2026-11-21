"""Citation verification — enforce the "never invent" rule.

A hypothesis may cite a PMID or DOI to ground its rationale. This module
checks that the citation EXISTS (PMID resolvable / DOI indexed) via PubMed
E-utilities, with a local content-addressed cache so repeat runs are free
and offline runs degrade to "check-failed" — never to a false "invalid".

Statuses:
  verified        citation resolves to a real publication
  invalid         checked online, no such publication (hallucination)
  check-failed    offline/timeout — unknown, treated as unverified (no bonus)
  unverified      never checked
"""
from __future__ import annotations

import json
import re
import urllib.parse
import urllib.error
import urllib.request
from pathlib import Path

CACHE = Path(".data/citations.json")
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils"

CHEMBL_RE = re.compile(r"\b(CHEMBL\d+)\b", re.I)
PMID_RE = re.compile(r"\bpmid[:\s]*(\d{5,9})\b", re.I)      # the label is required and the digits must not sit inside another token (CHEMBL428690)
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"')>]+)\b")


def parse(citation: str) -> tuple[str, str] | None:
    """('pmid', '35377565') or ('doi', '10.1021/...') or None."""
    citation = citation.strip()
    m = DOI_RE.search(citation)
    if m:
        return ("doi", m.group(1).rstrip(".,;"))
    m = PMID_RE.search(citation)
    if m:
        return ("pmid", m.group(1))
    if citation.isdigit():
        return ("pmid", citation)
    m = CHEMBL_RE.search(citation)
    if m:                                  # a ChEMBL id (molecule, target, document or assay): the lookup endpoint resolves any of them
        return ("chembl", m.group(1).upper())
    return None


def _default_fetch(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:  # noqa: S310
        return r.read()


def _load_cache() -> dict:
    try:
        return json.loads(CACHE.read_text())
    except Exception:
        return {}


def _save_cache(c: dict) -> None:
    CACHE.parent.mkdir(parents=True, exist_ok=True)
    CACHE.write_text(json.dumps(c, indent=1))


def verify(citation: str, fetch: callable = _default_fetch) -> dict:
    """Resolve one citation. Returns {status, kind, detail}."""
    parsed = parse(citation)
    if not parsed:
        return {"status": "invalid", "kind": "unparseable",
                "detail": "not a PMID or DOI"}
    kind, value = parsed
    cache = _load_cache()
    key = f"{kind}:{value}"
    if key in cache:
        return cache[key]
    try:
        if kind == "pmid":
            url = (f"{EUTILS}/esummary.fcgi?version=2.0&id={value}"
                   "&db=pubmed&retmode=json")
            data = json.loads(fetch(url))
            rec = data.get("result", {}).get(value)
            if rec and rec.get("title"):
                out = {"status": "verified", "kind": kind,
                       "detail": f"{rec.get('pubdate', '')[:4]} · "
                                 f"{(rec.get('source') or '')[:40]}"}
            else:
                out = {"status": "invalid", "kind": kind,
                       "detail": "PMID not found"}
        elif kind == "chembl":
            data = json.loads(fetch(f"https://www.ebi.ac.uk/chembl/api/data/chembl_id_lookup/{value}.json"))
            out = ({"status": "verified", "kind": kind, "detail": str(data.get("entity_type") or "")[:40]} if data.get("chembl_id") else
                   {"status": "invalid", "kind": kind, "detail": "ChEMBL id not found"})
        else:
            term = urllib.parse.quote(f"{value}[DOI]")
            url = f"{EUTILS}/esearch.fcgi?db=pubmed&retmode=json&term={term}"
            data = json.loads(fetch(url))
            ids = data.get("esearchresult", {}).get("idlist", [])
            out = ({"status": "verified", "kind": kind,
                    "detail": f"PMID {ids[0]}"} if ids else
                   {"status": "invalid", "kind": kind,
                    "detail": "DOI not indexed"})
    except urllib.error.HTTPError as exc:
        if exc.code == 404:                # the service answered: there is no such record (a fake id)
            out = {"status": "invalid", "kind": kind, "detail": f"{kind} not found (HTTP 404)"}
        else:
            return {"status": "check-failed", "kind": kind, "detail": f"HTTP {exc.code}"}
    except Exception as exc:
        # network failure is NOT evidence of a fake citation
        return {"status": "check-failed", "kind": kind,
                "detail": str(exc)[:80]}
    if out["status"] != "check-failed":
        cache[key] = out
        _save_cache(cache)
    return out


def ids_of(citation: str) -> list[tuple[str, str]]:
    """Every identifier in a citation string, in order, each as (kind, value): a model often writes several ('PMID 1; CHEMBL2; 10.x/y')."""
    out: list[tuple[str, str]] = []
    for m in re.finditer(r"(10\.\d{4,9}/[^\s\";,)>]+)|\bpmid[:\s]*(\d{5,9})\b|\b(CHEMBL\d+)\b", citation or "", re.I):
        item = ("doi", m.group(1).rstrip(".,;")) if m.group(1) else ("pmid", m.group(2)) if m.group(2) else ("chembl", m.group(3).upper())
        if item not in out:
            out.append(item)
    return out


def verify_each(citation: str, fetch: callable = _default_fetch) -> dict:
    """A citation that names several things is verified only if EVERY one resolves: any id that does not exist makes it invalid; an unreachable
    service on any makes it check-failed. (`verify` looks at the first identifier only.)"""
    ids = ids_of(citation)
    if not ids:
        return verify(citation, fetch)
    results = [verify(f"PMID {v}" if k == "pmid" else v, fetch) for k, v in ids]
    status = ("invalid" if any(r["status"] == "invalid" for r in results) else "check-failed" if any(r["status"] == "check-failed" for r in results)
              else "verified")
    return {"status": status, "kind": "multi" if len(ids) > 1 else ids[0][0], "detail": f"{len(ids)} identifier(s): " + ", ".join(r["status"] for r in results)}


def verify_many(citations: list[str], fetch=_default_fetch) -> dict[str, dict]:
    return {c: verify(c, fetch) for c in citations if c}
