"""Prediction tools: priors from specialist models, registered with a declared contract.

A prediction is never evidence. It can tell a scientist where to look, and it is shown beside a hypothesis in plan review,
labelled as a prior. Only a measurement reaches the verdict rule. Tools are registered, not built in: Proef ships the contract
and a reference tool (docs/DOMAIN-TOOLS.md). An operator enables which tools a campaign may use (a fence, as for instruments),
a model proposes which of those fit each hypothesis, and CODE validates the choice and runs the tool.
"""
from __future__ import annotations

import math
import os
import threading
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class ToolSpec:
    name: str
    endpoints: dict[str, str]                 # endpoint -> unit ("" when unitless)
    input_kind: str                           # what the candidate must provide: "smiles", "sequence", "composition"
    modalities: tuple[str, ...]               # modality pack names this applies to, or ("any",)
    uncertainty: str                          # "interval" | "ensemble" | "none": an uncalibrated tool says so
    version: str
    description: str
    trusted: bool = False                     # first-party code that may run in-process. Anything else is refused (see run)
    timeout_s: float = 10.0                   # a hanging tool must not freeze plan review
    fn: Callable[[str], dict] = field(compare=False, repr=False, default=lambda x: {})
    check_input: Callable[[str], str | None] = field(compare=False, repr=False, default=lambda x: None)


MAX_TOOLS_PER_VARIANT = 8


def _call_with_timeout(fn: Callable[[str], dict], arg: str, timeout_s: float) -> tuple[dict, str | None]:
    """Run a tool with a time limit. A tool that does not return is abandoned (a thread cannot be killed, so it is a daemon
    and its late result is dropped): the plan goes on without it."""
    box: dict[str, Any] = {}

    def work() -> None:
        try:
            box["out"] = fn(arg) or {}
        except Exception as exc:  # noqa: BLE001 - a tool failure must not abort a campaign
            box["err"] = f"{type(exc).__name__}: {str(exc)[:120]}"

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout_s)
    if t.is_alive():
        return {}, f"timed out after {timeout_s:g}s"
    return box.get("out", {}), box.get("err")


def _finite(x: Any) -> bool:
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)


class PredictorRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, spec: ToolSpec) -> ToolSpec:
        if spec.name in self._tools:
            raise ValueError(f"prediction tool {spec.name!r} is already registered")
        self._tools[spec.name] = spec
        return spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def names(self) -> list[str]:
        return sorted(self._tools)

    def applies(self, spec: ToolSpec, modality: str | None) -> bool:
        from proef.modality import pack_or_default
        return "any" in spec.modalities or pack_or_default(modality).name in spec.modalities

    def menu(self, enabled: list[str] | None, modality: str | None) -> list[ToolSpec]:
        """The tools a model may choose from: enabled by the operator AND applicable to the modality."""
        return [s for n in (enabled or []) if (s := self._tools.get(n)) and self.applies(s, modality)]

    def menu_text(self, specs: list[ToolSpec]) -> str:
        lines = []
        for s in specs:
            ends = ", ".join(f"{e}{' (' + u + ')' if u else ''}" for e, u in s.endpoints.items())
            lines.append(f"- {s.name} v{s.version}: predicts {ends}; needs a {s.input_kind}; uncertainty: {s.uncertainty}")
        return "\n".join(lines)

    def run(self, requested: list[str] | None, inputs: dict[str, str], enabled: list[str] | None,
            modality: str | None) -> list[dict]:
        """Validate each requested tool on FACTS (registered and enabled, applies to the modality, the candidate has the
        input and it is valid) and run the ones that pass. A failing tool is reported, never fatal. Every result says it is a
        prior and not evidence, carries the tool version, and keeps only the declared endpoints with their declared units."""
        out = []
        # a model's list is bounded: at most MAX_TOOLS_PER_VARIANT distinct names, each at most 80 characters
        for name in list(dict.fromkeys(str(n)[:80] for n in (requested or [])))[:MAX_TOOLS_PER_VARIANT]:
            base = {"tool": name, "kind": "prediction", "evidence": False}
            spec = self._tools.get(name)
            if spec is None or name not in (enabled or []):
                out.append({**base, "status": "unknown_tool",
                            "reason": f"'{name}' is not a prediction tool this campaign may use"})
                continue
            base["version"] = spec.version
            if not spec.trusted and os.environ.get("PROEF_ALLOW_UNSANDBOXED_TOOLS") != "1":
                # tools run in this process with this process's access. Until the sandbox lanes exist (S-T3 1d/1e), only
                # first-party tools run, unless the operator explicitly accepts the risk
                out.append({**base, "status": "untrusted_tool",
                            "reason": f"{name} is not first-party and tools run in-process: refused unless the operator sets "
                                      "PROEF_ALLOW_UNSANDBOXED_TOOLS=1"})
                continue
            if not self.applies(spec, modality):
                out.append({**base, "status": "wrong_modality", "reason": f"{name} does not apply to this modality"})
                continue
            value = (inputs or {}).get(spec.input_kind)
            if value:
                # the model's claim about the candidate, kept WITH the prediction (not on the hypothesis) so a person can
                # check that the structure is the compound named, and so the record can be audited
                base["input"] = str(value)[:120]
            if not value:
                out.append({**base, "status": "no_input", "reason": f"the candidate has no {spec.input_kind}"})
                continue
            bad = spec.check_input(value)
            if bad:
                out.append({**base, "status": "invalid_input", "reason": bad})
                continue
            raw, err = _call_with_timeout(spec.fn, value, spec.timeout_s)
            if err:
                out.append({**base, "status": "error", "reason": err})
                continue
            got = raw.get("values") if isinstance(raw, dict) and isinstance(raw.get("values"), dict) else {}
            # iterate the DECLARED endpoints, never the tool's own dict, so a flood of keys costs nothing
            values = {e: got[e] for e in spec.endpoints if e in got and (_finite(got[e]) or isinstance(got[e], bool))}
            if not values:
                out.append({**base, "status": "invalid_output", "reason": "no declared endpoint came back with a finite value"})
                continue
            out.append({**base, "status": "ok", "values": values,
                        "units": {e: spec.endpoints[e] for e in values},
                        "in_domain": raw.get("in_domain"), "uncertainty": spec.uncertainty,
                        "interval": raw.get("interval") if spec.uncertainty != "none" else None,
                        "calibrated": spec.uncertainty != "none"})
        return out


DEFAULT = PredictorRegistry()


# ---- the reference tool: physicochemical descriptors from the ADMET module --------------------------------------------

def _check_smiles(smiles: str) -> str | None:
    s = (smiles or "").strip()
    if not s or len(s) > 400 or any(ch.isspace() for ch in s):
        return "not a SMILES string"
    try:
        from rdkit import Chem
        return None if Chem.MolFromSmiles(s) is not None else "RDKit cannot parse this SMILES"
    except ImportError:
        import re
        return None if re.fullmatch(r"[A-Za-z0-9@+\-\[\]\(\)=#$%/\\.:*]+", s) else "unexpected characters for a SMILES string"


def _physchem(smiles: str) -> dict:
    from proef.adapters.admet import get_admet_predictor
    r = get_admet_predictor().predict(smiles)
    values = {"mw_da": r.mw_da, "logp": r.logp, "tpsa": r.tpsa, "lipinski_pass": r.passes_lipinski}
    return {"values": {k: v for k, v in values.items() if v is not None}, "in_domain": True}


DEFAULT.register(ToolSpec(
    name="admet-physchem", version="1", trusted=True,
    endpoints={"mw_da": "Da", "logp": "", "tpsa": "A2", "lipinski_pass": ""},
    input_kind="smiles", modalities=("small molecule", "agrochemical"), uncertainty="none",
    description="Physicochemical descriptors and a Lipinski flag. Deterministic descriptors, not a potency model: "
                "they say nothing about the gate, and carry no uncertainty.",
    fn=_physchem, check_input=_check_smiles))
