"""Run the combined grounding experiment (docs/LLM-ORCHESTRATION.md, 5J) with the model from the environment.

    export PROEF_LLM_PRESET=opencode-go PROEF_LLM_MODEL=gpt-6-luna
    python scripts/grounding_experiment.py --out /tmp/grounding_exp --repeats 2 --total-cap 300
    python scripts/grounding_analyze.py --dir /tmp/grounding_exp --verify-citations

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
    ap.add_argument("--total-cap", type=int, required=True, help="stop before a run that could take total model calls past this")
    ap.add_argument("--max-model-calls", type=int, default=60, help="the model-call budget of ONE campaign")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    suite = json.loads(Path(a.suite).read_text())
    objs = [o for o in suite["objectives"] if not a.only or o["id"] in a.only.split(",")]
    res = run_suite(objs, a.arms, a.repeats, model_from_env, services, Path(a.out), a.total_cap, a.max_model_calls)
    print(f"\n{len(res)} runs, {sum(r['model_calls'] for r in res)} model calls, raw data in {a.out}")


if __name__ == "__main__":
    main()
