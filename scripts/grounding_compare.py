"""Compare the grounded and ungrounded arms from the files grounding_real_model.py saved. No model calls."""
import glob
import json
import re
from pathlib import Path

import argparse

D = Path("/tmp/grounding_compare")


def proposals(run: dict) -> list[dict]:
    m = re.search(r"\{.*\}", run.get("proposal_response") or "", re.S)
    try:
        fams = json.loads(m.group(0)).get("families", []) if m else []
    except ValueError:
        fams = []
    return [{"family": f.get("family"), "citation": f.get("citation") or "", "candidate": v.get("candidate", "").lower()}
            for f in fams for v in f.get("variants", [])]


def load(arm: str) -> list[dict]:
    return [json.loads(Path(p).read_text()) for p in sorted(glob.glob(str(D / f"{arm}-*.json")))]


def recency(find: list[str], runs_u: list[dict], runs_g: list[dict]) -> None:
    """Did the arms use a paper the model cannot have been trained on? `find` = terms from it (a compound name, its PMID)."""
    for name, runs in (("ungrounded", runs_u), ("grounded", runs_g)):
        for t in find:
            t = t.lower()
            retrieved = sum(1 for r in runs if t in json.dumps(r.get("sealed_tool_results") or []).lower())
            in_summary = sum(1 for r in runs if t in (r.get("grounding_summary") or "").lower())
            proposed = sum(1 for r in runs if any(t in p["candidate"] for p in proposals(r)) or t in (r.get("proposal_response") or "").lower())
            print(f"  {name:11} {t!r}: retrieved in {retrieved}/{len(runs)} runs, in summary {in_summary}/{len(runs)}, in proposals {proposed}/{len(runs)}")


def citation_check(runs: list[dict], name: str) -> None:
    """Resolve every citation against PubMed/Crossref with the existing verifier, and (grounded) whether it came from this run's results."""
    from proef import citations
    seen = " ".join(json.dumps(r.get("sealed_tool_results") or []) for r in runs)
    cites = sorted({p["citation"] for r in runs for p in proposals(r) if p["citation"]})
    status = {c: citations.verify(c)["status"] for c in cites}
    print(f"  {name}: {len(cites)} distinct citations; status {dict(__import__('collections').Counter(status.values()))}")
    for c, st in status.items():
        ids = re.findall(r"\d{6,8}|10\.\d{4,}/\S+", c)
        print(f"     {st:9} {c[:60]:60} in this run's tool results: {any(i in seen for i in ids)}")


def decide_report(runs: list[dict]) -> None:
    from collections import Counter
    for i, r in enumerate(runs):
        calls = r.get("tool_calls") or []
        sealed = [d for d in (r.get("sealed_tool_results") or []) if d.get("scope")]
        bg = Counter((p["role"], p["has_background"]) for p in r.get("role_prompts") or [])
        print(f"  run {i}: model_calls={r.get('model_calls')} violations={r.get('audit_violations')} decide-loop model calls={r.get('decide_loop_prompts')}")
        print(f"     tool calls: {dict(Counter(c['tool'] + ':' + str(c['status']) + ('/' + c['code'] if c.get('code') else '') for c in calls))}")
        print(f"     sealed decide results by scope: {dict(Counter(d['scope'] for d in sealed))}")
        print(f"     prompts by (role, had background): {dict(bg)}")
        print(f"     orchestrator choices: {[(c['round'], c['chosen'], c['source']) for c in r.get('orchestrator_choices') or []]}")


def main() -> None:
    global D
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=str(D))
    ap.add_argument("--find", default="", help="comma-separated terms from the recent paper, e.g. fisetin,43005627")
    ap.add_argument("--verify-citations", action="store_true")
    ap.add_argument("--decide", action="store_true")
    a = ap.parse_args()
    D = Path(a.dir)
    ung, gr = load("ungrounded"), load("grounded")
    if a.decide:
        print("DECIDE-STEP GROUNDING"); decide_report(gr); return
    if a.find:
        print("USE OF THE RECENT PAPER")
        recency([t for t in a.find.split(",") if t], ung, gr)
    if a.verify_citations:
        print("\nCITATIONS")
        citation_check(ung, "ungrounded"); citation_check(gr, "grounded")
    seen_all = " ".join((r.get("tool_results_seen") or "") for r in gr).lower()     # everything the grounded runs retrieved
    for name, runs in (("ungrounded", ung), ("grounded", gr)):
        cands = [[p["candidate"] for p in proposals(r)] for r in runs]
        flat = [c for cs in cands for c in cs]
        print(f"\n{name}: {len(runs)} runs, {len(flat)} variants, {len(set(flat))} distinct candidates")
        print("  per run:", [sorted(set(c)) for c in cands])
        print("  candidates present in what the grounded runs retrieved:", sum(1 for c in flat if c and c in seen_all), "of", len(flat))
        cites = [p["citation"] for r in runs for p in proposals(r) if p["citation"]]
        print("  citations:", cites)
    g, u = {p["candidate"] for r in gr for p in proposals(r)}, {p["candidate"] for r in ung for p in proposals(r)}
    print("\ncandidates only grounded proposed:", sorted(g - u))
    print("candidates only ungrounded proposed:", sorted(u - g))
    print("shared:", sorted(g & u))


if __name__ == "__main__":
    main()
