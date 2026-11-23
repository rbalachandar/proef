"""One campaign with EVERY opt-in feature switched on, replayed from its ledger with no model and no network.

This replaces a separate "...replays from the ledger" test for each feature (grounding at propose, grounding at decide, the decide switch, blinding, the ground step,
compute tools at propose, compute tools in the ground loop), each of which ran its own full campaign and replay. One run through all of them proves sealed playback
end to end; what each feature must RESTORE on replay (the recorded setting) is checked cheaply by the unit tests at the bottom, which call the config-restoring function
directly."""
import json
from dataclasses import fields

import pytest

import tests.test_followups as F
from proef.adapters.lab.simulator import SimulatorLab
from proef.adapters.store.sqlite import SqliteProvenanceStore
from proef.audit import audit_campaign
from proef.autoloop import CampaignConfig, ResourceBudget, run_campaign
from proef.core.contracts import ToolResult
from proef.core.hypothesis import Constraint, ProblemStatement
from proef.flywheel import Flywheel
from proef.plan import PlanDecision
from proef.reproduce import _agent_cfg, reproduce

PMID = "7654321"
SMILES = "CC(=O)Oc1ccccc1C(=O)O"


class Pubmed:
    def __init__(self):
        self.queries = []

    def search_and_fetch(self, q, n=5, **kw):
        self.queries.append(q)
        return [{"pmid": PMID, "title": "T", "abstract": "A reports activity"}]


class AllFeatures(F.Model):
    """The follow-up scenario (C1..C8, some abstain), plus every loop: reading at propose (with a compute tool), reading at decide, and the ground step (with compute
    tools)."""

    def __init__(self, answer):
        super().__init__(answer)

    def _loop(self, prompt):
        n = prompt.count("\nCALL {")
        decide = "one step (decide)" in prompt
        ground = "FAMILY:" in prompt
        if n == 0:
            return {"tool": "pubmed_search", "args": {"query": "decide kinase" if decide else "kinase activity"}}
        if not decide and n == 1 and "predict(" in prompt:
            return {"tool": "predict", "args": {"tool": "admet-physchem", "smiles": SMILES}}
        if ground and n == 2 and "check_synthesis(" in prompt:
            return {"tool": "check_synthesis", "args": {"smiles": SMILES}}
        if ground:
            return {"done": {"items": [{"kind": "supports", "claim": "a paper reports activity", "source": f"PMID {PMID}"},
                                       {"kind": "prior", "claim": "Lipinski pass (a prior)", "source": "predict:admet-physchem"}]}}
        return {"done": f"PMID {PMID} reports activity"}

    def generate(self, inv, prompt, **kw):
        if "TOOLS OFFERED IN THIS STEP" in prompt:
            self.prompts.append(prompt)
            return ToolResult(envelope=inv.envelope.model_copy(), invocation_id=inv.invocation_id, status="succeeded", output={"text": json.dumps(self._loop(prompt))},
                              model_version=self.model_version)
        if "hypothesis lead" in prompt and "RESULTS SO FAR" not in prompt:
            self.prompts.append(prompt)
            plan = F.PLAN([f"C{i}" for i in range(1, 9)])
            for fam in plan["families"]:
                for v in fam["variants"]:
                    v.update({"smiles": SMILES, "tools": ["admet-physchem"]})
            return ToolResult(envelope=inv.envelope.model_copy(), invocation_id=inv.invocation_id, status="succeeded", output={"text": json.dumps(plan)},
                              model_version=self.model_version)
        return super().generate(inv, prompt, **kw)


@pytest.fixture(scope="module")
def everything(tmp_path_factory):
    d = tmp_path_factory.mktemp("all_features")
    store = SqliteProvenanceStore(str(d / "prov.db"))
    pm = Pubmed()
    cfg = CampaignConfig(problem=ProblemStatement(objective="o", constraints=[Constraint(name="potency", description="IC50 <= 20 uM", kind="hard", limit=20.0, unit="uM")]),
                         hypothesis_source="agent", model=AllFeatures(F._answer), autonomy="act", k=8, max_families=8, rounds=2, seed=1, followups=True,
                         inbox=str(d / "inbox"), poll_secs=0.05, flywheel=Flywheel(":memory:"), budget=ResourceBudget(max_total_runs=50, max_hypotheses_total=30),
                         grounding_tools=("pubmed",), grounding_decide=True, ground_hypotheses=True, ground_compute=("predict", "synthesis"),
                         loop_compute=("predict", "synthesis"), prediction_tools=["admet-physchem"], knowledge_services={"pubmed": pm},
                         blinding={"exclude": ["zz-never-matches"], "off_tools": ["opentargets_summary"]}, grounding_names="operator",
                         plan_approver=lambda plan, pid: PlanDecision(approved=True, by="t"))
    rep = run_campaign(cfg, SimulatorLab(seed=3), store, approver=lambda t: True, notify=lambda m: None)
    return d, rep, store.branch_records(f"problem/{rep.problem_id}"), cfg.model, pm


def test_the_campaign_really_used_every_feature(everything):
    """If a feature silently did nothing, the replay below would prove nothing about it."""
    d, rep, recs, model, pm = everything
    sealed = [r.decision for r in recs if (r.decision or {}).get("kind") == "tool_result"]
    scopes = {s.get("scope", "") for s in sealed}
    assert "" in scopes and any(s.startswith("decide-") for s in scopes) and any(s.startswith("ground-initial-") for s in scopes), scopes
    assert {"predict", "check_synthesis"} <= {s["tool"] for s in sealed} and "pubmed_search" in {s["tool"] for s in sealed}
    assert any((r.decision or {}).get("kind") == "grounding" and any(i["kind"] == "prior" for i in r.decision["items"]) for r in recs)
    assert all("opentargets_summary" not in p.split("TOOLS OFFERED IN THIS STEP")[1].split("Reply ONLY")[0] for p in model.prompts if "TOOLS OFFERED IN THIS STEP" in p), \
        "blinding turned the tool off: it was never on a menu"
    env = next(r.decision["environment"] for r in recs if (r.decision or {}).get("environment"))
    assert env["grounding_tools"] == ["pubmed"] and env["grounding_decide"] is True and env["ground_hypotheses"] is True
    assert env["ground_compute"] == ["predict", "synthesis"] and env["loop_compute"] == ["predict", "synthesis"]
    assert env["blinding"]["off_tools"] == ["opentargets_summary"] and env["blinding"]["exclude_n"] == 1
    assert audit_campaign(recs) == []


def test_a_campaign_using_every_feature_replays_from_the_ledger_with_no_model_and_no_network(everything, tmp_path):
    d, rep, recs, model, pm = everything
    before = list(pm.queries)
    out = reproduce(f"problem/{rep.problem_id}", db_path=str(d / "prov.db"), inbox=str(tmp_path / "rin"))
    assert out.get("status") == "match", out
    assert pm.queries == before, "the replay called no adapter: every knowledge result was played back from the ledger"


# ---- what a replay must RESTORE from the recording (cheap: the config-restoring function, called directly) ----

def _restored(**env):
    cfg = CampaignConfig(problem=ProblemStatement(objective="o"))
    out, _ = _agent_cfg(cfg, {"hypothesis_source": "agent", **env}, [], [], [], {})
    return out


@pytest.mark.parametrize("env,attr,expected", [
    ({"grounding_tools": ["pubmed", "chembl"]}, "grounding_tools", ("pubmed", "chembl")),
    ({"grounding_decide": False}, "grounding_decide", False),
    ({"ground_hypotheses": True}, "ground_hypotheses", True),
    ({"ground_compute": ["predict"]}, "ground_compute", ("predict",)),
    ({"loop_compute": ["synthesis"]}, "loop_compute", ("synthesis",)),
    ({"grounding_names": "all"}, "grounding_names", "all"),
    ({"grounding_public_names": ["gefitinib"]}, "grounding_public_names", ("gefitinib",)),
    ({"blinding": {"exclude_n": 3, "cutoff": "2019/12/31", "off_tools": ["opentargets_summary"]}}, "blinding",
     {"exclude": [], "cutoff": "2019/12/31", "off_tools": ["opentargets_summary"]}),
    ({"followups": True}, "followups", True),
    ({"replan": True}, "replan", True),
])
def test_a_replay_restores_each_recorded_setting(env, attr, expected):
    assert getattr(_restored(**env), attr) == expected


def test_a_recording_that_predates_a_setting_replays_with_the_behaviour_it_ran_with():
    """An older envelope has no grounding_names or grounding_decide: it ran with the strict names rule and with decide reading on."""
    cfg = _restored()
    assert cfg.grounding_names == "all" and cfg.grounding_decide is True
    assert cfg.grounding_tools == () and cfg.ground_hypotheses is False and cfg.ground_compute == () and cfg.loop_compute == () and cfg.blinding is None


def test_the_exclusion_list_is_never_restored_because_it_was_never_recorded():
    """The ledger keeps only the list's size and hash; a replay plays sealed results back, so it needs no list."""
    assert _restored(blinding={"exclude_n": 9, "exclude_sha256": "x", "cutoff": None, "off_tools": []}).blinding["exclude"] == []
