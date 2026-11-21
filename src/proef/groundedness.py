"""Is a claim about outside information backed by something this run actually retrieved?

A model that cites a paper or a database row can cite a real one it never looked at, or an invented one. The engine cannot tell a true
claim from a false one, but it can tell whether the identifier the claim rests on appears in a tool result of THIS run. That is cheap,
mechanical and needs no network. A claim that does not trace is not shown as evidence, and a citation that does not trace is flagged
(it may still be real: a model's memory is not worthless, it is just not what was read).
"""
from __future__ import annotations

import json
import re
from typing import Any

# the identifiers a claim can rest on: PubMed ids, ChEMBL ids, Ensembl gene ids, DOIs
_ID = re.compile(r"CHEMBL\d+|ENSG\d{6,}|10\.\d{4,9}/[^\s;,]+|(?<![\w.])\d{6,9}(?![\w.])", re.I)
KINDS = ("supports", "contradicts", "prior_measurement", "already_known", "prior")      # "prior": what a prediction or synthesis tool returned, never evidence


def ids_in(text: Any) -> list[str]:
    """The identifiers in a citation or source string, in order, without duplicates."""
    return list(dict.fromkeys(m.group(0).rstrip(".)") for m in _ID.finditer(str(text or ""))))


def haystack(bodies: list[Any]) -> str:
    return json.dumps(bodies, default=str).lower()


def traces(source: Any, bodies: list[Any]) -> bool:
    """True when at least one identifier in `source` appears in what the tools returned. A source with no identifier cannot trace."""
    ids = ids_in(source)
    hay = haystack(bodies)
    return bool(ids) and any(i.lower() in hay for i in ids)


def _prior_ok(source: str, called: set[str], bodies: list[Any]) -> bool:
    """A `prior` source names a compute tool that returned a result in this step: "check_synthesis", "predict", or "predict:<predictor>" where the predictor is
    one a `predict` result in this step actually names."""
    head, _, sub = source.partition(":")
    if head.strip() not in called:
        return False
    if sub.strip():
        return sub.strip() in {b.get("tool") for b in bodies if isinstance(b, dict) and b.get("kind") == "prediction"}
    return True


def verify_items(items: Any, bodies: list[Any], max_items: int = 8, tools_called: tuple[str, ...] | list[str] = ()) -> tuple[list[dict], list[dict]]:
    """Split a model's grounding items into (kept, dropped). Kept: a known kind, a claim, and a source that traces to a result of this run. For a literature or
    database item the source is an identifier in a retrieved result; for a `prior` item it is the NAME of a compute tool that actually returned a result in
    this loop ("predict", "predict:admet-physchem", "check_synthesis"). Dropped items carry the reason, so a person can see what was refused and why."""
    kept: list[dict] = []
    dropped: list[dict] = []
    called = {str(t) for t in tools_called}
    for it in (items if isinstance(items, list) else [])[: max_items * 2]:
        if not isinstance(it, dict):
            dropped.append({"reason": "not an object"})
            continue
        kind, claim, source = str(it.get("kind") or "")[:20], str(it.get("claim") or "")[:300], str(it.get("source") or "")[:120]
        if kind not in KINDS:
            dropped.append({"kind": kind, "claim": claim, "reason": f"kind must be one of {', '.join(KINDS)}"})
        elif not claim:
            dropped.append({"kind": kind, "reason": "no claim"})
        elif kind == "prior" and not _prior_ok(source, called, bodies):
            dropped.append({"kind": kind, "claim": claim, "source": source, "reason": "no such compute tool returned a result in this step"})
        elif kind != "prior" and not traces(source, bodies):
            dropped.append({"kind": kind, "claim": claim, "source": source, "reason": "its source is not in any result this run retrieved"})
        elif len(kept) < max_items:
            kept.append({"kind": kind, "claim": claim, "source": source})
    return kept, dropped


# ---- detectors for what a model says (5F, 5G narrative kinds) -----------------------------------------------------------------------------
# Each is lexical and deterministic. They catch what can be checked mechanically against facts Proef holds (a library, a gate, a ledger,
# a finding); they do NOT judge whether an argument is good. Their precision limits are stated in each docstring and measured in the
# `hallucination` eval on a labelled fixture, so a number here is a measured one.

_UNIT_TO_UM = {"pm": 1e-6, "nm": 1e-3, "um": 1.0, "µm": 1.0, "μm": 1.0, "mm": 1e3}
_NUM_UNIT = re.compile(r"(\d+(?:\.\d+)?)[\s-]*(pM|nM|[uµμ]M|mM)\b", re.I)


def to_um(value: float, unit: str) -> float:
    return float(value) * _UNIT_TO_UM[unit.lower().replace("u", "u")] if unit.lower() in _UNIT_TO_UM else float(value)


def not_in_library(name: str, library: list[str]) -> bool:
    """True when a compound name is not one the library offered (case and surrounding space ignored). Only meaningful when a library was
    offered: with no library every name is the model's own and this says nothing."""
    return bool(library) and name.strip().lower() not in {x.strip().lower() for x in library}


def structure_problem(smiles: str, name: str = "", library: dict[str, str] | None = None) -> str | None:
    """None when the structure is fine; else why not: it does not parse, or it disagrees with the structure the library holds for the name
    (compared as canonical SMILES, so a different spelling of the same molecule passes)."""
    from proef.predictors import _check_smiles
    bad = _check_smiles(smiles)
    if bad:
        return bad
    held = (library or {}).get(name) or (library or {}).get(name.lower())
    if held:
        try:
            from rdkit import Chem
            a, b = Chem.MolFromSmiles(smiles), Chem.MolFromSmiles(held)
            if a is not None and b is not None and Chem.MolToSmiles(a) != Chem.MolToSmiles(b):
                return f"the library holds a different structure for {name}"
        except ImportError:
            return None
    return None


_OBSERVABLE = re.compile(
    r"\d|<|>|≤|≥|\b(below|above|under|over|exceed\w*|less than|greater than|fewer|more than|no (saturable|specific|measurable|detectable|binding|"
    r"inhibition|activity|shift|effect|dose)|fails? to (reach|show|inhibit|bind)|ic50|ec50|kd|ki|potency|fold|ratio|signal|curve|"
    r"saturat\w*|plateau|competition|shift)\b", re.I)
_CIRCULAR = re.compile(r"^\W*(if|when|should)\b.{0,40}\b(does not|doesn'?t|fails?|is wrong|is false|is incorrect|is not (true|correct|valid)|"
                       r"does not work|isn'?t (true|correct|right)|turns out (wrong|false))\b", re.I)


def untestable_refutation(text: str) -> str | None:
    """None when a 'refuted if' clause names something that could be observed; else why not. Lexical: empty, circular ('if it does not
    work') with nothing observable, or nothing observable at all. It cannot tell a sharp clause from a vague one that happens to contain a
    number or the word 'binding'; it catches the obviously empty and obviously circular."""
    t = (text or "").strip()
    if len(t) < 8:
        return "empty"
    if not _OBSERVABLE.search(t):
        return "circular" if _CIRCULAR.search(t) else "no observable or threshold"
    return None


_STATE_WORDS = re.compile(r"\b(gate|threshold|cut-?off|criterion|criteria|success|hit|goal|target|requirement)\b|ic50|potency", re.I)
_CMP = re.compile(r"(below|under|less than|at most|up to|within|sub-?|<=?|≤|above|over|greater than|at least|>=?|≥)\s*(?:an? )?(\d+(?:\.\d+)?)[\s-]*(pM|nM|[uµμ]M|mM)\b", re.I)


def gate_misstated(text: str, threshold_um: float | None, tol: float = 0.01) -> list[dict]:
    """A threshold written in the text that is not the gate's, after converting units ('below 20 nM' against a gate of 20 µM). Only a number
    with a concentration unit and a comparison word, in a sentence that talks about the gate, is considered, so a dose or a measurement
    is not mistaken for a threshold. Does not check direction."""
    if not threshold_um:
        return []
    out = []
    for sent in re.split(r"(?<=[.;!?])\s+|\n", text or ""):
        if not _STATE_WORDS.search(sent):
            continue
        # a sentence that states the RIGHT gate is not misstating it: its other comparisons are predictions or examples ("IC50 below
        # 1 uM, and so below the 20 uM gate"). Added after the first real-model text showed exactly that false positive.
        if any(abs(to_um(float(n.group(1)), n.group(2)) - threshold_um) <= tol * threshold_um for n in _NUM_UNIT.finditer(sent)):
            continue
        for m in _CMP.finditer(sent):
            val = to_um(float(m.group(2)), m.group(3))
            if abs(val - threshold_um) > tol * threshold_um:
                out.append({"stated": f"{m.group(2)} {m.group(3)}", "stated_um": val, "gate_um": threshold_um})
    return out


_QUOTE_CTX = re.compile(r"(ic50|ec50|kd|\bki\b|observed|measured|potency|gave|showed|yielded|reported|value|=)"
                        r"(?:[\s:=~≈]|\b(?:of|was|is|at|about|around|near|approximately|roughly|only|just|to be)\b){0,6}$", re.I)


_LITERATURE = re.compile(r"\b(reported|published|literature|chembl|pubmed|pmid|paper|study|studies|previously|prior work)\b", re.I)


def quoted_number_problems(text: str, findings: list[dict], gate_um: float | None = None, tol: float = 0.05) -> list[dict]:
    """A value quoted for a named candidate that the data does not show. `findings`: [{'candidate', 'observed_um', 'ci_um': (lo, hi)|None}].
    Only a number with a concentration unit, introduced by a measurement word (IC50, observed, measured, gave...), and within 80 characters
    after a candidate's name is checked; it must match the candidate's observed value or a bound of its interval within `tol`. The gate's own
    value is allowed. Names and numbers only: it does not judge whether the claim built on them is right."""
    out = []
    by = {str(f["candidate"]).lower(): f for f in findings}
    low = (text or "").lower()
    for name, f in by.items():
        for nm in re.finditer(re.escape(name), low):
            window = (text or "")[nm.end(): nm.end() + 80]
            if _LITERATURE.search((text or "")[max(0, nm.start() - 30): nm.end() + 80]):
                continue        # "reported IC50 is 506 nM (ChEMBL ...)": a published value, not this campaign's measurement (added after a real flag)
            for m in _NUM_UNIT.finditer(window):
                if not _QUOTE_CTX.search(window[: m.start()]):
                    continue
                val = to_um(float(m.group(1)), m.group(2))
                known = [x for x in [f.get("observed_um"), *(f.get("ci_um") or ()), gate_um] if x is not None]
                if not any(abs(val - k) <= tol * abs(k) for k in known) and not any(
                        o["candidate"] == f["candidate"] and o["quoted"] == f"{m.group(1)} {m.group(2)}" for o in out):
                    out.append({"candidate": f["candidate"], "quoted": f"{m.group(1)} {m.group(2)}", "quoted_um": val,
                                "observed_um": f.get("observed_um")})
    return out


def ruled_out(candidate: str, family: str | None, negatives: list[dict]) -> dict | None:
    """The flywheel negative a proposal repeats: the same candidate, or the same mechanistic family, already refuted or invalidated."""
    c, fam = (candidate or "").strip().lower(), (family or "").strip().lower()
    for n in negatives or []:
        if c and str(n.get("candidate", "")).strip().lower() == c:
            return {**n, "match": "candidate"}
        if fam and str(n.get("family") or "").strip().lower() == fam:
            return {**n, "match": "family"}
    return None


_NEG = re.compile(r"(\bnot\b|n't\b|\bnever\b|\bno need to\b|\bwithout\b|\bfailed to\b|\bunable to\b)\W+(?:\w+\W+){0,2}$", re.I)


def phantom_tool_claims(text: str, called: set[str], offered: set[str]) -> list[str]:
    """Tool names a narrative says it used that the ledger has no call for. A name must be an offered tool and sit within 40 characters
    after a verb of use (ran, called, used, queried, searched, submitted), and the verb must not be negated ('did not run', 'never used').
    Name-based: it cannot tell a call that failed from one that never happened, only that none is on the record. The negation handling was
    added AFTER the first fixture run showed a false positive on 'I did not run predict', so that category is no longer an untouched test."""
    out = []
    for tool in sorted(offered - called):
        for m in re.finditer(rf"\b(ran|run|called|used|queried|searched|submitted|invoked|checked)\b.{{0,40}}\b{re.escape(tool)}\b", text or "", re.I):
            if not _NEG.search((text or "")[max(0, m.start() - 30): m.start()]):
                out.append(tool)
                break
    return out


_VERDICT_CLASS = {"confirmed": "positive", "validated": "positive", "refuted": "negative", "invalidated": "negative", "inconclusive": "abstain",
                  "abstained": "abstain", "abstention": "abstain"}
_STATUS_CLASS = {"confirmed": "positive", "validated": "positive", "refuted": "negative", "invalidated": "negative", "inconclusive": "abstain"}
_WORD_NUM = {"no": 0, "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}


def _num(tok: str) -> int | None:
    t = tok.lower()
    return int(t) if t.isdigit() else _WORD_NUM.get(t)


def state_claims(text: str, verdicts: dict[str, str], queued: int | None = None, runs_left: int | None = None) -> list[dict]:
    """What a narrative (an orchestrator's reason, a follow-up's rationale) says about the campaign's own state, checked against it: a
    candidate's verdict, the number of hypotheses queued, the number of lab runs left. Verdicts are compared by class (confirmed and validated
    are positive, refuted and invalidated negative, inconclusive and abstained neither), so 'refuted' for an 'invalidated' result is not an
    error but 'confirmed' for an inconclusive one is. Names and numbers only."""
    out: list[dict] = []
    t = text or ""
    for cand, status in (verdicts or {}).items():
        actual = _STATUS_CLASS.get(str(status).lower())
        for m in re.finditer(rf"\b{re.escape(str(cand))}\b", t, re.I):
            tail = t[m.end(): m.end() + 45]
            for v in re.finditer(r"\b(inconclusive|invalidated|validated|confirmed|refuted|abstained|abstention)\b", tail, re.I):
                # a verdict word used as an ADJECTIVE is not a claim about the candidate: "a validated follow-up", "a confirmed assay". Added after
                # a real model's "the inconclusive AZ191 result may benefit from a validated follow-up" was flagged as claiming AZ191 was validated.
                before = tail[max(0, v.start() - 10): v.start()].lower()
                after = tail[v.end(): v.end() + 14].lower()
                if re.search(r"\b(a|an|the|any|each|another|prior)\s+$", before) or re.match(r"\s+(follow-?up|assay|run|result|method|test|confirmation|replicate)", after):
                    continue
                if actual and _VERDICT_CLASS[v.group(1).lower()] != actual:
                    out.append({"kind": "verdict", "candidate": cand, "claimed": v.group(1).lower(), "actual": status})
                break
            else:
                continue
            if out and out[-1].get("candidate") == cand:
                break
    if queued is not None:
        for m in re.finditer(r"\b(\d+|no|zero|one|two|three|four|five|six|seven|eight|nine|ten)\b(?:\s+\w+){0,2}?\s+(?:hypothes[ie]s|candidates|variants)\b[^.]{0,30}?\bqueued", t, re.I):
            n = _num(m.group(1))
            if n is not None and n != queued:
                out.append({"kind": "queued", "claimed": n, "actual": queued})
    if runs_left is not None:
        for m in re.finditer(r"\b(\d+)\s+(?:lab\s+)?runs?\s+(?:remain|are left|are available|left|remaining)", t, re.I):
            if int(m.group(1)) != runs_left:
                out.append({"kind": "runs_left", "claimed": int(m.group(1)), "actual": runs_left})
    return out


# ---- what a tool loop's summary says about its own calls and what they returned ----------------------------------------------------------------

_NUM_ANY = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)(?![\w.]*\d)")


def _decomma(t: str) -> str:
    return re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", t)          # 1,100 nM is 1100 nM, not "100 nM"


def _numbers_in(bodies: list[Any]) -> list[float]:
    out: list[float] = []
    for m in _NUM_ANY.finditer(_decomma(json.dumps(bodies, default=str))):
        try:
            out.append(float(m.group(1)))
        except ValueError:
            continue
    return out


def unsupported_values(text: str, bodies: list[Any], extra: str = "", tol: float = 0.02) -> list[dict]:
    """Values a summary states with a concentration unit or a percent sign that appear NOWHERE in what the tools returned or in the task the model was
    given (`extra`: the objective and the round's results; "sub-20uM" is the task's own gate, not a finding) ("fabricated results"). A value counts as
    supported if its number, or the number converted between nM, uM and mM, is in those within `tol`. Lexical: it cannot tell a value attached to the wrong
    compound from a right one, only that it is not in the data at all, and it does not check values written as words."""
    nums = _numbers_in(bodies) + [float(m.group(1)) for m in _NUM_ANY.finditer(_decomma(extra or ""))]
    out = []
    for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(pM|nM|[uµμ]M|mM|%)", _decomma(text or ""), re.I):
        v = float(m.group(1))
        forms = {v} if m.group(2) == "%" else {v, v * 1e3, v / 1e3, v * 1e6, v / 1e6}
        if not any(abs(n - f) <= tol * max(abs(f), 1e-12) for n in nums for f in forms):
            out.append({"stated": f"{m.group(1)} {m.group(2)}"})
    return out


_NAME_KEYS = ("name", "molecule_name", "pref_name", "title", "symbol", "candidate", "molecule_chembl_id")


def _records(x: Any, out: list[dict] | None = None) -> list[dict]:
    out = [] if out is None else out
    if isinstance(x, dict):
        out.append(x)
        for v in x.values():
            _records(v, out)
    elif isinstance(x, list):
        for v in x:
            _records(v, out)
    return out


def misattributed_values(text: str, bodies: list[Any], tol: float = 0.02) -> list[dict]:
    """A value stated right after a compound's name that is in the results, but not in the results' record for THAT compound ("gefitinib 41 nM" when 41 is
    erlotinib's). Only when the results hold a record carrying that name: otherwise there is nothing to compare against. A name is a record's own name field,
    not any string inside it."""
    recs = _records(bodies)
    by_name: dict[str, list[dict]] = {}
    for r in recs:
        for k in _NAME_KEYS:
            if isinstance(r.get(k), str) and len(r[k]) >= 3:
                by_name.setdefault(r[k].strip().lower(), []).append(r)
    out = []
    t = _decomma(text or "")
    for name, rs in by_name.items():
        for nm in re.finditer(rf"(?<![\w-]){re.escape(name)}(?![\w-])", t, re.I):
            window = t[nm.end(): nm.end() + 60]
            nxt = [m2.start() for other in by_name if other != name for m2 in re.finditer(rf"(?<![\w-]){re.escape(other)}(?![\w-])", window, re.I)]
            if nxt:
                window = window[: min(nxt)]            # a value after ANOTHER compound's name belongs to that compound
            vals = [n for r in rs for n in _numbers_in([{k: v for k, v in r.items() if not isinstance(v, (dict, list))}])]
            for m in re.finditer(r"(\d+(?:\.\d+)?)\s*(pM|nM|[uµμ]M|mM|%)", window, re.I):
                v = float(m.group(1))
                forms = {v} if m.group(2) == "%" else {v, v * 1e3, v / 1e3, v * 1e6, v / 1e6}
                if vals and not any(abs(n - f) <= tol * max(abs(f), 1e-12) for n in vals for f in forms):
                    item = {"compound": name, "stated": f"{m.group(1)} {m.group(2)}"}
                    if item not in out:
                        out.append(item)
    return out


def loop_narrative_flags(text: str, calls: list[dict], offered: set[str], bodies: list[Any], extra: str = "") -> list[dict]:
    """Advisory flags on a tool loop's own summary, checked against its own calls and results: a tool it says it used that it never called (`phantom_call`),
    a tool it says it used whose every call was refused so nothing came back (`claimed_refused_call`), a refused call repeated unchanged (`ignored_refusal`),
    and a value it states that is in no result (`unsupported_value`)."""
    out: list[dict] = []
    called = {c["tool"] for c in calls}
    ok_tools = {c["tool"] for c in calls if c.get("status") == "ok"}
    for t in phantom_tool_claims(text, called, offered):
        out.append({"kind": "phantom_call", "tool": t})
    for t in sorted(called - ok_tools - {"list_tools", "describe_tool"}):
        if re.search(rf"\b(ran|run|called|used|queried|searched|invoked|checked|found|returned|according to)\b.{{0,40}}\b{re.escape(t)}\b", text or "", re.I):
            out.append({"kind": "claimed_refused_call", "tool": t})
    seen: set[str] = set()
    for c in calls:
        sig = c.get("sig") or c["tool"] + json.dumps(c.get("args"), sort_keys=True, default=str)
        if c.get("status") == "refused" and sig in seen:
            out.append({"kind": "ignored_refusal", "tool": c["tool"], "code": c.get("code")})
        if c.get("status") == "refused":
            seen.add(sig)
    out += [{"kind": "unsupported_value", **x} for x in unsupported_values(text, bodies, extra)]
    out += [{"kind": "misattributed_value", **x} for x in misattributed_values(text, bodies)]
    return out
