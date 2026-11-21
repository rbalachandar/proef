"""One real-model campaign, with the invariants checked by code. Run it before a release or after changing the loop, the tools or a prompt: the scripted tests
cannot tell you the real model still works with the real services. Uses model quota (about 25 calls) and the network.

    export PROEF_LLM_PRESET=opencode-go PROEF_LLM_MODEL=gpt-6-luna
    make smoke-real
"""
import json
import sys
import tempfile
from pathlib import Path

from proef.adapters.databases.chembl_lookup import ChemblLookup
from proef.adapters.databases.open_targets import OpenTargetsAdapter
from proef.adapters.databases.pubmed import PubMedAdapter
from proef.adapters.models.from_env import model_from_env
from proef.adapters.store.sqlite import SqliteProvenanceStore
from proef.experiments.grounding_exp import load_runs, run_one

OBJ = {"id": "smoke", "objective": "Find a confirmed sub-20uM DYRK1B kinase inhibitor with potential in ovarian cancer",
       "context": "No candidate list: the model proposes mechanistic families and variants", "paper_pmid": "43005627", "find": ["fisetin"]}


def main() -> int:
    out = Path(tempfile.mkdtemp(prefix="proef-smoke-"))
    r = run_one("C", OBJ, 0, model_from_env(), lambda arm, o: {"pubmed": PubMedAdapter(), "chembl": ChemblLookup(), "opentargets": OpenTargetsAdapter()},
                out, Path(tempfile.mkdtemp()), max_calls=60, pace={"min_interval": 2.0})
    run = load_runs(out)[0] if (out / "C-smoke-0.json").exists() else json.loads((out / "C-smoke-0.partial.json").read_text())
    store = SqliteProvenanceStore(str(out / "C-smoke-0.db"))
    branch = f"problem/{run['problem_id']}"
    steps = (out / "C-smoke-0.steps.jsonl").read_text().splitlines()
    loops = {lp["state"] for lp in run["loops"]}
    checks = {
        "the run is complete (no model call failed, no quota problem)": run["complete"],
        "the campaign does not report itself degraded": not any((x.decision or {}).get("kind") == "campaign_degraded" for x in store.branch_records(branch)),
        "zero audit violations": run["audit_violations"] == [],
        "the propose loop read the outside services": "propose" in loops and any(c.get("status") == "ok" for lp in run["loops"] for c in lp["calls"]),
        "the decide loop fired (an open result existed)": "decide" in loops,
        "every orchestrator choice was the model's and valid": bool(run["orchestrator_choices"]) and all(c["source"] == "model" and c["valid"] for c in run["orchestrator_choices"]),
        "every model call is in the step log": len(steps) == run["model_calls"],
        "the saved ledger verifies": store.verify_chain(branch) == [],
        "no tool call that was not offered": not any(c.get("code") == "not_offered" for lp in run["loops"] for c in lp["calls"]),
    }
    for name, ok in checks.items():
        print(("PASS  " if ok else "FAIL  ") + name)
    print(f"\n{r['model_calls']} model calls; raw data in {out}")
    return 0 if all(checks.values()) else 1


if __name__ == "__main__":
    sys.exit(main())
