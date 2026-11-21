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
    assert p.families[0].citation_traced is False
    w = next(w for w in p.warnings if w.kind == "untraced_citation")
    assert w.family == "f" and w.candidate == ""


def test_a_model_that_hands_back_the_items_without_the_done_wrapper_is_not_thrown_away():
    """Seen in the first real run of the ground step: the model's whole answer, {"items": [...]}, was discarded as malformed on its last step."""
    from proef.adapters.store.sqlite import SqliteProvenanceStore
    from proef.core.interfaces import system_actor
    from proef.toolloop import ToolLoop
    from proef.tools import ToolCatalog, ToolContext, pubmed_tool
    cat = ToolCatalog()
    cat.register(pubmed_tool())

    class Bare(Model):
        def generate(self, inv, prompt, **kw):
            text = ({"tool": "pubmed_search", "args": {"query": "kinase"}} if "\nRESULT {" not in prompt else ITEMS)     # the object itself, no "done"
            return ToolResult(envelope=inv.envelope.model_copy(), invocation_id=inv.invocation_id, status="succeeded", output={"text": json.dumps(text)})

    def ctx():
        return ToolContext(state="propose", autonomy="act", store=SqliteProvenanceStore(":memory:"), actor=system_actor("t"), branch="t",
                           services={"pubmed": Pubmed()}, egress_ok={"pubmed_search"})
    res = ToolLoop(Bare(), cat, ctx(), done_keys=("items",)).run("t")
    assert res.done and json.loads(res.done)["items"][0]["source"] == f"PMID {PMID}" and res.stopped is None
    strict = ToolLoop(Bare(), cat, ctx(), max_bad_outputs=0).run("t")
    assert strict.done is None and strict.stopped == "bad_output", "without the keys a caller expects, a bare object is still malformed (the protocol is unchanged)"


def test_the_ground_task_states_the_exact_reply_shape():
    from proef.grounding import GROUND_TASK
    assert '{{"done": {{"items"' in GROUND_TASK and "EXACTLY this shape" in GROUND_TASK


# ---- compute tools in the ground loop (a prior, never evidence) ----

SMILES = "CC(=O)Oc1ccccc1C(=O)O"
PRIOR_PLAN = {"families": [{"family": "f", "parent_statement": "claim", "kind": "mechanistic", "mechanism": "m", "refutation_condition": "No binding below 20 uM",
                            "confidence": 0.6, "citation": "", "variants": [{"statement": "A binds", "candidate": "A", "assay_type": "binding", "predicted": "p",
                                                                             "predicted_value": 20.0, "rationale": "x", "smiles": SMILES, "tools": ["admet-physchem"]}]}]}


class PriorModel(Model):
    """In the ground loop: read, run the predictor and the synthesis check on the compound's structure, then report one traced item and priors (one real tool, one invented)."""

    def generate(self, inv, prompt, **kw):
        if "TOOLS OFFERED IN THIS STEP" in prompt and "FAMILY:" in prompt:
            self.prompts.append(prompt)
            n = prompt.count("\nCALL {")
            if n == 0:
                out = {"tool": "pubmed_search", "args": {"query": "kinase activity"}}
            elif n == 1 and "predict(" in prompt:
                out = {"tool": "predict", "args": {"tool": "admet-physchem", "smiles": SMILES}}
            elif n == 2 and "check_synthesis(" in prompt:
                out = {"tool": "check_synthesis", "args": {"smiles": SMILES}}
            else:
                out = {"done": {"items": [{"kind": "supports", "claim": "a paper reports activity", "source": f"PMID {PMID}"},
                                          {"kind": "prior", "claim": "Lipinski pass (a prior)", "source": "predict:admet-physchem"},
                                          {"kind": "prior", "claim": "modelled as makeable (a prior)", "source": "check_synthesis"},
                                          {"kind": "prior", "claim": "a model I never ran says it is potent", "source": "predict:invented-model"}]}}
            return ToolResult(envelope=inv.envelope.model_copy(), invocation_id=inv.invocation_id, status="succeeded", output={"text": json.dumps(out)},
                              model_version=self.model_version)
        if "hypothesis lead" in prompt:
            self.prompts.append(prompt)
            return ToolResult(envelope=inv.envelope.model_copy(), invocation_id=inv.invocation_id, status="succeeded", output={"text": json.dumps(PRIOR_PLAN)},
                              model_version=self.model_version)
        return super().generate(inv, prompt, **kw)


def _run_prior(tmp_path, compute=("predict", "synthesis")):
    seen = []
    store = SqliteProvenanceStore(str(tmp_path / "p.db"))
    pm = Pubmed()
    model = PriorModel()
    cfg = CampaignConfig(problem=ProblemStatement(objective="o", constraints=[Constraint(name="potency", description="IC50 <= 20 uM", kind="hard", limit=20.0, unit="uM")]),
                         hypothesis_source="agent", model=model, autonomy="gate", k=2, seed=1, inbox=str(tmp_path / "in"), poll_secs=0.05,
                         flywheel=Flywheel(":memory:"), grounding_tools=("pubmed",), ground_hypotheses=True, ground_compute=compute, prediction_tools=["admet-physchem"],
                         knowledge_services={"pubmed": pm}, plan_approver=lambda plan, pid: (seen.append(plan), PlanDecision(approved=True, by="t"))[1])
    rep = run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    return rep, store.branch_records(f"problem/{rep.problem_id}"), model, seen


def test_a_prior_item_is_kept_only_if_that_compute_tool_returned_a_result_in_the_step():
    items = [{"kind": "prior", "claim": "c", "source": "predict:admet-physchem"}, {"kind": "prior", "claim": "c", "source": "check_synthesis"},
             {"kind": "prior", "claim": "c", "source": "predict:invented"}, {"kind": "prior", "claim": "c", "source": ""}]
    kept, dropped = verify_items(items, [{"kind": "prediction", "tool": "admet-physchem", "status": "ok"}], tools_called=["predict"])
    assert [k["source"] for k in kept] == ["predict:admet-physchem"] and len(dropped) == 3, "a predictor the step never ran is not a source"
    assert all("no such compute tool" in d["reason"] for d in dropped)
    assert verify_items([{"kind": "prior", "claim": "c", "source": "predict"}], [])[0] == [], "no tools called, no prior"
    assert verify_items([{"kind": "supports", "claim": "c", "source": "PMID 7654321"}], [{"p": "7654321"}])[0], "literature items still trace to identifiers"


def test_the_ground_loop_runs_the_compute_tools_on_the_compounds_structure_and_keeps_only_the_priors_a_tool_returned(tmp_path):
    rep, recs, model, seen = _run_prior(tmp_path)
    task = next(p for p in model.prompts if "FAMILY:" in p)
    assert f"A (structure as the model stated it: {SMILES})" in task, "the model's own SMILES is labelled as its claim"
    assert "PRIOR" in task and "never evidence" in task and 'already_known|prior"' in task
    g = next(r.decision for r in recs if (r.decision or {}).get("kind") == "grounding")
    assert [i["kind"] for i in g["items"]] == ["supports", "prior", "prior"] and g["n_dropped"] == 1 and g["tool_calls"] == 3
    assert "invented" in json.dumps(g["dropped"])
    sealed = [r.decision for r in recs if (r.decision or {}).get("kind") == "tool_result" and r.decision["tool"] in ("predict", "check_synthesis")]
    assert {d["tool"] for d in sealed} == {"predict", "check_synthesis"} and all(d["scope"].startswith("ground-initial-") for d in sealed), "sealed under the ground scope"
    calls = [r.decision for r in recs if (r.decision or {}).get("kind") == "tool_call" and r.decision["class"] == "compute"]
    assert [c["status"] for c in calls] == ["ok", "ok"]
    fam = seen[0]["families"][0]["grounding"]["items"]
    assert [i["kind"] for i in fam] == ["supports", "prior", "prior"], "the plan a person reviews carries the priors, labelled as priors"
    from proef.api.v1.schemas import Plan
    assert Plan.model_validate(seen[0]).families[0].grounding.items[1].kind == "prior"
    assert audit_campaign(recs) == []


def test_without_the_switch_the_compute_tools_are_not_offered_in_the_ground_loop_and_a_prior_is_dropped(tmp_path):
    rep, recs, model, _ = _run_prior(tmp_path, compute=())
    task = next(p for p in model.prompts if "FAMILY:" in p)
    assert "structure as the model stated it" not in task and 'already_known|prior"' not in task and "COMPOUNDS: A\n" in task
    menu = task.split("TOOLS OFFERED IN THIS STEP")[1].split("Reply ONLY")[0]
    assert "predict(" not in menu and "check_synthesis(" not in menu
    g = next(r.decision for r in recs if (r.decision or {}).get("kind") == "grounding")
    assert [i["kind"] for i in g["items"]] == ["supports"] and g["n_dropped"] == 3


def test_a_campaign_that_used_compute_in_the_ground_loop_replays_from_the_ledger_with_no_model_and_no_network(tmp_path):
    from proef.reproduce import reproduce
    rep, recs, model, _ = _run_prior(tmp_path)
    out = reproduce(f"problem/{rep.problem_id}", db_path=str(tmp_path / "p.db"), inbox=str(tmp_path / "rin"))
    assert out.get("status") == "match", out
