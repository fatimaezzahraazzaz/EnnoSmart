from __future__ import annotations

import httpx

from agents.EnnoScholarV2.config import ScholarConfig
from agents.EnnoScholarV2.models import Article
from agents.EnnoScholarV2.tools.scientific_search import ScientificSearch, deduplicate
from agents.EnnoScholarV2.tools.similarity import find_similar_articles


ATOM = b'''<feed xmlns="http://www.w3.org/2005/Atom" xmlns:arxiv="http://arxiv.org/schemas/atom"><entry><id>http://arxiv.org/abs/2401.12345v2</id><title>Neural meshing</title><summary>A trained model constructs meshes.</summary><published>2024-01-01</published><author><name>Alice</name></author><arxiv:doi>10.1234/mesh</arxiv:doi></entry></feed>'''


def test_official_providers_normalization_and_cross_source_dedup():
    def handler(request):
        if "semanticscholar" in request.url.host:
            return httpx.Response(200, json={"data": [{"title": "Neural meshing", "abstract": "A model.", "authors": [{"name": "Alice"}], "year": 2024, "externalIds": {"DOI": "10.1234/mesh", "ArXiv": "2401.12345"}, "citationCount": 9}]})
        if "openalex" in request.url.host:
            return httpx.Response(200, json={"results": [{"title": "Neural meshing", "doi": "https://doi.org/10.1234/MESH", "abstract_inverted_index": {"Neural": [0], "mesh": [1]}, "best_oa_location": {"pdf_url": "https://arxiv.org/pdf/2401.12345"}, "cited_by_count": 11}]})
        return httpx.Response(200, content=ATOM)
    client = httpx.Client(transport=httpx.MockTransport(handler))
    result = ScientificSearch(ScholarConfig(), client).search_scientific("neural mesh", 5)
    assert len(result["articles"]) == 1
    article = result["articles"][0]
    assert article["doi"] == "10.1234/mesh" and article["citation_count"] == 11
    assert set(article["sources"]) == {"semantic_scholar", "openalex", "arxiv"}
    assert article["access_level"] == "abstract_only" and article["analysis_basis"] is None
    assert article["pdf_url"] and article["abstract"]


def test_provider_failure_preserves_paid_metadata_article():
    def handler(request):
        if "openalex" in request.url.host:
            return httpx.Response(200, json={"results": [{"title": "Relevant paid paper", "doi": "10.1234/paywall"}]})
        return httpx.Response(429)
    result = ScientificSearch(ScholarConfig(), httpx.Client(transport=httpx.MockTransport(handler))).search_scientific("mesh", 5)
    assert result["articles"][0]["access_level"] == "metadata_only"
    assert len(result["warnings"]) == 2


def test_dedup_title_and_arxiv_version_and_preserve_distinct_articles():
    items = [Article(title="Méthode: Mesh!", source="one", sources=["one"]), Article(title="Methode mesh", source="two", sources=["two"]),
        Article(title="Mesh algorithm", arxiv_id="2401.12345v1"), Article(title="Alternate title", arxiv_id="2401.12345v3"), Article(title="Unrelated method")]
    assert len(deduplicate(items, 10)) == 3


def test_similar_queries_exclude_seed_and_keep_paywalled_results(monkeypatch):
    search = ScientificSearch(ScholarConfig())
    seed = deduplicate([Article(title="Seed", doi="10.1234/seed")], 1)[0]
    queries = []
    def fake(query, limit):
        queries.append(query)
        return {"articles": [seed.model_dump(), Article(title="Paid related", doi="10.1234/related").model_dump()], "warnings": []}
    monkeypatch.setattr(search, "search_scientific", fake)
    result = find_similar_articles("Seed", search, article=seed, queries=["learned meshes", "mesh limitations"])
    assert queries == ["learned meshes", "mesh limitations"]
    assert len(result["articles"]) == 1 and result["articles"][0]["access_level"] == "metadata_only"


def test_openalex_arxiv_location_preserves_id_and_pdf_candidate():
    article = ScientificSearch(ScholarConfig())._openalex({"title": "MeshingNet", "best_oa_location": {
        "landing_page_url": "http://arxiv.org/abs/2004.07016v2", "is_oa": True}})
    assert article.arxiv_id == "2004.07016v2"
    assert article.url.endswith("2004.07016v2")
