#!/usr/bin/env python3
"""Records the CLI demo GIF: runs the real `proef demo`, paces its output like a person reading it, and renders it.

    python scripts/record_cli_demo.py            # writes assets/demo/proef-demo.gif  (needs agg on PATH)

The output is the demo's own (simulated numbers, labelled so in its first line). Only the playback is paced here;
nothing in the text is invented. Verdict words are coloured for the recording. Speed is a flag of the render step."""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "demo" / "proef-demo.gif"
SPEED = "2.25"   # 1.5x faster than the previous 1.5x cut
COLS = 118

RESET, DIM, BOLD = "\x1b[0m", "\x1b[2m", "\x1b[1m"
BLUE, RED, AMBER, GREY = "\x1b[38;5;75m", "\x1b[38;5;203m", "\x1b[38;5;179m", "\x1b[38;5;245m"
PAINT = [(r"\bCONFIRMED\b", BLUE), (r"\bREFUTED\b", RED), (r"\bABSTAINS\b|\bINCONCLUSIVE\b", AMBER), (r"\bVALIDATED\b", BLUE),
         (r"chain verified: OK", BLUE), (r"All numbers SIMULATED", AMBER)]


def paint(line: str) -> str:
    for pat, colour in PAINT:
        line = re.sub(pat, lambda m: f"{colour}{BOLD}{m.group(0)}{RESET}", line)
    return line


def run_demo(workdir: Path) -> list[str]:
    out = subprocess.run([sys.executable, "-m", "proef.cli", "demo", "--db", str(workdir / "demo.db")],
                         capture_output=True, text=True, cwd=workdir, check=True)
    # show the demo's normal default location, not this run's scratch path
    return out.stdout.replace(str(workdir / "demo.db"), ".data/demo.db").splitlines()


def wrap(line: str) -> list[str]:
    """Keep the line breaks readable: wrap long lines with a hanging indent instead of letting the terminal run them on."""
    if len(line) <= COLS:
        return [line]
    indent = " " * (len(line) - len(line.lstrip()) + (4 if line.lstrip().startswith(("*", "[")) else 2))
    return textwrap.wrap(line, COLS, subsequent_indent=indent, break_long_words=False) or [line]


def delay(line: str) -> float:
    """How long a reader lingers on a line."""
    if line.lstrip().startswith("*"):
        return 3.0                       # the three narrated moments are the point: slowed in raw time so they stay readable
    if "orthogonal confirmation for" in line or "CAMPAIGN COMPLETE" in line:
        return 1.1
    if line.startswith("verdicts:"):
        return 2.4
    if not line.strip():
        return 0.5
    return 0.32


def main() -> None:
    if shutil.which("agg") is None:
        sys.exit("agg is needed to render the GIF: brew install agg")
    work = Path(tempfile.mkdtemp(prefix="proef-cli-rec-"))
    lines = [l for l in run_demo(work) if "] [agent] analyzed" not in l and "] reviewer:" not in l]   # trim the dullest lines
    events, t = [], 0.0

    def emit(text: str, wait: float = 0.0) -> None:
        nonlocal t
        t += wait
        events.append([round(t, 3), "o", text])

    emit(f"{GREY}${RESET} ", 0.4)
    for ch in "proef demo":                          # typed, like a person
        emit(ch, 0.07)
    emit("\r\n", 0.5)
    for line in lines:
        for k, piece in enumerate(wrap(line)):
            emit(paint(piece.replace("[demo] ", f"{DIM}[demo]{RESET} ")) + "\r\n", delay(line) if k == 0 else 0.05)
    emit("", 0.5)                                    # hold the final frame
    rows = sum(len(wrap(l)) for l in lines) + 3
    cast = work / "demo.cast"
    with cast.open("w") as fh:
        fh.write(json.dumps({"version": 2, "width": COLS, "height": rows, "env": {"TERM": "xterm-256color"}}) + "\n")
        for e in events:
            fh.write(json.dumps(e) + "\n")
    subprocess.run(["agg", str(cast), str(OUT), "--speed", SPEED, "--theme", "dracula", "--font-size", "15",
                    "--last-frame-duration", "2"], check=True)
    print("wrote", OUT, f"{OUT.stat().st_size / 1e6:.2f} MB")


if __name__ == "__main__":
    main()
