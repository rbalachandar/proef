"""Modality packs — how the DMTA flow specializes per therapeutic
modality. The framework is modality-agnostic; a pack is the thin layer
that words the flow and supplies modality-specific safety/assay adapters.

A pack: {production verb (Make), safety rule names (gate), test assays
(Test), notes}. Registering a new modality = one entry here plus real
adapters — not framework changes.
"""
from __future__ import annotations

from dataclasses import dataclass, field


# Assays whose analysis is a dose-response fit: the fit produces ic50_um and lower is better, whatever the program's
# metric is named. Every other assay reads the program's own metric and direction. One definition, used by the
# hypothesis agent, the plan, the toolkit and the simulator.
DOSE_RESPONSE: frozenset[str] = frozenset({"binding", "enzymatic", "cytotoxicity", "counterscreen"})


@dataclass(frozen=True)
class ModalityPack:
    name: str
    production_action: str            # Make wording (virtual adapter)
    test_assays: tuple[str, ...]      # the modality's readouts
    safety_rule_names: tuple[str, ...] = field(
        default=("durc_screen", "sequence_screen", "toxicophores"))
    notes: str = ""
    # domain knowledge as DATA, so the hypothesis code does not fork per domain:
    dose_response_assays: frozenset = DOSE_RESPONSE     # assays judged as ic50_um, lower is better
    prompt_role: str = "drug-discovery program"         # "You are the hypothesis lead of a <this>"
    candidate_kind: str = "compound"                    # what a candidate is: compound, sequence, composition
    candidate_example: str = "erlotinib"                # shown in the prompt as the name-only example
    mechanism_hint: str = "molecules, binding, pathway"  # what a causal story is made of in this domain
    default_assay: str = "custom"                       # the assay a hand-listed candidate is tested by


PACKS: dict[str, ModalityPack] = {
    "small molecule": ModalityPack(
        name="small molecule",
        production_action="synthesized",
        test_assays=("binding dose-response", "counterscreen"),
        safety_rule_names=("toxicophores", "durc_screen"),
        default_assay="binding",
        notes="classic chem DMTA; structural alerts on SMILES"),
    "biologic": ModalityPack(
        name="biologic",
        production_action="expressed and purified",
        test_assays=("binding dose-response", "immunogenicity",
                     "aggregation", "thermal stability"),
        safety_rule_names=("sequence_screen", "durc_screen"),
        default_assay="binding",   # its first test assay is a binding dose-response, the one the virtual lab runs
        prompt_role="biologics-discovery program", candidate_kind="sequence", candidate_example="binder-017",
        mechanism_hint="sequence, fold, interface, epitope",
        notes="Design = sequences + structure models; Make = "
              "bioprocessing batches; immunogenicity/aggregation need "
              "real-assay adapters (virtual lab runs binding only)"),
    "cell therapy": ModalityPack(
        name="cell therapy",
        production_action="engineered and expanded",
        test_assays=("potency", "sterility", "phenotype stability"),
        safety_rule_names=("sequence_screen", "durc_screen"),
        notes="slowest Make of any modality — Design-phase pressure is "
              "highest (get the construct right before committing)"),
    "mRNA": ModalityPack(
        name="mRNA",
        production_action="transcribed and encapsulated",
        test_assays=("expression", "stability", "immunogenicity"),
        safety_rule_names=("sequence_screen", "durc_screen")),
"material": ModalityPack(
        name="material",
        production_action="produced",
        test_assays=("property sweep",),
        safety_rule_names=("durc_screen",),
        dose_response_assays=frozenset(),
        prompt_role="materials-discovery program", candidate_kind="composition or sample",
        candidate_example="Li7La3Zr2O12", mechanism_hint="composition, structure, phase, defects, processing",
        notes="Analyze reads any registered metric (tensile strength, "
              "conductivity, heat resistance) against the program gate; "
              "sweep simulator + analysis adapter shipped "
              "(assays/simulate.simulate_property_sweep, "
              "assays.properties.read_property_sweep)"),
    "agrochemical": ModalityPack(
        name="agrochemical",
        production_action="synthesized",
        test_assays=("organism dose-response", "phytotoxicity"),
        safety_rule_names=("toxicophores", "durc_screen"),
        default_assay="cytotoxicity",   # an organism dose-response: the standard Hill-fit engine applies unchanged
        notes="same chemical DMTA; Test targets organisms, not humans — "
              "the standard dose-response engine (SimulatorLab, Hill "
              "fits, CIs) applies unchanged")
}


def pack_or_default(modality: str | None) -> ModalityPack:
    """The pack for a modality, or the small-molecule pack for an unknown or empty one (the behaviour before packs
    carried domain data)."""
    return get_pack(modality or "") or PACKS["small molecule"]


def metric_for_assay(assay_type: str, modality: str | None = None) -> str | None:
    """'ic50_um' when this assay is a dose-response fit in the modality, else None (read the program's own metric)."""
    return "ic50_um" if str(assay_type) in pack_or_default(modality).dose_response_assays else None


def get_pack(modality: str) -> ModalityPack | None:
    """Case- and whitespace-tolerant lookup (mRNA pack included)."""
    m = (modality or "").strip()
    if not m:
        return None
    return PACKS.get(m) or next(
        (p for k, p in PACKS.items() if k.lower() == m.lower()), None)
