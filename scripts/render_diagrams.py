#!/usr/bin/env python3
"""Writes the README architecture diagrams to diagrams/*.svg (dark, self-contained, no external fonts).
    python scripts/render_diagrams.py
"""
from html import escape
from pathlib import Path

OUT = Path(__file__).resolve().parents[1] / "diagrams"
BG, PANEL, SUNK, LINE, LINE2 = "#090c12", "#0e131c", "#121926", "#1d2535", "#2a3550"
INK, MUTED, FAINT = "#e8ecf4", "#8f9ab0", "#5d6882"
ACC, BAD, WARN, ABS = "#5b8def", "#e8604c", "#e0ac24", "#aeb6c8"
SANS = "-apple-system,'Segoe UI',Inter,Helvetica,Arial,sans-serif"
MONO = "ui-monospace,SFMono-Regular,Menlo,Consolas,monospace"
KINDS = {"p": (PANEL, LINE2, INK), "a": ("#14233f", ACC, INK), "r": ("#35150f", BAD, INK), "w": ("#33290d", WARN, INK), "g": (SUNK, LINE2, INK)}


class Svg:
    def __init__(self, w, h):
        self.w, self.h, self.o = w, h, []

    def add(self, s):
        self.o.append(s)

    def text(self, x, y, s, size=13, fill=INK, weight=400, mono=False, anchor="start", spacing=None):
        ls = f' letter-spacing="{spacing}"' if spacing else ""
        self.add(f'<text x="{x}" y="{y}" font-size="{size}" fill="{fill}" font-weight="{weight}" '
                 f'font-family="{MONO if mono else SANS}" text-anchor="{anchor}"{ls}>{escape(s)}</text>')

    def box(self, x, y, w, h, title, sub=(), kind="p", mono_sub=False):
        fill, stroke, ink = KINDS[kind]
        self.add(f'<rect x="{x}" y="{y}" width="{w}" height="{h}" fill="{fill}" stroke="{stroke}" stroke-width="1"/>')
        self.text(x + 14, y + 25, title, 14.5, ink, 600)
        for i, s in enumerate(sub):
            self.text(x + 14, y + 47 + 17 * i, s, 12, MUTED, mono=mono_sub)

    def label(self, x, y, s, fill=FAINT):
        self.text(x, y, s.upper(), 11, fill, 600, spacing="1.4")

    def arrow(self, x1, y1, x2, y2, color=FAINT, dash=False, head=True):
        d = ' stroke-dasharray="5 4"' if dash else ""
        self.add(f'<path d="M{x1} {y1} L{x2} {y2}" stroke="{color}" stroke-width="1.4" fill="none"{d}/>')
        if head:
            import math
            a = math.atan2(y2 - y1, x2 - x1)
            p = [(x2, y2), (x2 - 9 * math.cos(a - .4), y2 - 9 * math.sin(a - .4)), (x2 - 9 * math.cos(a + .4), y2 - 9 * math.sin(a + .4))]
            self.add(f'<polygon points="{" ".join(f"{px:.1f},{py:.1f}" for px, py in p)}" fill="{color}"/>')

    def path(self, d, color=FAINT, dash=False):
        ds = ' stroke-dasharray="5 4"' if dash else ""
        self.add(f'<path d="{d}" stroke="{color}" stroke-width="1.4" fill="none"{ds}/>')

    def save(self, name, title):
        body = "\n".join(self.o)
        (OUT / name).write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {self.w} {self.h}" width="{self.w}" height="{self.h}" role="img" aria-label="{escape(title)}">\n'
            f'<title>{escape(title)}</title>\n<rect width="{self.w}" height="{self.h}" fill="{BG}"/>\n{body}\n</svg>\n')


def architecture():
    s = Svg(1200, 650)
    X, W = 200, 970
    rows = [
        ("People", 40, 96, [("Plan review and decisions", ["approve, remove or decline", "sealed under the decider's name"]),
                            ("Pause and halt", ["hold one program or campaign", "global halt refuses everything"]),
                            ("Authority and SOPs", ["what agents may do alone", "sign-offs recorded"])], "w"),
        ("Engine", 168, 96, [("Hypothesis agent and critic", ["families with a 'refuted if' clause", "citations checked"]),
                             ("Plan gate and portfolio", ["selects what goes to the lab", "budget enforced"]),
                             ("Analysis and verdict rule", ["fits, QC, 95% interval", "abstains when it straddles"]),
                             ("Programs and research trees", ["gate inherited downward", "results roll up"])], "a"),
    ]
    for name, y, h, boxes, kind in rows:
        s.label(40, y + 28, name)
        n = len(boxes)
        bw = (W - 16 * (n - 1)) // n
        for i, (t, sub) in enumerate(boxes):
            s.box(X + i * (bw + 16), y, bw, h, t, sub, kind)
    # gates band
    s.label(40, 328, "Gates")
    s.add(f'<rect x="{X}" y="300" width="{W}" height="56" fill="#35150f" stroke="{BAD}" stroke-width="1"/>')
    s.text(X + 16, 334, "Readiness review  ·  Safety engine  ·  Halt and pause  ·  Authorization  ·  every refusal carries a reason", 13.5, INK, 600)
    mid = X + W // 2
    s.arrow(mid, 136, mid, 168, FAINT)
    s.arrow(mid, 264, mid, 300, FAINT)
    # interfaces
    s.label(40, 400, "Interfaces")
    ifs = [("ModelProvider", ["OpenAI-compatible,", "Anthropic"]), ("BioTool", ["RDKit, ADMET,", "genomics"]),
           ("LabAdapter", ["simulator, plate reader", "share, Opentrons"]), ("Sandbox", ["local, Docker,", "OpenSandbox"]),
           ("ProvenanceStore", ["SQLite, hash-chained", ""])]
    bw = (W - 16 * 4) // 5
    for i, (t, sub) in enumerate(ifs):
        s.box(X + i * (bw + 16), 376, bw, 88, t, [x for x in sub if x], "g")
    s.arrow(mid, 356, mid, 376, FAINT)
    # ledger
    s.label(40, 516, "Record")
    s.add(f'<rect x="{X}" y="490" width="{W}" height="70" fill="#14233f" stroke="{ACC}" stroke-width="1"/>')
    s.text(X + 16, 520, "Hash-chained ledger", 14.5, INK, 600)
    s.text(X + 16, 541, "Every proposal, decision, run and verdict is sealed. Chain verification recomputes it on read. Anchoring makes it checkable outside this machine.", 12, MUTED)
    s.arrow(mid, 464, mid, 490, FAINT)
    s.text(40, 612, "Everything in the Interfaces row is pluggable. The contracts are pydantic models in proef.core.contracts.", 12, FAINT)
    s.text(40, 634, "A person sits above the engine: nothing reaches the lab until a plan is approved, and the gates apply even then.", 12, FAINT)
    s.save("architecture.svg", "Proef architecture: people, engine, gates, interfaces and the ledger")


def campaign():
    s = Svg(1200, 560)
    steps = [("1  Problem and gate", ["the gate is registered", "before any data"], "w"),
             ("2  Hypotheses", ["the model proposes families", "with a 'refuted if' clause"], "a"),
             ("3  Plan review", ["critic raises concerns,", "a person approves or edits"], "w"),
             ("4  Gates", ["readiness, safety,", "halt and pause"], "r"),
             ("5  Lab run", ["simulated lab or real", "instruments, raw files kept"], "g"),
             ("6  Verdict", ["fixed rule on the 95%", "interval against the gate"], "a")]
    bw, gap, x0, y = 170, 24, 30, 56
    for i, (t, sub, k) in enumerate(steps):
        x = x0 + i * (bw + gap)
        s.box(x, y, bw, 92, t, sub, k)
        if i:
            s.arrow(x - gap + 2, y + 46, x - 2, y + 46, FAINT)
    # outcomes
    oy = 250
    s.label(30, oy - 18, "What the rule decides")
    outs = [("Validated", ["interval fully past the gate", "an orthogonal assay confirms or refutes it"], "a"),
            ("Abstained", ["interval straddles the gate, or QC failed", "no claim is made"], "g"),
            ("Refuted", ["interval fully on the wrong side", "recorded, the family is discounted later"], "r")]
    ow = 360
    for i, (t, sub, k) in enumerate(outs):
        s.box(30 + i * (ow + 30), oy, ow, 84, t, sub, k)
    vx = x0 + 5 * (bw + gap) + bw // 2
    for i in range(3):
        cx = 30 + i * (ow + 30) + ow // 2
        s.path(f"M{vx} 148 L{vx} 196 L{cx} 196 L{cx} {oy}", FAINT)
    # human decision + next round
    s.box(30 + 1 * (ow + 30), 390, ow, 80, "A person decides what is left", ["follow up, accept as inconclusive, or drop", "the decision is sealed in the ledger"], "w")
    s.arrow(30 + 1 * (ow + 30) + ow // 2, 334, 30 + 1 * (ow + 30) + ow // 2, 390, FAINT)
    s.path(f"M{30 + ow + 30} 430 L 14 430 L 14 28 L {x0 + bw + gap + bw // 2} 28 L {x0 + bw + gap + bw // 2} 50", ACC, dash=True)
    s.arrow(x0 + bw + gap + bw // 2, 40, x0 + bw + gap + bw // 2, 56, ACC)
    s.text(60, 497, "Next round: with re-plan on, the model proposes new hypotheses from the results and a person approves them the same way.", 12, ACC)
    # ledger
    s.add(f'<rect x="30" y="512" width="1140" height="34" fill="#14233f" stroke="{ACC}" stroke-width="1"/>')
    s.text(46, 534, "Every step above is a hash-chained record. proef prove <problem-id> renders the whole chain as a shareable audit.", 12.5, INK)
    s.save("campaign-flow.svg", "How a campaign runs: problem, hypotheses, plan review, gates, lab run, verdict")


def gates():
    s = Svg(1200, 506)
    s.text(30, 36, "Between a plan and a lab run, four checks. Any of them can stop the run, and each stop records its reason.", 13.5, MUTED)
    cols = [("Readiness review", ["is the result interpretable?", "denominator, controls,", "readout capability"], "Blocked: redesign"),
            ("Safety engine", ["reagents, biosafety level,", "toxicophores in structures,", "dual-use text"], "Refused: rule-set version"),
            ("Halt and pause", ["global halt refuses", "a paused program or campaign", "holds, then resumes"], "Held or refused"),
            ("Authorization", ["what agents may do alone", "SOP sign-offs,", "approver identity"], "Needs a person")]
    bw, gap, x0, y = 250, 40, 30, 80
    for i, (t, sub, stop) in enumerate(cols):
        x = x0 + i * (bw + gap)
        s.box(x, y, bw, 108, t, sub, "g")
        if i:
            s.arrow(x - gap + 2, y + 54, x - 2, y + 54, FAINT)
        s.arrow(x + bw // 2, y + 108, x + bw // 2, 244, BAD)
        s.box(x, 244, bw, 46, stop, [], "r")
    s.arrow(x0 + 3 * (bw + gap) + bw, y + 54, 1176, y + 54, FAINT, head=False)
    s.text(322, 332, "all four pass", 11.5, MUTED)
    s.box(30, 340, 560, 84, "Lab adapter", ["simulator, plate reader share, Opentrons", "raw files are kept as the evidence"], "a")
    s.path(f"M{x0 + 3 * (bw + gap) + bw + 4} {y + 54} L 1186 {y + 54} L 1186 316 L 310 316 L 310 340", FAINT)
    s.box(620, 340, 550, 84, "A plan approval covers its own submissions", ["recorded as covered_by_plan", "the safety gate still applies to every one"], "w")
    s.add(f'<rect x="30" y="452" width="1140" height="34" fill="#14233f" stroke="{ACC}" stroke-width="1"/>')
    s.text(46, 474, "Governance is code the agent cannot reach. A pause is scoped (program or campaign), and a halt is global.", 12.5, INK)
    s.save("gates.svg", "The checks between an approved plan and a lab run")


def verdict():
    s = Svg(1200, 520)
    s.text(30, 36, "One rule, fixed before the experiment. It reads the 95% interval, never a point estimate alone.", 13.5, MUTED)
    gx, lo, hi = 560, 150, 950   # gate x, axis
    s.path(f"M{lo} 96 L{hi} 96", LINE2)
    s.text(lo, 84, "lower IC50 is better", 11.5, FAINT)
    s.text(hi, 84, "higher", 11.5, FAINT, anchor="end")
    s.add(f'<rect x="{lo}" y="104" width="{gx - lo}" height="318" fill="#14233f" opacity=".45"/>')
    s.path(f"M{gx} 100 L{gx} 430", WARN, dash=True)
    s.text(gx + 8, 118, "gate, registered beforehand", 12, WARN)
    rows = [(160, 250, 420, "Validated", "interval fully inside the gate", ACC),
            (250, 440, 700, "Abstained", "interval straddles the gate: no claim", ABS),
            (340, 700, 880, "Refuted", "interval fully outside the gate", BAD)]
    for i, (y, a, b, name, note, c) in enumerate(rows):
        yy = 150 + i * 90
        s.path(f"M{a} {yy} L{b} {yy}", c)
        s.path(f"M{a} {yy - 8} L{a} {yy + 8}", c)
        s.path(f"M{b} {yy - 8} L{b} {yy + 8}", c)
        m = (a + b) // 2
        if name == "Abstained":
            s.add(f'<path d="M{m} {yy - 8} L{m + 8} {yy} L{m} {yy + 8} L{m - 8} {yy} Z" fill="{BG}" stroke="{c}" stroke-width="1.6"/>')
        elif name == "Refuted":
            s.add(f'<rect x="{m - 6}" y="{yy - 6}" width="12" height="12" fill="{c}"/>')
        else:
            s.add(f'<circle cx="{m}" cy="{yy}" r="6" fill="{c}"/>')
        s.text(lo - 120, yy + 5, name, 14, c, 600)
        s.text(gx + 14 if name == "Abstained" else a, yy + 30, note, 12, MUTED)
    s.text(lo - 120, 436, "Also abstains: QC failed, fewer than 3 replicates,", 12, MUTED)
    s.text(lo - 120, 454, "or a call that rests on an inferred top plateau.", 12, MUTED)
    s.box(30, 468, 1140, 36, "After the verdict: a validated hit goes to an orthogonal assay, an abstention goes to a person.", [], "g")
    s.save("verdict-rule.svg", "The verdict rule: validated, abstained or refuted from the interval against the gate")


if __name__ == "__main__":
    OUT.mkdir(exist_ok=True)
    architecture(); campaign(); gates(); verdict()
