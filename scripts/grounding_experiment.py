"""Run the combined grounding experiment (docs/LLM-ORCHESTRATION.md, 5J) with the model from the environment.

    export PROEF_LLM_PRESET=opencode-go PROEF_LLM_MODEL=gpt-6-luna
    python scripts/grounding_experiment.py --out /tmp/grounding_exp --repeats 2 --min-interval 2
    python scripts/grounding_analyze.py --dir /tmp/grounding_exp --verify-citations

Rate limit and quota: the provider's quota is the limit, so there is no call cap by default; what to vary is the pace (--min-interval seconds between calls,
--backoff waits after a 429). If quota runs out the run is saved as partial (not data), the suite stops, and the SAME command resumes later: completed
runs are skipped. Run a part only with --only / --arms / --reps / --tags; add --force to redo completed runs. Every run saves its ledger (.db), every model
call as it happens (.steps.jsonl) and the raw result (.json), so analysis can be redone offline without a model.

The lab is SIMULATED. The knowledge tools call the public PubMed, ChEMBL and Open Targets APIs. Arm D plants a hostile instruction in a retrieved PubMed abstract.
"""
import argparse
import json
from pathlib import Path

from proef.adapters.databases.chembl_lookup import ChemblLookup
from proef.adapters.databases.open_targets import OpenTargetsAdapter
from proef.adapters.databases.pubmed import PubMedAdapter
from proef.adapters.models.from_env import model_from_env
from proef.experiments.grounding_exp import ARMS, HostilePubMed, run_suite


def services(arm: str, objective: dict) -> dict:
    pm = PubMedAdapter()
    if ARMS[arm]["hostile"]:
        pm = HostilePubMed(pm, prefer_pmid=objective.get("paper_pmid"))
    return {"pubmed": pm, "chembl": ChemblLookup(), "opentargets": OpenTargetsAdapter()}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", default=str(Path(__file__).with_name("grounding_suite.json")))
    ap.add_argument("--only", default="", help="comma-separated objective ids (default: all in the suite)")
    ap.add_argument("--arms", default="ABCD")
    ap.add_argument("--repeats", type=int, default=2)
    ap.add_argument("--total-cap", type=int, default=None, help="optional: stop before a run that could take this session's model calls past this")
    ap.add_argument("--min-interval", type=float, default=0.0, help="seconds between model calls (the rate limit)")
    ap.add_argument("--backoff", default="30,60,120", help="seconds to wait after each rate-limit refusal, before giving up")
    ap.add_argument("--reps", default="", help="repeat numbers to run, e.g. 0,1 (default: 0..repeats-1)")
    ap.add_argument("--tags", default="", help="run exactly these, e.g. B-pi3k_cdk7-1,D-dyrk1b-0")
    ap.add_argument("--force", action="store_true", help="re-run runs that are already complete")
    ap.add_argument("--max-model-calls", type=int, default=60, help="the model-call budget of ONE campaign")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    suite = json.loads(Path(a.suite).read_text())
    objs = [o for o in suite["objectives"] if not a.only or o["id"] in a.only.split(",")]
    pace = {"min_interval": a.min_interval, "backoff": tuple(float(x) for x in a.backoff.split(",") if x)}
    res = run_suite(objs, a.arms, a.repeats, model_from_env, services, Path(a.out), a.total_cap, a.max_model_calls, pace=pace, resume=not a.force,
                    tags=set(a.tags.split(",")) if a.tags else None, reps=[int(x) for x in a.reps.split(",")] if a.reps else None)
    print(f"\n{len(res)} runs, {sum(r['model_calls'] for r in res)} model calls, raw data in {a.out}")


if __name__ == "__main__":
    main()
