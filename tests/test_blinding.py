"""A blinded study withholds what could hand a model the answer it is being scored against (5H): the held-out compounds, anything after a cutoff date, whole
tools. The withholding happens BEFORE the model sees a result, is counted on the ledger, and the ledger never holds the exclusion list itself."""
import json

from proef.adapters.store.sqlite import SqliteProvenanceStore
from proef.core.interfaces import system_actor
from proef.tools import ToolCatalog, ToolContext, blind, chembl_tool, opentargets_tool, pubmed_tool

HELD = ["EGFR-HELD-1", "gefitinib", "CC(=O)Oc1ccccc1C(=O)O"]


class Pm:
    def __init__(self, rows):
        self.rows, self.calls = rows, []

    def search_and_fetch(self, q, n=5, **kw):
        self.calls.append(kw)
        return list(self.rows)


class Ch:
    def target_search(self, q, limit=5):
        return [{"target_chembl_id": "CHEMBL203", "name": "EGFR"}, {"target_chembl_id": "CHEMBL999", "name": "gefitinib target panel"}]

    def activities(self, t, limit=10):
        return [{"molecule_chembl_id": "CHEMBL939", "molecule_name": "GEFITINIB", "smiles": "COc1cc2ncnc(Nc3ccc(F)c(Cl)c3)c2cc1OCCCN1CCOCC1"},
                {"molecule_chembl_id": "CHEMBL1", "molecule_name": None, "smiles": "CC(=O)Oc1ccccc1C(=O)O"},
                {"molecule_chembl_id": "CHEMBL553", "molecule_name": "ERLOTINIB", "smiles": "COCCOc1cc2ncnc(Nc3cccc(C#C)c3)c2cc1OCCOC"}]


def _env(blinding=None, services=None):
    cat = ToolCatalog()
    for t in (pubmed_tool(), chembl_tool(), opentargets_tool()):
        cat.register(t)
    store = SqliteProvenanceStore(":memory:")
    ctx = ToolContext(state="propose", autonomy="act", store=store, actor=system_actor("t"), branch="t", services=services or {},
                      egress_ok={"pubmed_search", "chembl_search", "opentargets_summary"}, blinding=blinding)
    return cat, ctx, store


def test_without_blinding_nothing_is_withheld():
    cat, ctx, _ = _env(services={"chembl": Ch()})
    assert len(cat.call(ctx, "chembl_search", {"target_chembl_id": "CHEMBL203"})["result"]["data"]) == 3


def test_a_result_that_mentions_a_held_out_compound_by_name_id_or_structure_is_dropped_and_the_rest_kept():
    cat, ctx, store = _env({"exclude": HELD}, {"chembl": Ch()})
    r = cat.call(ctx, "chembl_search", {"target_chembl_id": "CHEMBL203"})
    kept = r["result"]["data"]
    assert [k["molecule_chembl_id"] for k in kept] == ["CHEMBL553"], "gefitinib (by name, any case) and the aspirin structure are gone; erlotinib stays"
    rec = next(x.decision for x in store.branch_records("t") if (x.decision or {}).get("kind") == "tool_call")
    assert rec["blinded_dropped"] == 2, "the ledger counts what was withheld"
    assert "withheld" not in json.dumps(r) and "blind" not in json.dumps(r).lower(), "the model is not told something was withheld: that would itself leak"
    assert [k["name"] for k in cat.call(ctx, "chembl_search", {"query": "EGFR"})["result"]["data"]] == ["EGFR"], "target search is filtered too"


def test_pubmed_abstracts_that_name_a_held_out_compound_are_dropped_and_a_cutoff_date_is_passed_to_the_search():
    rows = [{"pmid": "1", "title": "Gefitinib in NSCLC", "abstract": "x"}, {"pmid": "2", "title": "Kinase review", "abstract": "mentions EGFR-HELD-1 potency"},
            {"pmid": "3", "title": "ATP-site binding", "abstract": "a clean abstract"}]
    pm = Pm(rows)
    cat, ctx, _ = _env({"exclude": HELD, "cutoff": "2019/12/31"}, {"pubmed": pm})
    out = cat.call(ctx, "pubmed_search", {"query": "EGFR"})["result"]["data"]
    assert [x["pmid"] for x in out] == ["3"] and pm.calls == [{"maxdate": "2019/12/31"}]
    pm2 = Pm(rows)
    cat2, ctx2, _ = _env(None, {"pubmed": pm2})
    assert len(cat2.call(ctx2, "pubmed_search", {"query": "EGFR"})["result"]["data"]) == 3 and pm2.calls == [{}], "no blinding, no cutoff passed"


def test_a_tool_a_blinded_study_turns_off_is_not_on_the_menu_and_cannot_be_called():
    cat, ctx, _ = _env({"off_tools": ["opentargets_summary"]}, {})
    assert "opentargets_summary" not in cat.menu_text(ctx) and "pubmed_search" in cat.menu_text(ctx)
    assert cat.call(ctx, "opentargets_summary", {"symbol": "EGFR"})["code"] == "not_offered"


def test_short_exclusion_terms_are_ignored_so_a_two_letter_entry_cannot_blank_every_result():
    class C:
        pass
    c = ToolContext(blinding={"exclude": ["ab", "x"]})
    assert blind([{"a": "about"}], c) == [{"a": "about"}] and c.dropped == 0


def test_a_blinded_campaign_records_the_size_and_hash_of_the_exclusion_list_but_not_the_list(tmp_path):
    from tests.test_grounding_decide import _grounded
    report, store, recs, model, pm = _grounded(tmp_path, blinding={"exclude": ["Gefitinib", "EGFR-HELD-1"], "cutoff": "2019/12/31", "off_tools": ["chembl_search"]})
    env = next(r.decision["environment"] for r in recs if (r.decision or {}).get("environment"))
    b = env["blinding"]
    assert b["exclude_n"] == 2 and len(b["exclude_sha256"]) == 16 and b["cutoff"] == "2019/12/31" and b["off_tools"] == ["chembl_search"]
    blob = json.dumps([r.decision for r in recs], default=str).lower()
    assert "gefitinib" not in blob and "egfr-held-1" not in blob, "the held-out list is not in the record"


def test_a_blinded_campaign_replays_with_the_same_menu_and_no_network(tmp_path):
    from proef.reproduce import reproduce
    from tests.test_grounding_decide import _grounded
    (tmp_path / "a").mkdir()
    report, store, recs, model, pm = _grounded(tmp_path / "a", blinding={"exclude": ["zz-never-matches"], "off_tools": ["opentargets_summary"]})
    before = list(pm.queries)
    out = reproduce(f"problem/{report.problem_id}", db_path=str(tmp_path / "a" / "prov.db"), inbox=str(tmp_path / "rin"))
    assert out.get("status") == "match", out
    assert pm.queries == before
