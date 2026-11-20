"""Do the deterministic detectors catch what a model fabricates, without crying wolf?  (5F, and the narrative kinds of 5G)

Scored against `evals/fixtures/hallucination.json`: per category, recall on injected items and false-positive rate on clean ones. The
thresholds are in the fixture's readme and were set before the first run. The fixture includes deliberately hard cases, so a score below
1.0 is the detectors' real limit, not a bug in the eval. Offline and deterministic (a fake citation fetch, a throwaway cache)."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest import mock

from proef import citations, groundedness as g
from proef.evals.harness import TaskResult, _evals_dir, register

RECALL_MIN, FPR_MAX = 0.80, 0.20


def _text(x):
    return x["text"] if isinstance(x, dict) else x


def _fixture() -> dict:
    return json.loads((_evals_dir() / "fixtures" / "hallucination.json").read_text())


def _detectors(fx: dict) -> dict:
    def citation_flag(c):
        real = set(fx["fabricated_citation"]["real_pmids"])

        def fetch(url):
            pid = next((p for p in real if f"id={p}" in url), None)
            return json.dumps({"result": {pid: {"title": "t", "pubdate": "2020", "source": "J"}}} if pid else {"result": {}, "esearchresult": {"idlist": []}}).encode()
        with tempfile.TemporaryDirectory() as d, mock.patch.object(citations, "CACHE", Path(d) / "c.json"):
            return citations.verify(c, fetch)["status"] == "invalid"
    f = fx
    return {
        "untestable_refutation": lambda x: g.untestable_refutation(_text(x)) is not None,
        "invented_candidate": lambda x: g.not_in_library(_text(x), f["invented_candidate"]["library"]),
        "bad_structure": lambda x: g.structure_problem(x["smiles"], x["name"], f["bad_structure"]["library_structures"]) is not None,
        "quoted_number": lambda x: bool(g.quoted_number_problems(_text(x), [{**q, "ci_um": tuple(q["ci_um"])} for q in f["quoted_number"]["findings"]],
                                                                 f["quoted_number"]["gate_um"])),
        "gate_misstated": lambda x: bool(g.gate_misstated(_text(x), f["gate_misstated"]["gate_um"])),
        "state_claims": lambda x: bool(g.state_claims(x["text"], x["verdicts"], x.get("queued"), x.get("runs_left"))),
        "ruled_out": lambda x: g.ruled_out(x["candidate"], x["family"], f["ruled_out"]["negatives"]) is not None,
        "phantom_tool": lambda x: bool(g.phantom_tool_claims(_text(x), set(f["phantom_tool"]["called"]), set(f["phantom_tool"]["offered"]))),
        "fabricated_citation": lambda x: citation_flag(x),
    }


def score() -> dict[str, dict]:
    try:
        from rdkit import RDLogger
        RDLogger.DisableLog("rdApp.*")      # the injected unparseable structures are meant to fail: no stderr noise
    except ImportError:
        pass
    fx = _fixture()
    out = {}
    for cat, detect in _detectors(fx).items():
        inj, cln = fx[cat]["injected"], fx[cat]["clean"]
        hit = [bool(detect(x)) for x in inj]
        fp = [bool(detect(x)) for x in cln]
        out[cat] = {"injected": len(inj), "caught": sum(hit), "recall": round(sum(hit) / len(inj), 3), "clean": len(cln), "false_positives": sum(fp),
                    "false_positive_rate": round(sum(fp) / len(cln), 3),
                    "missed": [_text(x) if not isinstance(x, dict) or "text" in x else x for x, h in zip(inj, hit) if not h],
                    "wrongly_flagged": [_text(x) if not isinstance(x, dict) or "text" in x else x for x, h in zip(cln, fp) if h]}
    return out


@register("hallucination-detectors",
          "deterministic detectors for what a model fabricates (invented candidate, bad structure, untestable refuted-if, misquoted number, misstated "
          "gate, wrong claim about the campaign's own state, repeated ruled-out idea, phantom tool call, fabricated citation): recall and false-positive rate per category on a labelled fixture that "
          "includes hard cases on purpose", suite="hallucination")
def _t_hallucination_detectors():
    res = score()
    ok = all(v["recall"] >= RECALL_MIN and v["false_positive_rate"] <= FPR_MAX for v in res.values())
    mean = sum(max(0.0, v["recall"] - v["false_positive_rate"]) for v in res.values()) / len(res)
    worst = min(res, key=lambda k: res[k]["recall"] - res[k]["false_positive_rate"])
    return TaskResult("hallucination-detectors", "what a model fabricates is caught, and clean text is not flagged", round(mean, 3), ok,
                      {"categories": res, "recall_min": RECALL_MIN, "false_positive_rate_max": FPR_MAX},
                      f"{sum(v['caught'] for v in res.values())}/{sum(v['injected'] for v in res.values())} injected caught, "
                      f"{sum(v['false_positives'] for v in res.values())}/{sum(v['clean'] for v in res.values())} clean wrongly flagged; weakest: {worst} "
                      f"(recall {res[worst]['recall']}, false-positive {res[worst]['false_positive_rate']}); simulated-author fixture, not a real model")
