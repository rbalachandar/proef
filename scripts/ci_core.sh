#!/usr/bin/env bash
# Run the whole suite in CI's `core` environment: Python 3.12 and ONLY the packages the CI job installs (no RDKit, NumPy, torch, scikit-learn, YAML or SciPy),
# with PROEF_SKIP_DOCKER=1. A green `make test-full` on a developer machine is not a green CI: the full local environment has everything installed, and a test that
# quietly depends on RDKit passes here and fails there. Run this before a push. The venv is cached in /tmp and is about 45 MB; delete it with `rm -rf /tmp/ci-core`.
set -euo pipefail
cd "$(dirname "$0")/.."
VENV="${CI_CORE_VENV:-/tmp/ci-core}"
PY="$(command -v python3.12 || ls /opt/homebrew/bin/python3.12 2>/dev/null || command -v python3)"
if [ ! -x "$VENV/bin/python" ]; then
  "$PY" -m venv "$VENV"
  "$VENV/bin/pip" install -q pydantic pytest fastapi httpx python-multipart pytest-xdist   # keep in step with .github/workflows/ci.yml (job: core)
  "$VENV/bin/pip" install -q -e . --no-deps
fi
PROEF_SKIP_DOCKER=1 "$VENV/bin/python" -m pytest tests/ -q -p no:warnings -n "${CI_CORE_WORKERS:-8}" -rfE "$@"
