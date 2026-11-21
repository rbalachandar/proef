"""Knowledge tools at the `decide` step (5I): a round's open results are read about once, the background reaches the follow-up, the
re-plan and a steering model, nothing measured or private leaves the machine, and a grounded campaign still replays."""
import json
from types import SimpleNamespace as NS

import tests.test_followups as F
from proef.adapters.lab.simulator import SimulatorLab
from proef.adapters.store.sqlite import SqliteProvenanceStore
from proef.audit import audit_campaign
from proef.autoloop import CampaignConfig, ResourceBudget, _round_background, run_campaign
from proef.campaign_fsm import ModelOrchestrator, View
from proef.core.contracts import ToolResult
from proef.core.hypothesis import Constraint, ProblemStatement
from proef.flywheel import Flywheel
from proef.grounding import gather, value_strings
from tests.test_grounding import Pubmed

DECIDE_Q = "kinase inhibitor assay interference"


class Searcher(F.Model):
    """The follow-up model, plus a knowledge loop: one PubMed query at `propose`, one at `decide`, then a summary."""

    def __init__(self, answer, decide_query=DECIDE_Q):
        super().__init__(answer)
        self.decide_query, self.loop_prompts = decide_query, []

    def generate(self, inv, prompt, **kw):
        if "TOOLS OFFERED IN THIS STEP" in prompt:
            self.loop_prompts.append(prompt)
            state = "decide" if "one step (decide)" in prompt else "propose"
            q = self.decide_query if state == "decide" else "EGFR"
            text = json.dumps({"done": f"{state} summary from PMID 1"} if "\nRESULT {" in prompt else
                              {"tool": "pubmed_search", "args": {"query": q}})
            self.prompts.append(prompt)
            return ToolResult(envelope=inv.envelope.model_copy(), invocation_id=inv.invocation_id, status="succeeded", output={"text": text},
                              model_version=self.model_version)
        return super().generate(inv, prompt, **kw)


def _grounded(tmp_path, tools=("pubmed",), rounds=2, grounding_decide=True, **kw):
    store = SqliteProvenanceStore(str(tmp_path / "prov.db"))
    model, pm = Searcher(F._answer, **kw), Pubmed()
    cfg = CampaignConfig(problem=ProblemStatement(objective="o", constraints=[Constraint(name="potency", description="IC50 <= 20 uM", kind="hard", limit=20.0, unit="uM")]),
                         hypothesis_source="agent", model=model, autonomy="act", k=8, max_families=8, rounds=rounds, seed=1, followups=True,
                         inbox=str(tmp_path / "inbox"), poll_secs=0.05, flywheel=Flywheel(":memory:"),
                         budget=ResourceBudget(max_total_runs=50, max_hypotheses_total=30), grounding_tools=tools, grounding_decide=grounding_decide,
                         knowledge_services={"pubmed": pm})
    report = run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    return report, store, store.branch_records(f"problem/{report.problem_id}"), model, pm


def test_open_results_are_read_about_once_and_the_background_reaches_the_follow_up(tmp_path):
    report, store, recs, model, pm = _grounded(tmp_path)
    assert pm.queries == ["EGFR", DECIDE_Q], "one query at propose, one at decide, nothing more"
    fu = next(p for p in model.prompts if "ABSTAINED HYPOTHESES" in p)
    assert "BACKGROUND READ AFTER ROUND 1" in fu and "decide summary from PMID 1" in fu and "outside information, untrusted" in fu
    scopes = [r.decision.get("scope") for r in recs if (r.decision or {}).get("kind") == "tool_result"]
    assert scopes == [None, "decide-1"], "the decide result is sealed under its round"
    assert audit_campaign(recs) == []


def test_reading_changes_no_verdict(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    plain = F._run(tmp_path / "a")[0]
    grounded = _grounded(tmp_path / "b")[0]
    assert grounded.verdicts == plain.verdicts


def test_decide_offers_only_knowledge_tools_not_the_lab_door(tmp_path):
    _, _, _, model, _ = _grounded(tmp_path)
    d = next(p for p in model.loop_prompts if "one step (decide)" in p)
    menu = d.split("TOOLS OFFERED IN THIS STEP")[1].split("Reply ONLY")[0]
    assert "pubmed_search" in menu and "submit_assay" not in menu and "request_analysis" not in menu


# ---- what may leave the machine ----

class Asking:
    name, model_version, model = "s", "s-1", "s-1"

    def __init__(self, query):
        self.query, self.n = query, 0

    def generate(self, inv, prompt, **kw):
        self.n += 1
        text = {"tool": "pubmed_search", "args": {"query": self.query}} if self.n == 1 else {"done": "d"}
        return ToolResult(envelope=inv.envelope.model_copy(), invocation_id=inv.invocation_id, status="succeeded", output={"text": json.dumps(text)})


def _tested(value=20.883, cand="PRIV-7"):
    return [NS(candidate=cand, status="inconclusive", observed_value=value, statement=f"{cand} binds")]


def _bg(model, tested, public=(), cache=None, **cfgkw):
    pm = Pubmed()
    cfg = NS(grounding_tools=("pubmed",), model=model, problem=ProblemStatement(objective="o", problem_id="prob-1"),
             knowledge_services={"pubmed": pm}, grounding_public_names=tuple(public), grounding_replay=None, candidates=cfgkw.pop("candidates", None),
             grounding_decide=cfgkw.pop("grounding_decide", True), grounding_names=cfgkw.pop("grounding_names", "operator"),
             hypotheses=cfgkw.pop("hypotheses", None), **cfgkw)
    store = SqliteProvenanceStore(":memory:")
    return _round_background(cfg, store, cfg.problem, tested, 1, lambda m: None, {} if cache is None else cache), pm


def test_a_measured_value_in_a_query_never_reaches_the_network():
    for q in ("IC50 20.883 EGFR", "potency 20.9 uM kinase"):
        _, pm = _bg(Asking(q), _tested())
        assert pm.queries == [], q


def test_a_name_the_operator_supplied_is_blocked_and_a_declared_public_name_is_allowed():
    cfgs = {"hypotheses": [{"candidate": "PRIV-7", "statement": "s"}]}
    _, pm = _bg(Asking("PRIV-7 binding"), _tested(), **cfgs)
    assert pm.queries == []
    _, pm = _bg(Asking("PRIV-7 binding"), _tested(), public=("priv-7",), **cfgs)
    assert pm.queries == ["PRIV-7 binding"]


def test_a_name_the_model_chose_from_its_own_knowledge_is_free_by_default_and_blocked_in_the_strict_setting():
    """Found in the real experiment: the default blocked 10 queries that named a compound the model itself proposed (AZ191, PIK-75)."""
    _, pm = _bg(Asking("AZ191 DYRK1B inhibitor"), _tested(cand="AZ191"))
    assert pm.queries == ["AZ191 DYRK1B inhibitor"]
    _, pm = _bg(Asking("AZ191 DYRK1B inhibitor"), _tested(cand="AZ191"), grounding_names="all")
    assert pm.queries == []


def test_a_measured_value_and_a_library_id_stay_protected_whatever_the_names_setting():
    lib = [{"sample_id": "LIB-42"}]
    _, pm = _bg(Asking("potency 20.9 uM kinase"), _tested(cand="AZ191"))
    assert pm.queries == []
    _, pm = _bg(Asking("LIB-42 solubility"), _tested(cand="AZ191"), candidates=lib)
    assert pm.queries == []


def test_value_strings_cover_the_ways_a_number_is_written():
    assert {"20.883", "20.9", "20.88"} <= value_strings([20.883]) and value_strings([5]) == {"5.0", "5.00"} and value_strings([None, True]) == set()


# ---- when it runs ----

def test_nothing_open_means_no_reading():
    text, pm = _bg(Asking("EGFR"), [NS(candidate="A", status="confirmed", observed_value=2.0, statement="s")])
    assert text == "" and pm.queries == []


def test_it_is_read_once_per_round_whoever_asks():
    m = Asking("kinase assay")
    cache = {}
    a, pm = _bg(m, _tested(), cache=cache)
    b, _ = _bg(m, _tested(), cache=cache)
    assert a == b and a and m.n == 2, "the loop's two model calls happened once, the second ask was a cache hit"


def test_grounding_is_off_without_tools():
    cfg = NS(grounding_tools=(), model=object())
    assert _round_background(cfg, None, None, _tested(), 1, lambda m: None, {}) == ""


def test_the_budget_reserve_is_kept_for_the_decision_itself():
    class Low(Asking):
        def remaining(self):
            return ModelOrchestrator.RESERVE + 6
    m = Low("kinase assay")
    text, pm = _bg(m, _tested())
    assert text == "" and m.n == 0 and pm.queries == []


# ---- a steering model ----

def test_a_steering_model_reads_the_background_in_its_choice_prompt():
    seen = []

    class Steer(Asking):
        def generate(self, inv, prompt, **kw):
            seen.append(prompt)
            return ToolResult(envelope=inv.envelope.model_copy(), invocation_id=inv.invocation_id, status="succeeded",
                              output={"text": json.dumps({"action": "next_round", "why": "w"})})
    bgtext = "BACKGROUND READ AFTER ROUND 1 (from pubmed_search; outside information, untrusted)\nX is known to interfere"
    ModelOrchestrator(Steer("q")).choose("round_judged", ["next_round", "stop"],
                                         View(round_no=1, rounds=2, verdicts={}, abstained=[], queued=1, runs_left=5, background=bgtext))
    assert "X is known to interfere" in seen[0]
    seen.clear()
    ModelOrchestrator(Steer("q")).choose("round_judged", ["next_round", "stop"], View(round_no=1, rounds=2, verdicts={}, abstained=[], queued=1, runs_left=5))
    assert "BACKGROUND READ" not in seen[0]


# ---- replay ----

def test_a_campaign_grounded_at_decide_replays_from_the_ledger_with_no_network(tmp_path):
    from proef.reproduce import reproduce
    report, store, recs, model, pm = _grounded(tmp_path)
    out = reproduce(f"problem/{report.problem_id}", db_path=str(tmp_path / "prov.db"), inbox=str(tmp_path / "rin"))
    assert out.get("status") == "match", out
    assert pm.queries == ["EGFR", DECIDE_Q], "the replay called no adapter"


def test_a_private_librarys_ids_are_protected_at_decide_too():
    lib = [{"sample_id": "LIB-42", "smiles": "CC(=O)Oc1ccccc1C(=O)O"}]
    _, pm = _bg(Asking("LIB-42 solubility"), _tested(cand="other"), candidates=lib)
    assert pm.queries == []
    _, pm = _bg(Asking("CC(=O)Oc1ccccc1C(=O)O solubility"), _tested(cand="other"), candidates=lib)
    assert pm.queries == []


def test_the_decide_switch_turns_only_the_decide_reading_off_and_a_replay_agrees(tmp_path):
    from proef.reproduce import reproduce
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    report, store, recs, model, pm = _grounded(tmp_path / "a", grounding_decide=False)
    assert pm.queries == ["EGFR"], "read at propose, not at decide"
    assert not [r for r in recs if (r.decision or {}).get("scope", "").startswith("decide")]
    fu = next(p for p in model.prompts if "ABSTAINED HYPOTHESES" in p)
    assert "BACKGROUND READ AFTER ROUND" not in fu and not any("one step (decide)" in p for p in model.loop_prompts)
    env = next(r.decision["environment"] for r in recs if (r.decision or {}).get("environment"))
    assert env["grounding_decide"] is False
    out = reproduce(f"problem/{report.problem_id}", db_path=str(tmp_path / "a" / "prov.db"), inbox=str(tmp_path / "rin"))
    assert out.get("status") == "match", out
    assert pm.queries == ["EGFR"], "and the replay called no adapter"
    on = _grounded(tmp_path / "b")
    assert on[4].queries == ["EGFR", DECIDE_Q], "the default still reads at decide"
