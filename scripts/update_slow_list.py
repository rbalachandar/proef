#!/usr/bin/env python3
"""Regenerate tests/slow_tests.txt from a `pytest --durations` run: every test whose call took over THRESHOLD seconds joins the slow tier (skipped by `make test`,
run by `make test-full` and CI). Whole-file entries already in the list are kept.

    .venv/bin/python -m pytest -q -p no:warnings -n 8 --durations=0 | tee /tmp/dur.txt
    python scripts/update_slow_list.py /tmp/dur.txt [threshold_seconds]
"""
import re
import sys
from pathlib import Path

THRESHOLD = float(sys.argv[2]) if len(sys.argv) > 2 else 11.0
LIST = Path(__file__).resolve().parents[1] / "tests" / "slow_tests.txt"


def main() -> None:
    text = Path(sys.argv[1]).read_text()
    old = [ln for ln in LIST.read_text().splitlines()] if LIST.exists() else []
    header = [ln for ln in old if ln.startswith("#")]
    files = [ln for ln in old if ln.endswith(".py") and not ln.startswith("#")]
    slow = sorted({m.group(2) for m in re.finditer(r"^([\d.]+)s call\s+(\S+)$", text, re.M) if float(m.group(1)) >= THRESHOLD and m.group(2).split("::")[0] not in files})
    LIST.write_text("\n".join(header + files + slow) + "\n")
    print(f"{len(slow)} slow tests (>= {THRESHOLD:g} s) and {len(files)} whole-file entries written to {LIST}")


if __name__ == "__main__":
    main()
