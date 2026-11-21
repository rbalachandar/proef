"""Analyse the saved runs of scripts/grounding_experiment.py. Offline: no model calls. --verify-citations resolves each cited PMID or DOI at PubMed."""
import argparse
import json
from pathlib import Path

from proef import citations
from proef.experiments.grounding_exp import analyze, load_partials, load_runs, render


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", required=True)
    ap.add_argument("--verify-citations", action="store_true")
    ap.add_argument("--json", default=None, help="also write the full result here")
    a = ap.parse_args()
    res = analyze(load_runs(a.dir), verify=citations.verify if a.verify_citations else None, partials=len(load_partials(a.dir)))
    print(render(res))
    if a.json:
        Path(a.json).write_text(json.dumps(res, indent=1, default=str))


if __name__ == "__main__":
    main()
