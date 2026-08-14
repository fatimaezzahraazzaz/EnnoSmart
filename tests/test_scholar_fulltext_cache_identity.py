from types import SimpleNamespace

from services.scholar_fulltext_cache_service import (
    article_cache_key,
    get_cached_fulltexts,
    normalize_doi,
)


def test_doi_variants_share_one_global_cache_key():
    a = SimpleNamespace(
        doi="https://doi.org/10.1234/ABC.9",
        title="Titre A",
        year=2024,
    )
    b = SimpleNamespace(
        doi="DOI: 10.1234/abc.9",
        title="Titre modifié",
        year=2025,
    )

    assert normalize_doi(a.doi) == "10.1234/abc.9"
    assert article_cache_key(a) == article_cache_key(b) == "doi:10.1234/abc.9"


def test_title_year_fallback_is_stable_without_doi():
    a = SimpleNamespace(doi=None, title="Radar — bistatique", year=2023)
    b = SimpleNamespace(doi="", title="Radar bistatique", year=2023)

    assert article_cache_key(a) == article_cache_key(b)


def test_batch_lookup_reuses_one_query_for_multiple_articles(monkeypatch):
    articles = [
        SimpleNamespace(id=1, doi="10.1234/a", title="A", year=2024),
        SimpleNamespace(id=2, doi="10.1234/b", title="B", year=2025),
    ]
    cache_row = SimpleNamespace(
        id=9,
        cache_key="doi:10.1234/a",
        text_chars=1200,
        payload_json={
            "ok": True,
            "full_text_status": "text_extracted",
            "full_text": "contenu",
        },
    )

    class Query:
        def filter(self, *args, **kwargs):
            return self

        def all(self):
            return [cache_row]

    class DB:
        def __init__(self):
            self.query_count = 0

        def query(self, *args, **kwargs):
            self.query_count += 1
            return Query()

    monkeypatch.setattr(
        "services.scholar_fulltext_cache_service._ensure_table",
        lambda: None,
    )
    db = DB()

    hits = get_cached_fulltexts(db, articles)

    assert db.query_count == 1
    assert set(hits) == {1}
    assert hits[1]["fulltext_cache_id"] == 9
    assert hits[1]["fulltext_cache_hit"] is True
