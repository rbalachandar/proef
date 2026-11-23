"""Tests must not interfere with each other or with a second pytest process. The old conftest used fixed scratch directories and deleted them at the start of
every session, so starting a second pytest wiped the working directory of the one already running."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def test_each_process_works_in_its_own_scratch_directory():
    cwd = Path(os.getcwd()).resolve()
    assert cwd.parent.name.startswith("proef-test-") and cwd.parent.parent.resolve() == Path(tempfile.gettempdir()).resolve()
    assert Path(os.environ["PROEF_DATA_DIR"]).resolve().parent == cwd.parent
    assert cwd.parent.name != "proef-test-cwd", "not the old fixed, shared name"


def test_starting_a_second_pytest_does_not_delete_this_ones_working_directory():
    before = Path(os.getcwd())
    marker = before / "still-here.txt"
    marker.write_text("x")
    child = subprocess.run([sys.executable, "-m", "pytest", "--collect-only", "-q", "-p", "no:warnings", str(Path(__file__))], cwd=REPO,
                           capture_output=True, text=True, timeout=120)
    assert child.returncode == 0, child.stdout[-400:] + child.stderr[-400:]
    assert before.exists() and marker.exists(), "the second process wiped this process's scratch directory"
    assert os.getcwd() == str(before)


def test_a_process_cleans_up_its_scratch_root_when_it_exits():
    code = ("import os, sys; sys.path.insert(0, 'tests'); import conftest; print(conftest._root)")
    out = subprocess.run([sys.executable, "-c", code], cwd=REPO, capture_output=True, text=True, timeout=120)
    root = Path(out.stdout.strip().splitlines()[-1])
    assert root.name.startswith("proef-test-") and not root.exists(), "the scratch root is removed at exit"
