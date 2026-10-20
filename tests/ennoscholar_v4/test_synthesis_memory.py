"""Final synthesis keeps every work's methods, results and limits; storage is never compressed."""
import copy
import inspect
import json

from agents.EnnoScholarV4 import context as context_module
from agents.EnnoScholarV4.context import model_context
from agents.EnnoScholarV4.prompts import SYSTEM
from tests.ennoscholar_v4.test_multi_article_reading import corpus, read_windows


def six_analyzed():
    state, toolkit = corpus(6)
    read_windows(state, toolkit, 60)
    state["messages"] = [{"role": "user", "content": "Synthèse des méthodes, résultats et limites."}]
    return state, toolkit


def memory(state, toolkit, budget=24000):
    return json.loads(model_context(state, toolkit, budget, final=True)[0]["content"].split(" : ", 1)[1])


def test_final_context_keeps_method_result_and_limit_for_each_of_six_works():
    state, toolkit = six_analyzed()
    papers = memory(state, toolkit)["conversation_science"]["papers"]
    assert {p["id"] for p in papers} == set(toolkit.registry)
    for paper in papers:
        assert {"method", "results", "limitations"} <= set(paper["analysis"]), paper["id"]
        cited = {eid for claims in paper["analysis"].values() if isinstance(claims, list)
            for claim in claims for eid in claim["evidence_ids"]}
        assert cited <= {proof["evidence_id"] for proof in paper["proofs"]}


def test_context_compression_never_alters_stored_scientific_cards():
    state, toolkit = six_analyzed()
    before = copy.deepcopy({aid: a.analysis for aid, a in toolkit.registry.items()})
    for budget in (6000, 12000, 24000):
        memory(state, toolkit, budget)
    after = {aid: a.analysis for aid, a in toolkit.registry.items()}
    assert after == before
    for card in after.values():
        assert len(card["method"]) >= 3 and len(card["results"]) >= 2 and len(card["limitations"]) >= 2
        assert card["experimental_setup"] and card["evidence"]


def test_no_domain_specific_terms_bias_the_generic_agent():
    source = inspect.getsource(context_module)
    assert "AUROC" not in source
    for name in ("ATHENATEST", "A3Test", "SAR2SAR", "Speckle2Void", "MERLIN"):
        assert name not in SYSTEM


def test_writing_reaches_the_complete_saved_card_without_new_search():
    import time
    from agents.EnnoScholarV4.writing_context import writing_context
    state, toolkit = six_analyzed()
    aid = "paper_long_0"
    section = {"id": "section_1", "title": "Méthodes", "article_ids": [aid], "drafts": []}
    packet = writing_context(state, toolkit, section, budget=14000)
    card = toolkit.registry[aid].analysis
    assert packet["sources"][0]["claims_available"]["method"] == len(card["method"])
    full = toolkit.execute("get_article", {"identifiers": [aid]}, time.monotonic()+30)["articles"][0]
    assert len(full["analysis"]["method"]) == len(card["method"])
    assert len(full["analysis"]["results"]) == len(card["results"])
    assert not toolkit.sources.search.called and not toolkit.sources.metadata.called
