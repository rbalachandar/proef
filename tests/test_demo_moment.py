"""proef demo — the moment must LAND, every run, on the real machinery."""
import subprocess
import sys
from pathlib import Path

from proef.demo import DEMO_GATE_UM, EXPECTED, run_demo


def test_demo_engineers_the_two_moments(tmp_path):
    rep = run_demo(db_path=tmp_path / "demo.db",
                   html_path=tmp_path / "diary.html")
    assert rep["verdicts"] == EXPECTED
    assert rep["chain_ok"] and rep["records"] > 20
    assert rep["simulated"] is True

    ab = rep["moments"]["abstain"]
    assert ab["ci95"][0] < DEMO_GATE_UM < ab["ci95"][1]   # straddle

    rf = rep["moments"]["refutation"]
    assert rf["rule"] == "ci-overlap" and rf["agreed"] is False
    pre_ci = rf["preliminary"]["ci95"]
    assert pre_ci[1] < DEMO_GATE_UM     # preliminary looked like a REAL hit
    assert rf["secondary_uM"] > rf["primary_uM"]

    assert "abstain" in rep["narration"][0].lower() or "ABSTAIN" in rep["narration"][0]
    assert any("REFUTED" in line for line in rep["narration"])

    html = (tmp_path / "diary.html").read_text()
    assert "Campaign diary" in html and "BORDER" in html


def test_demo_is_deterministic(tmp_path):
    a = run_demo(db_path=tmp_path / "a.db")
    b = run_demo(db_path=tmp_path / "b.db")
    assert a["verdicts"] == b["verdicts"] == EXPECTED
    assert [l for l in a["narration"]] == b["narration"]


def test_cli_demo_exit_zero(tmp_path):
    out = subprocess.run(
        [sys.executable, "-m", "proef.cli", "demo", "--quiet",
         "--db", str(tmp_path / "c.db")],
        capture_output=True, text=True, cwd=Path(__file__).parent.parent)
    assert out.returncode == 0
    assert "the two moments landed" in out.stdout
    assert "chain verified: OK" in out.stdout


def _shuffled_listing(monkeypatch, seed):
    """What another filesystem could do: list a directory's files in a different order."""
    import random
    real = Path.glob
    monkeypatch.setattr(Path, "glob", lambda self, pat: iter(random.Random(seed).sample(list(real(self, pat)),
                                                                                         len(list(real(self, pat))))))


def _confirmation_runs(rep):
    """candidate -> the run id its orthogonal confirmation was submitted under (from the campaign's own log)."""
    import re
    return dict(re.findall(r"orthogonal confirmation assay for (\w+) .*-> run (sim-\d+-\d+)", "\n".join(rep["lines"])))


def test_confirmation_run_ids_and_verdicts_do_not_depend_on_directory_listing_order(tmp_path, monkeypatch):
    """CI found this: macOS and Linux list an outbox in different orders, and the analysis order decided which
    confirmation run got which run id (and so which simulator noise). The monitor now fixes the order."""
    baseline = _confirmation_runs(run_demo(db_path=tmp_path / "base.db"))
    assert baseline == {"WINNER": "sim-19-004", "FALSER": "sim-19-005", "BORDER": "sim-19-006"}, baseline
    for seed in range(6):
        _shuffled_listing(monkeypatch, seed)
        rep = run_demo(db_path=tmp_path / f"s{seed}.db")
        assert _confirmation_runs(rep) == baseline, f"listing order {seed} changed which run confirmed what"
        assert rep["verdicts"] == EXPECTED


def test_the_monitor_yields_files_in_a_fixed_order(tmp_path, monkeypatch):
    from proef.watch import DeviceMonitor
    for name in ("sim-1-003.csv", "sim-1-001.csv", "sim-1-002.csv"):
        (tmp_path / name).write_text("x")
    _shuffled_listing(monkeypatch, 3)
    assert [e.path.name for e in DeviceMonitor(tmp_path).poll()] == ["sim-1-001.csv", "sim-1-002.csv", "sim-1-003.csv"]


def test_the_false_positive_is_engineered_not_left_to_noise():
    """FALSER looks potent in the binding assay and is inactive by the orthogonal enzymatic method, by explicit bias."""
    from proef.demo import DEMO_METHOD_BIAS
    from proef.adapters.lab.simulator import SimulatorLab
    from proef.core.contracts import Envelope, ProtocolOp, ProtocolStep, ProtocolSubmission, SampleKind, SampleSpec

    def run(lab, assay):
        p = ProtocolSubmission(envelope=Envelope(experiment_branch_id="t"), title="t", assay_type=assay,
                               steps=[ProtocolStep(seq=1, op=ProtocolOp.dispense)],
                               samples=[SampleSpec(sample_id="FALSER", kind=SampleKind.compound, concentration="1uM")])
        return lab.apparent_ic50_um(lab.submit_protocol(p), "FALSER")
    plain, biased = SimulatorLab(seed=19), SimulatorLab(seed=19, method_bias=DEMO_METHOD_BIAS)
    assert run(biased, "binding") == run(plain, "binding"), "the primary (binding) assay is untouched"
    assert run(biased, "enzymatic") == 4.0 * run(plain, "enzymatic"), "only the orthogonal method disagrees"
    again = SimulatorLab.import_state(biased.export_state())
    assert again.method_bias == DEMO_METHOD_BIAS, "the bias survives the sandbox state round trip"
