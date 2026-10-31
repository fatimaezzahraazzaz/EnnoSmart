"""Preparation of the next live test: a new conversation asking for ten articles.

Simulated providers only. Checks continuity and honesty, not Sol's selection quality.
"""
import json
from unittest.mock import Mock
from agents.EnnoScholarV4.config import ScholarV4Config
from agents.EnnoScholarV5.models import Article
from experiments.ennoscholar_v5.progress import Scope, project, model_work_view
from .test_graph import case
from .test_live_integration import app_env, call, finish, run_sync, scientific_state
from .test_stokes_control import control

REQUEST = ("Trouve dix publications scientifiques pertinentes sur la réduction électrochimique du CO2 par des catalyseurs "
    "au cuivre, analyse les plus importantes et compare leurs méthodes et limites.")


def test_ten_article_request_keeps_candidates_reuses_search_and_declares_missing(app_env):
    make, factory, _ = app_env
    sources = Mock()
    found = [Article(title=f"Copper catalyst for CO2 electroreduction study {i}", doi=f"10.1234/cu.{i}",
        authors=["A Author"], year=2020+i % 4, abstract="Copper electrocatalysts reduce CO2 to multicarbon products.")
        for i in range(6)]
    sources.search.return_value = (found, [])
    sources.last_provider_stats = {"openalex": {"returned": 6}}
    query = {"query": "copper CO2 electroreduction catalyst", "sources": ["openalex"], "limit_per_source": 10,
        "offset": 0, "family": "copper electrocatalysis"}
    svc, sid, tr = make([call("search_scientific", query, "s1"), call("search_scientific", query, "s2"),
        finish("Six candidats trouvés, non encore évalués ; quatre publications manquent pour atteindre dix.")],
        config=ScholarV4Config(api_key="offline", web_search_enabled=False), sources=sources)
    result = run_sync(svc, sid, REQUEST)
    # A repeated identical search is answered from its receipt, not re-sent.
    assert sources.search.call_count == 1, result
    assert result["usage"]["llm_calls"] == len(tr.requests) == 3
    data = scientific_state(factory, sid)
    assert data["research_progress"]["requested_count"] == 10
    assert len(data["articles"]) == 6
    # Unexamined candidates and the missing count stay as pending work.
    kinds = [t["kind"] for t in result["remaining_tasks"].values()]
    assert kinds.count("evaluate_candidate") == 6
    selection = next(t for t in result["remaining_tasks"].values() if t["kind"] == "complete_selection")
    assert selection["missing"] == 10
    assert result["partial"] and not result.get("coverage_sufficient")
    # Sol saw the factual counts, without a quota push or per-article section maps.
    view = control(tr.requests[-1])["v5_work_state"]
    assert view["counts"]["target"] == 10 and view["counts"]["missing"] == 10
    assert not any("reading_map" in a for a in view["articles"])
    assert result["budget_limits"]["tokens"] == svc.config.max_tokens


def test_real_multi_article_state_distinguishes_abstract_partial_and_fulltext():
    c = case("chemistry")
    p = project(c["state"], scope=Scope(1, "c"))
    states = {r["document"] for r in p["articles"].values()}
    assert {"not_recovered", "extracted"} <= states
    assert p["counts"]["target"] == 10 and p["counts"]["missing"] == 5
    view = model_work_view(p)
    assert len(json.dumps(view, ensure_ascii=False)) <= 10000
    assert {r['article_id'] for r in view['articles'] if r['in_request_scope']} == {
        aid for aid,a in p['articles'].items() if a['in_request_scope']}
    # Compact list rows keep the factual unexamined-page count for read articles.
    read = [r for r in view["articles"] if r["in_request_scope"] and r["document"] != "not_recovered"]
    assert all("unexamined_pages" in r for r in read)
