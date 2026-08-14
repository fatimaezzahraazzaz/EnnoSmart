from types import SimpleNamespace

import pytest

from services import scholar_deterministic_oa_service as service


def oa_candidate(url: str, provider: str):
    return {
        "url": url,
        "kind": "pdf",
        "source": f"{provider}_deterministic",
        "provider": provider,
        "legal_access": True,
        "retrieval_stage": "deterministic_oa",
    }


def test_openalex_keeps_only_open_access_locations():
    candidates = service._candidates_from_openalex(
        {
            "best_oa_location": {
                "is_oa": True,
                "pdf_url": "https://repository.example/article.pdf",
                "landing_page_url": "https://repository.example/article",
                "license": "cc-by",
            },
            "locations": [
                {
                    "is_oa": False,
                    "pdf_url": "https://publisher.example/paid.pdf",
                }
            ],
        }
    )

    assert [item["url"] for item in candidates] == [
        "https://repository.example/article.pdf",
        "https://repository.example/article",
    ]
    assert all(item["legal_access"] is True for item in candidates)


def test_openalex_batches_at_most_one_hundred_dois(monkeypatch):
    calls = []
    stored = {}
    monkeypatch.setattr(service._CACHE, "get", lambda provider, doi: None)
    monkeypatch.setattr(
        service._CACHE,
        "set",
        lambda provider, doi, payload: stored.setdefault((provider, doi), payload),
    )

    def fake_get_json(url, *, params=None, headers=None):
        del url, headers
        chunk = params["filter"].removeprefix("doi:").split("|")
        calls.append(chunk)
        return {
            "results": [
                {
                    "doi": f"https://doi.org/{doi}",
                    "best_oa_location": {
                        "is_oa": True,
                        "pdf_url": f"https://oa.example/{index}.pdf",
                    },
                }
                for index, doi in enumerate(chunk)
            ]
        }

    monkeypatch.setattr(service, "_get_json", fake_get_json)
    dois = [f"10.1234/item-{index}" for index in range(205)]

    resolved, stats = service._openalex_batch(dois)

    assert [len(chunk) for chunk in calls] == [100, 100, 5]
    assert len(resolved) == 205
    assert stats["request_count"] == 3
    assert len(stored) == 205


def test_fallback_providers_receive_only_still_unresolved_dois(monkeypatch):
    articles = [
        SimpleNamespace(
            id=index,
            doi=f"10.5555/{index}",
            source_json={},
        )
        for index in range(1, 4)
    ]

    class DB:
        committed = False

        def add(self, article):
            return article

        def commit(self):
            self.committed = True

    calls = {"unpaywall": [], "crossref": [], "core": []}
    monkeypatch.setattr(
        service,
        "_openalex_batch",
        lambda dois: (
            {"10.5555/1": [oa_candidate("https://oa.example/1.pdf", "openalex")]},
            {
                "provider": "openalex",
                "input_count": len(dois),
                "resolved_count": 1,
                "cache_hits": 0,
                "request_count": 1,
                "errors": [],
            },
        ),
    )

    def unpaywall(doi):
        calls["unpaywall"].append(doi)
        if doi == "10.5555/2":
            return [oa_candidate("https://oa.example/2.pdf", "unpaywall")], False
        return [], False

    def crossref(doi):
        calls["crossref"].append(doi)
        if doi == "10.5555/3":
            return [oa_candidate("https://oa.example/3.pdf", "crossref")], False
        return [], False

    def core(doi):
        calls["core"].append(doi)
        return [], False

    monkeypatch.setattr(service, "_resolve_unpaywall", unpaywall)
    monkeypatch.setattr(service, "_resolve_crossref", crossref)
    monkeypatch.setattr(service, "_resolve_core", core)

    db = DB()
    summary = service.enrich_articles_with_deterministic_oa(db, articles)

    assert set(calls["unpaywall"]) == {"10.5555/2", "10.5555/3"}
    assert calls["crossref"] == ["10.5555/3"]
    assert calls["core"] == []
    assert summary["resolved_doi_count"] == 3
    assert summary["unresolved_doi_count"] == 0
    assert db.committed is True
    assert all(article.source_json["deterministic_oa_candidates"] for article in articles)


def test_direct_fetcher_prioritizes_deterministic_candidates():
    pytest.importorskip("bs4")
    from services.scholar_fulltext_fetcher import build_candidate_urls_for_article

    article = SimpleNamespace(
        url=None,
        doi="10.1234/example",
        source_json={
            "deterministic_oa_candidates": [
                {
                    **oa_candidate("https://oa.example/fulltext.pdf", "openalex"),
                    "license": "cc-by",
                    "discovered_via": "openalex_batch_doi",
                }
            ]
        },
    )

    candidates = build_candidate_urls_for_article(article)

    assert candidates[0]["url"] == "https://oa.example/fulltext.pdf"
    assert candidates[0]["provider"] == "openalex"
    assert candidates[0]["retrieval_stage"] == "deterministic_oa"


def test_crossref_rejects_unlicensed_publisher_tdm_link():
    candidates = service._candidates_from_crossref(
        {
            "URL": "https://publisher.example/article",
            "link": [
                {
                    "URL": "https://publisher.example/article.pdf",
                    "content-type": "application/pdf",
                    "intended-application": "text-mining",
                }
            ],
        }
    )

    assert candidates == []
