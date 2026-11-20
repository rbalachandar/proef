"""The `ground` step (5H): evidence for and against each family is looked for before plan review, and only what traces to a result this
run retrieved is shown as evidence."""
import json

from proef.adapters.lab.simulator import SimulatorLab
from proef.adapters.store.sqlite import SqliteProvenanceStore
from proef.audit import audit_campaign
from proef.autoloop import CampaignConfig, run_campaign
from proef.core.contracts import ToolResult
from proef.core.hypothesis import Constraint, ProblemStatement
from proef.flywheel import Flywheel
from proef.groundedness import ids_in, traces, verify_items
from proef.plan import PlanDecision, build_plan

PMID = "7654321"
PLAN = {"families": [{"family": "f", "parent_statement": "claim", "kind": "mechanistic", "mechanism": "m", "refutation_condition": "r",
                      "confidence": 0.6, "citation": "PMID 1111111",
                      "variants": [{"statement": "A binds", "candidate": "A", "assay_type": "binding", "predicted": "p", "predicted_value": 20.0,
                                    "rationale": "x"}]}]}
ITEMS = {"items": [{"kind": "supports", "claim": "a paper reports activity", "source": f"PMID {PMID}"},
                   {"kind": "contradicts", "claim": "invented", "source": "PMID 9999999"},
                   {"kind": "supports", "claim": "no source at all", "source": ""}]}


class Pubmed:
    def __init__(self):
        self.queries = []

    def search_and_fetch(self, q, n=5):
        self.queries.append(q)
        return [{"pmid": PMID, "title": "T", "abstract": "A reports activity"}]


class Model:
    name, model_version, model = "scripted", "s-1", "scripted-1"

    def __init__(self):
        self.prompts = []

    def generate(self, inv, prompt, **kw):
        self.prompts.append(prompt)
        if "TOOLS OFFERED IN THIS STEP" in prompt:
            if "FAMILY:" in prompt:
                text = {"done": ITEMS} if "\nRESULT {" in prompt else {"tool": "pubmed_search", "args": {"query": "kinase activity"}}
            else:
                text = {"done": "background"}
            text = json.dumps(text)
        elif "hypothesis lead" in prompt:
            text = json.dumps(PLAN)
        else:
            text = json.dumps({"notes": []})
        return ToolResult(envelope=inv.envelope.model_copy(), invocation_id=inv.invocation_id, status="succeeded", output={"text": text},
                          model_version=self.model_version)

    def predict(self, inv, kind, payload):
        raise NotImplementedError


def _run(tmp_path, ground=True, tools=("pubmed",)):
    seen = []
    store = SqliteProvenanceStore(str(tmp_path / "p.db"))
    pm = Pubmed()
    cfg = CampaignConfig(problem=ProblemStatement(objective="o", constraints=[Constraint(name="potency", description="IC50 <= 20 uM", kind="hard", limit=20.0, unit="uM")]),
                         hypothesis_source="agent", model=Model(), autonomy="gate", k=2, seed=1, inbox=str(tmp_path / "in"), poll_secs=0.05,
                         flywheel=Flywheel(":memory:"), grounding_tools=tools, ground_hypotheses=ground, knowledge_services={"pubmed": pm},
                         plan_approver=lambda plan, pid: (seen.append(plan), PlanDecision(approved=True, by="t"))[1])
    rep = run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    return rep, store, store.branch_records(f"problem/{rep.problem_id}"), cfg.model, pm, seen


# ---- the check, on its own ----

def test_ids_are_read_from_the_ways_a_source_is_written():
    assert ids_in("PMID 38493304; CHEMBL553, 10.1056/NEJMoa1411817.") == ["38493304", "CHEMBL553", "10.1056/NEJMoa1411817"]
    assert ids_in("version 5.0 of 12345") == []


def test_a_source_traces_only_if_an_identifier_is_in_a_result_and_a_bare_claim_never_does():
    assert traces("PMID 7654321", [{"pmid": "7654321"}]) and not traces("PMID 9999999", [{"pmid": "7654321"}]) and not traces("", [{"a": 1}])


def test_untraced_untyped_and_empty_items_are_dropped_with_a_reason():
    kept, dropped = verify_items(ITEMS["items"] + [{"kind": "rumour", "claim": "c", "source": f"PMID {PMID}"}, "junk"], [{"pmid": PMID}])
    assert [k["source"] for k in kept] == [f"PMID {PMID}"]
    reasons = " | ".join(d["reason"] for d in dropped)
    assert "not in any result" in reasons and "kind must be one of" in reasons and "not an object" in reasons and len(dropped) == 4


def test_text_is_bounded_and_the_number_of_items_capped():
    big = [{"kind": "supports", "claim": "c" * 5000, "source": f"PMID {PMID} " + "x" * 500} for _ in range(30)]
    kept, _ = verify_items(big, [{"pmid": PMID}], max_items=8)
    assert len(kept) == 8 and all(len(k["claim"]) <= 300 and len(k["source"]) <= 120 for k in kept)


# ---- in a campaign ----

def test_each_family_is_grounded_before_plan_review_and_only_traced_items_reach_the_plan(tmp_path):
    rep, store, recs, model, pm, seen = _run(tmp_path)
    g = [r.decision for r in recs if (r.decision or {}).get("kind") == "grounding"]
    assert len(g) == 1 and g[0]["family"] == "f" and g[0]["stage"] == "initial"
    assert [i["source"] for i in g[0]["items"]] == [f"PMID {PMID}"] and g[0]["n_dropped"] == 2 and g[0]["tool_calls"] == 1
    fam = seen[0]["families"][0]
    assert fam["grounding"]["items"][0]["claim"] == "a paper reports activity" and fam["grounding"]["dropped"] == 2
    order = [(r.decision or {}).get("kind") for r in recs if (r.decision or {}).get("kind") in ("grounding", "plan_decision")]
    assert order == ["grounding", "plan_decision"], "looked for BEFORE the person decides"
    assert audit_campaign(recs) == []


def test_a_citation_the_run_never_retrieved_is_flagged_in_the_plan(tmp_path):
    _, _, _, _, _, seen = _run(tmp_path)
    fam = seen[0]["families"][0]
    assert fam["citation"] == "PMID 1111111" and fam["citation_traced"] is False
    assert [w["kind"] for w in seen[0]["warnings"] if w["kind"] == "untraced_citation"] == ["untraced_citation"]


def test_a_traced_citation_is_not_flagged():
    class H:
        hypothesis_id, candidate, statement, predicted, rationale, assay_type = "h", "A", "s", "p", "r", "binding"
        family, mechanism, refutation_condition, citation, citation_status, metadata = "f", "m", "r", f"PMID {PMID}", "verified", {}
    plan = build_plan([H()], {"f": {}}, {}, retrieved=[{"pmid": PMID}])
    assert plan["families"][0]["citation_traced"] is True and not [w for w in plan["warnings"] if w["kind"] == "untraced_citation"]


def test_without_the_switch_nothing_is_looked_up_and_the_plan_is_unchanged(tmp_path):
    _, _, recs, _, pm, seen = _run(tmp_path, ground=False)
    assert pm.queries == [] or all(q != "kinase activity" for q in pm.queries)
    assert not [r for r in recs if (r.decision or {}).get("kind") == "grounding"]
    assert seen[0]["families"][0].get("grounding") is None and "citation_traced" not in seen[0]["families"][0]


def test_grounding_changes_no_verdict_and_no_approval(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    on = _run(tmp_path / "a", ground=True)[0]
    off = _run(tmp_path / "b", ground=False, tools=())[0]
    assert on.verdicts == off.verdicts


def test_a_grounded_plan_replays_from_the_ledger_with_no_network(tmp_path):
    from proef.reproduce import reproduce
    rep, store, recs, model, pm, _ = _run(tmp_path)
    before = list(pm.queries)
    out = reproduce(f"problem/{rep.problem_id}", db_path=str(tmp_path / "p.db"), inbox=str(tmp_path / "rin"))
    assert out.get("status") == "match", out
    assert pm.queries == before, "the replay called no adapter"


def test_the_plan_api_schema_accepts_grounding_and_the_untraced_citation_warning(tmp_path):
    """The workbench reads the plan through this schema. A warning kind it did not allow would have failed validation and hidden the plan."""
    from proef.api.v1.schemas import Plan
    _, _, _, _, _, seen = _run(tmp_path)
    p = Plan.model_validate(seen[0])
    assert p.families[0].grounding.items[0].source == f"PMID {PMID}" and p.families[0].grounding.dropped == 2
    assert p.families[0].citation_traced is False and [w.kind for w in p.warnings] == ["untraced_citation"]
    assert p.warnings[0].family == "f" and p.warnings[0].candidate == ""
