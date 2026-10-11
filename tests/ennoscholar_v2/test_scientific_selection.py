from __future__ import annotations

import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agents.EnnoScholarV2.agent import ScholarChatAgent, StoredArticle
from agents.EnnoScholarV2.config import ScholarConfig
from agents.EnnoScholarV2.models import Article, FulltextResult
from agents.EnnoScholarV2.tools.scientific_selection import (
    candidate_notice, diverse_shortlist, ordered_articles, rerank_candidates, work_groups,
)
from scientific_fixture import CENTRAL_TITLES, INTENT, QUERIES, fixture_articles
from conftest import call, message, response
from test_context_window import active_notices


def ranking_client(grades, *, invalid=False):
    client = Mock()
    client.with_options.return_value = client
    calls = []
    def create(**kwargs):
        calls.append(kwargs)
        candidates = json.loads(kwargs["input"])["candidates"]
        rows = [{"article_id": a["article_id"], "relevance_label": grades.get(a["title"], ("CONNEXE", 0))[0],
            "relevance_score": grades.get(a["title"], ("CONNEXE", 0))[1], "reason": "Jugement scientifique de fixture, fondé sur titre et abstract."} for a in candidates]
        if invalid:
            rows[0]["article_id"] = "invented_id"
        return SimpleNamespace(output_text=json.dumps({"articles": rows}))
    client.responses.create.side_effect = create
    return client, calls


def test_mixed_benchmark_is_not_100_percent_connexe_and_noise_cannot_win():
    articles = fixture_articles()
    for article in articles:
        article.matched_queries = QUERIES
        article.retrieval_score = 10000 if article.title not in CENTRAL_TITLES else 0
    articles.append(Article(id="editorial", title="Editorial message from the editor-in-chief",
        abstract="An editorial mentioning deep learning and mesh generation.", source="openalex", sources=["openalex"],
        matched_queries=QUERIES, retrieval_score=100000, citation_count=1000000, access_level="fulltext"))
    registry = {article.id: StoredArticle(article) for article in articles}
    grades = {title: ("FONDAMENTAL" if "survey" in title else "DIRECT", 100 - i) for i, title in enumerate(CENTRAL_TITLES)}
    client, calls = ranking_client(grades)
    selected, stats = rerank_candidates(registry, client, ScholarConfig(model="mock"), "Scientific reference question", INTENT)
    assert set(CENTRAL_TITLES) <= {article.title for article in selected}
    labels = {r.article.relevance_label for r in registry.values() if r.article.relevance_origin == "llm"}
    assert labels == {"DIRECT", "FONDAMENTAL", "CONNEXE"} and not stats["all_connexe"]
    assert "editorial" not in [article.id for article in selected[:8]]
    assert all("unrelated" not in article.title for article in selected[:8])
    assert len(selected) <= 12 and len(registry) == 651
    assert all(request["tools"] == [] and request["tool_choice"] == "none" for request in calls)
    assert all(json.loads(request["input"])["candidates"][0]["abstract_excerpt"] for request in calls)


def test_specialized_query_recovers_candidate_below_global_rank_926():
    noise = [Article(id=f"noise-{i}", title=f"General simulation experiment {i}", abstract="A simulation using an existing object.",
        source="openalex", sources=["openalex"], matched_queries=["general simulation"], retrieval_score=1000) for i in range(1000)]
    target = Article(id="specialized", title="Neural design of proteins", abstract="A model learns to create new proteins.",
        source="arxiv", sources=["arxiv"], matched_queries=["neural protein design"], retrieval_score=.1)
    shortlist = diverse_shortlist([*noise, target], None, limit=32)
    assert "specialized" in [a.id for a in shortlist]
    registry = {a.id: StoredArticle(a) for a in [*noise, target]}
    client, _ = ranking_client({target.title: ("DIRECT", 95)})
    selected, _ = rerank_candidates(registry, client, ScholarConfig(model="mock"), "Design new proteins with trained models")
    assert selected[0].id == target.id


def test_publication_and_preprint_one_work_without_deleting_originals():
    title = "DL-Polycube: Deep learning enhanced generalized polycube method for high-quality hexahedral mesh generation and volumetric spline construction"
    pub = Article(id="publication", title=title, doi="10.1007/example", year=2025, authors=["Researcher A"],
        source="openalex", sources=["openalex"], url="https://publisher.example/paper", matched_queries=QUERIES, access_level="metadata_only")
    pre = Article(id="preprint", title=title.replace("generalized ", ""), doi="10.48550/arxiv.2410.18852", arxiv_id="2410.18852", year=2024,
        authors=["Researcher A"], source="arxiv", sources=["arxiv"], url="https://arxiv.org/abs/2410.18852", pdf_url="https://arxiv.org/pdf/2410.18852",
        abstract="A learned mesh generation method.", matched_queries=QUERIES, access_level="fulltext")
    text = FulltextResult(access_level="fulltext", text="Verified content stays backend.")
    registry = {pub.id: StoredArticle(pub), pre.id: StoredArticle(pre, fulltext=text)}
    views = work_groups([pub, pre])
    assert len(views) == 1 and views[0].id == "publication"
    assert set(views[0].sources) == {"openalex", "arxiv"} and len(views[0].work_urls) >= 4
    client, _ = ranking_client({title: ("DIRECT", 95)})
    selected, _ = rerank_candidates(registry, client, ScholarConfig(model="mock"), "Find learned generation methods")
    assert [a.id for a in selected] == ["publication"] and len(registry) == 2
    assert registry["preprint"].fulltext is text
    assert "Verified content stays backend." not in json.dumps(candidate_notice(views[0]))
    assert set(registry["publication"].article.related_article_ids) == {"publication", "preprint"}


def test_different_numeric_titles_are_not_merged():
    articles = [Article(id=str(i), title=f"Neural model for prediction of protein domain {i} in biological simulation", year=2025) for i in range(2)]
    assert len(work_groups(articles)) == 2


def test_similar_titles_with_disjoint_authors_are_not_merged():
    a = Article(id="a", title="A neural method for protein design and optimization of biological structures", authors=["Researcher A"], year=2025)
    b = Article(id="b", title="A neural method for protein design and optimization of biological structure", authors=["Researcher B"], year=2025)
    assert len(work_groups([a, b])) == 2


def test_access_cannot_promote_related_work_above_direct_paid_article():
    direct = Article(id="paid", title="Neural design of proteins", relevance_origin="llm", relevance_label="DIRECT", relevance_score=50,
        access_level="metadata_only", citation_count=0)
    related = Article(id="free", title="Neural simulation of proteins", relevance_origin="llm", relevance_label="CONNEXE", relevance_score=100,
        access_level="fulltext", citation_count=1000000)
    assert ordered_articles([related, direct])[0].id == "paid"
    direct.access_level, related.access_level = "fulltext", "metadata_only"
    assert ordered_articles([related, direct])[0].id == "paid"


def test_new_retrieval_does_not_overwrite_scientific_qualification():
    article = Article(id="stored", title="Neural design of proteins", doi="10.1234/design", relevance_origin="llm",
        relevance_for="request", relevance_label="DIRECT", relevance_tag="DIRECT", relevance_score=95, ranking_reason="Direct task.")
    registry = {article.id: StoredArticle(article)}
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=Mock())
    agent.register([Article(title=article.title, doi=article.doi, relevance_score=.1, relevance_tag="CONNEXE", source="arxiv").model_dump()], registry)
    assert registry["stored"].article.relevance_score == 95 and registry["stored"].article.relevance_label == "DIRECT"


def test_invalid_llm_ids_are_not_committed_and_failure_is_explicit():
    article = Article(id="valid", title="Neural design of proteins", matched_queries=["protein design"], sources=["arxiv"])
    registry = {article.id: StoredArticle(article)}
    client, _ = ranking_client({}, invalid=True)
    _, stats = rerank_candidates(registry, client, ScholarConfig(model="mock"), "Design proteins")
    assert stats["errors"] == ["ValueError"] and not stats["scientific_selection"]
    assert registry["valid"].article.relevance_origin != "llm"


def test_expired_ranking_deadline_makes_no_api_call():
    article = Article(id="a", title="Neural design of proteins", matched_queries=["protein design"], sources=["arxiv"])
    client, calls = ranking_client({})
    _, stats = rerank_candidates({"a": StoredArticle(article)}, client, ScholarConfig(model="mock"), "Design proteins", deadline=time.monotonic() - 1)
    assert not calls and stats["errors"] == ["TimeoutError"]


def test_all_connexe_is_reported_without_inventing_direct_labels():
    article = Article(id="a", title="An unrelated editorial", matched_queries=["science"], sources=["arxiv"])
    client, _ = ranking_client({})
    _, stats = rerank_candidates({"a": StoredArticle(article)}, client, ScholarConfig(model="mock"), "A different task")
    assert stats["all_connexe"]


def test_mass_ranking_tool_is_absent_from_normal_catalogue():
    from agents.EnnoScholarV2.agent import TOOLS
    assert "rank_scientific_articles" not in [tool.get("name") for tool in TOOLS]


def test_normal_search_never_calls_legacy_reranker(monkeypatch, fake_client):
    import agents.EnnoScholarV2.tools.scientific_selection as legacy
    monkeypatch.setattr(legacy, "rerank_candidates", lambda *a, **kw: pytest.fail("No mass ranking"))
    monkeypatch.setattr(legacy, "diverse_shortlist", lambda *a, **kw: pytest.fail("No mass shortlist"))
    article = Article(title="Native candidate", doi="10.1234/example", sources=["arxiv"])
    agent = ScholarChatAgent(ScholarConfig(model="mock"), client=fake_client)
    monkeypatch.setattr(agent.search, "search_batch", lambda *a, **kw: {"all_articles": [article.model_dump()]})
    fake_client.responses.create.side_effect = [response(call("search_scientific", {"queries": ["science"], "top_k": 8, "intent": INTENT})), response(message("Selected by conversation."))]
    result = agent.run("Find papers", [], {})
    assert result.tool_counts == {"search_scientific": 1} and fake_client.responses.create.call_count == 2
