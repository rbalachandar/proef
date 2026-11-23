"""Isolate all local state writes away from the repo working tree.

The workbench registry (.data/wizard.db), the provenance ledger (proef.db) and other local
state live in cwd-relative paths; without this redirect every pytest run pollutes the user's
workspace with test campaigns and programs (program records go to ./proef.db regardless of
PROEF_DATA_DIR). So the whole session runs from a scratch working directory."""
import os
import tempfile

# ONE scratch root PER PROCESS (not a fixed path). The old code used /tmp/proef-test-data and /tmp/proef-test-cwd and rmtree'd them at the start of every
# session, so starting a second pytest deleted the working directory of the one already running (FileNotFoundError in whichever tests came next), and
# parallel workers could never have worked. A unique directory per process, removed when the process exits, makes concurrent and parallel runs safe.
import atexit
import shutil

_root = tempfile.mkdtemp(prefix="proef-test-")
atexit.register(shutil.rmtree, _root, ignore_errors=True)
_data_dir = os.path.join(_root, "data")
os.makedirs(_data_dir, exist_ok=True)
os.environ["PROEF_DATA_DIR"] = _data_dir

# cwd-relative state (proef.db, .data/...) must never be the developer's real workspace
_repo = os.getcwd()
import pathlib
REPO = pathlib.Path(__file__).resolve().parents[1]
_scratch = os.path.join(_root, "cwd")
os.makedirs(_scratch, exist_ok=True)
# NOT chdir'd here at import: collection (and pytest-xdist's workers, which resolve the relative `tests/...` paths from the invocation directory) must still see the
# repository. The tests run from the scratch directory, entered by the session fixture below.


import pytest  # noqa: E402


@pytest.fixture(scope="session", autouse=True)
def _run_from_the_scratch_directory():
    """Every test runs with this process's scratch directory as its working directory (each xdist worker has its own)."""
    before = os.getcwd()
    os.chdir(_scratch)
    yield
    os.chdir(before)


@pytest.fixture
def repo_cwd(monkeypatch):
    """For the few tests that inspect the repository layout itself (skills/, frozen data)."""
    monkeypatch.chdir(REPO)


# ---- the slow tier -------------------------------------------------------------------------------------------------------------------------------------------
# A suite that takes a quarter of an hour stops being run. Tests that take over ~5 s (campaigns and replays) are listed in tests/slow_tests.txt, measured with
# `pytest --durations`, and carry the `slow` marker. `make test` skips them and runs in about a minute; `make test-full` and CI run everything.
def _slow_entries() -> list[str]:
    f = pathlib.Path(__file__).parent / "slow_tests.txt"
    return [ln.strip() for ln in f.read_text().splitlines() if ln.strip() and not ln.startswith("#")] if f.exists() else []


def pytest_collection_modifyitems(config, items):
    entries = _slow_entries()
    files = tuple(e for e in entries if e.endswith(".py"))
    ids = {e for e in entries if not e.endswith(".py")}
    for item in items:
        nid = item.nodeid.replace("\\", "/")
        if nid in ids or nid.split("::")[0] in files:
            item.add_marker(pytest.mark.slow)
