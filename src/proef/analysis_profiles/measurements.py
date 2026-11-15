"""Measurements analysis profile: a table of values per sample, judged against a gate.

The SECOND analysis profile, added because a real second use case needs it: property data that is not a dose
series (ionic conductivity, solubility, yield, tensile strength, a pH reading). One row per measurement; repeated
rows for a sample are its replicates. Per sample it reports n, mean, sd and a 95% interval on the mean,
and, only when a gate is registered, a call by the SAME rule as everywhere else: an interval that straddles the
gate abstains.

What it cannot do, and says so: there is no curve, no plate, so no Z' and no control-based QC. One measurement gives
no interval and therefore no call. The interval assumes roughly normal replicate scatter on the arithmetic scale
(a quantity that spans orders of magnitude should be entered as its log10), which is stated in the result.
"""
from __future__ import annotations

import csv
import io
import math
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict

from proef.core.contracts.analysis import (AnalysisDiagnostic, AnalysisState, EvidenceOrigin, PreliminaryCall)
from proef.core.hypothesis import SuccessCriteria

PROFILE_NAME = "measurements"
PROFILE_VERSION = 1

SAMPLE_NAMES = ("sample", "sample_id", "sample id", "id", "candidate", "compound", "material", "name", "specimen",
                "entity", "composition", "formulation", "batch_id")
VALUE_NAMES = ("value", "measurement", "result", "reading", "response", "y", "mean")
NOT_A_VALUE = ("replicate", "rep", "run", "batch", "n", "index", "row", "plate", "well", "trial", "repeat", "date", "time")
PLATE_WORDS = {"well", "wells", "conc", "concentration", "dose", "plate", "rfu", "rlu", "od", "absorbance", "fluorescence",
               "luminescence", "cycle", "kinetic"}

MIN_REPLICATES = 3     # the repo-wide rule (fitting.replicate_ci): fewer than 3 readings carry no honest interval


class MeasurementsConfig(BaseModel):
    metric: str = "value"
    direction: str = "min"                # min = lower is better
    threshold: float | None = None        # registered gate; None => estimates only
    unit: str | None = None
    sample_column: str | None = None      # None => inferred, and shown for confirmation
    value_column: str | None = None
    evidence_origin: EvidenceOrigin = EvidenceOrigin.measured

    model_config = ConfigDict(extra="forbid")


CONFIG_TYPE = MeasurementsConfig


def _engine_version() -> str:
    try:
        from importlib.metadata import version
        return version("proef")
    except Exception:  # noqa: BLE001
        return "unknown"


def _table(path: Path) -> tuple[list[str], list[list[str]], str]:
    """(header, rows, delimiter) of a delimited text file. XLSX is read when openpyxl is available."""
    if path.suffix.lower() == ".xlsx":
        try:
            import openpyxl
        except ImportError as exc:
            raise ImportError("reading .xlsx needs openpyxl; export the sheet as CSV instead") from exc
        ws = openpyxl.load_workbook(path, read_only=True, data_only=True).worksheets[0]
        rows = [["" if c is None else str(c) for c in r] for r in ws.iter_rows(values_only=True)]
        rows = [r for r in rows if any(c.strip() for c in r)]
        if not rows:
            raise ValueError("the sheet is empty")
        return [h.strip() for h in rows[0]], rows[1:], "xlsx"
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    sample = text[:4096]
    try:
        delim = csv.Sniffer().sniff(sample, delimiters=",;\t|").delimiter
    except csv.Error:
        delim = "\t" if "\t" in sample else ","
    rows = [r for r in csv.reader(io.StringIO(text), delimiter=delim) if any(c.strip() for c in r)]
    if len(rows) < 2:
        raise ValueError("a measurements table needs a header row and at least one data row")
    return [h.strip() for h in rows[0]], rows[1:], delim


def _num(s: str) -> float | None:
    try:
        x = float(s.strip().replace(",", "") if s.count(",") == 1 and "." in s else s.strip())
    except (ValueError, AttributeError):
        return None
    return x if math.isfinite(x) else None


def looks_like_measurements(path: Path) -> bool:
    """True only for a plain table: a sample column, numeric values, and nothing that says plate or dose.
    Anything with plate vocabulary in a header (well, conc, dose, plate, RFU...) or a plate-grid shape (numeric
    column headers 1..12) stays a plate, so a real instrument export is never read as a measurements table."""
    import re
    try:
        header, rows, _ = _table(path)
    except Exception:  # noqa: BLE001
        return False
    words = {w for h in header for w in re.findall(r"[a-z]+", h.lower())}
    if words & PLATE_WORDS:
        return False
    if sum(bool(re.fullmatch(r"\d{1,2}", h.strip())) for h in header) >= 4:   # a plate grid: columns 1, 2, 3...
        return False
    cols = _columns(header, rows, None, None)
    return cols["sample"] is not None and bool(cols["value_candidates"])


def _columns(header: list[str], rows: list[list[str]], sample_col: str | None, value_col: str | None) -> dict[str, Any]:
    low = [h.lower() for h in header]
    numeric = [i for i in range(len(header)) if sum(_num(r[i]) is not None for r in rows if i < len(r)) >= max(1, 0.6 * len(rows))]

    def pick(names, explicit):
        if explicit and explicit in header:
            return header.index(explicit)
        for n in names:
            if n in low:
                return low.index(n)
        return None

    s = pick(SAMPLE_NAMES, sample_col)
    if s is None:
        text_cols = [i for i in range(len(header)) if i not in numeric]
        s = text_cols[0] if text_cols else None
    v = pick(VALUE_NAMES, value_col)
    cands = [i for i in numeric if i != s and not any(low[i] == t or low[i].startswith(t + "_") for t in NOT_A_VALUE)]
    if v is None and len(cands) == 1:
        v = cands[0]
    return {"sample": s, "value": v, "value_candidates": cands}


def _read(path: Path, cfg: MeasurementsConfig) -> dict[str, Any]:
    header, rows, delim = _table(path)
    cols = _columns(header, rows, cfg.sample_column, cfg.value_column)
    out: dict[str, Any] = {"header": header, "delimiter": delim, "cols": cols, "values": {}, "dropped": 0, "n_rows": len(rows)}
    if cols["sample"] is None or cols["value"] is None:
        return out
    s, v = cols["sample"], cols["value"]
    for r in rows:
        name = r[s].strip() if s < len(r) else ""
        x = _num(r[v]) if v < len(r) else None
        if not name or x is None:
            out["dropped"] += 1
            continue
        out["values"].setdefault(name, []).append(x)
    return out


def _stats(vals: list[float]) -> dict[str, Any]:
    """n, mean, sd and the 95% interval of the mean, from the SAME function the campaigns use for a property metric
    (Student-t below 10 readings, a deterministic bootstrap from 10), and None below 3 readings."""
    from proef.fitting import replicate_ci
    n = len(vals)
    mean = sum(vals) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in vals) / (n - 1)) if n > 1 else None
    ci = replicate_ci(vals)
    return {"n": n, "mean": mean, "sd": sd, "ci": list(ci) if ci else None, "values": vals}


def preview(source: Path, cfg: MeasurementsConfig, layout_path: Path | None = None) -> dict[str, Any]:
    r = _read(source, cfg)
    header, cols = r["header"], r["cols"]
    diags: list[AnalysisDiagnostic] = []
    D = lambda sev, code, msg: diags.append(AnalysisDiagnostic(severity=sev, code=code, message=msg))  # noqa: E731
    needs: list[str] = []
    if cols["sample"] is None:
        D("blocker", "no_sample_column", "no sample column found: name the column that identifies each sample")
        needs.append("sample_column")
    if cols["value"] is None:
        if len(cols["value_candidates"]) > 1:
            D("blocker", "ambiguous_value_column", "several numeric columns could be the measurement: "
              + ", ".join(header[i] for i in cols["value_candidates"]) + ". Choose one.")
        else:
            D("blocker", "no_value_column", "no numeric measurement column found")
        needs.append("value_column")
    samples = sorted(r["values"])
    for s_ in samples:
        if len(r["values"][s_]) < MIN_REPLICATES:
            D("warning", "too_few_measurements", f"{s_}: {len(r['values'][s_])} measurement(s). An interval needs at least "
              f"{MIN_REPLICATES}, so there will be no call for it")
    if r["dropped"]:
        D("warning", "rows_dropped", f"{r['dropped']} row(s) had a missing sample or a non-numeric value and were left out")
    if samples and all(len(v) >= MIN_REPLICATES for v in r["values"].values()):
        D("info", "replicates", f"{len(samples)} samples, {min(len(v) for v in r['values'].values())} to "
          f"{max(len(v) for v in r['values'].values())} measurements each")
    D("info", "no_qc", "No plate, so no Z′. Trust rests on the replicates.")
    state = AnalysisState.needs_mapping if any(d.severity == "blocker" for d in diags) else AnalysisState.preview_ready
    return {
        "profile": PROFILE_NAME, "profile_version": PROFILE_VERSION, "state": state.value, "kind": "measurements",
        "format": f"measurements table ({'xlsx' if r['delimiter'] == 'xlsx' else repr(r['delimiter'])[1:-1] if r['delimiter'] != ',' else 'csv'})",
        "n_wells": 0, "n_measurements": sum(len(v) for v in r["values"].values()), "z_prime": None, "z_prime_kind": None,
        "controls_detected": 0, "samples": samples, "has_doses": False,
        "columns": {"sample": header[cols["sample"]] if cols["sample"] is not None else None,
                    "value": header[cols["value"]] if cols["value"] is not None else None,
                    "candidates": [header[i] for i in cols["value_candidates"]], "all": header},
        "diagnostics": [d.model_dump() for d in diags], "needs_confirmation": needs,
        "evidence_origin": cfg.evidence_origin.value,
    }


def analyze(source: Path, cfg: MeasurementsConfig, source_sha256: str | None = None,
            layout_path: Path | None = None) -> dict[str, Any]:
    r = _read(source, cfg)
    if r["cols"]["sample"] is None or r["cols"]["value"] is None or not r["values"]:
        raise ValueError("cannot analyze: confirm the sample and value columns first")
    criteria = SuccessCriteria(metric=cfg.metric, direction=cfg.direction, threshold=cfg.threshold) if cfg.threshold is not None else None
    compounds: dict[str, dict] = {}
    calls: list[PreliminaryCall] = []
    for sample in sorted(r["values"]):
        st = _stats(r["values"][sample])
        compounds[sample] = st
        if criteria is None:
            continue
        if st["ci"] is None:
            calls.append(PreliminaryCall(entity=sample, call="no_call", value=st["mean"],
                                         reason=f"{st['n']} measurement(s): an interval needs at least {MIN_REPLICATES}, so no call"))
            continue
        call = criteria.classify(st["mean"], (st["ci"][0], st["ci"][1]))
        calls.append(PreliminaryCall(
            entity=sample, call=call, value=st["mean"], ci=(st["ci"][0], st["ci"][1]),
            reason="95% interval fully clears the gate" if call != "inconclusive"
            else "95% interval straddles the registered gate: honest abstention"))
    return {
        "profile": PROFILE_NAME, "profile_version": PROFILE_VERSION, "kind": "measurements",
        "state": AnalysisState.analyzed.value, "format": "measurements table", "engine_version": _engine_version(),
        "n_measurements": sum(len(v) for v in r["values"].values()), "compounds": compounds,
        "calls": [c.model_dump() for c in calls], "threshold_registered": cfg.threshold is not None,
        "metric": cfg.metric, "direction": cfg.direction, "unit": cfg.unit, "evidence_origin": cfg.evidence_origin.value,
        "method": "mean with a 95% interval over the replicates (Student-t below 10 readings, bootstrap from 10), arithmetic scale; at least 3 readings per sample",
        "columns": {"sample": r["header"][r["cols"]["sample"]], "value": r["header"][r["cols"]["value"]]},
        "z_prime": None, "qc_hold": False, "source_sha256": source_sha256,
    }


def records_for(path: Path, cfg: MeasurementsConfig, layout_path: Path | None = None) -> list[dict[str, Any]]:
    r = _read(path, cfg)
    return [{"sample": s, "value": x} for s, vals in sorted(r["values"].items()) for x in vals]


def as_read_result(path: Path):
    """A table of measurements in the shape the campaign agent reads a plate in: `.records` of {sample, value}
    (no concentrations, no controls), no Z', and no fitted compounds. `no_plate_qc` tells the verdict code that
    plate QC does not apply here (recorded as 'not applicable', never as a pass or a fail)."""
    from types import SimpleNamespace
    r = _read(path, MeasurementsConfig())
    if r["cols"]["sample"] is None or r["cols"]["value"] is None:
        raise ValueError("this table has no single sample column and measurement column; use Analyze to choose them")
    records = [{"well": "", "sample": s, "conc_um": None, "value": x, "control": False}
              for s, vals in sorted(r["values"].items()) for x in vals]
    return SimpleNamespace(records=records, z_prime=None, z_prime_kind=None, analysis={"compounds": {}},
                           format="measurements", channels=[], response_unit=None, instrument={}, regime=None, batch=None,
                           kind="measurements", no_plate_qc=True)


def read_plate_or_table(path: Path, max_response: float | None = None):
    """What a campaign reads a results file with: a plain table of measurements goes through the measurements
    reader; anything else is a plate. (A plain table must never be fed to the plate reader: it would take a
    replicate column for well coordinates.)"""
    if Path(path).suffix.lower() in (".csv", ".tsv", ".txt") and looks_like_measurements(Path(path)):
        return as_read_result(Path(path))
    from proef.labread import read_plate
    return read_plate(path, max_response=max_response)
