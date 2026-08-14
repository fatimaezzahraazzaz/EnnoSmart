from types import SimpleNamespace

from services.scholar_evidence_preflight_service import _classify
from services import scholar_evidence_preflight_service as service


def article_with_abstract(value: str = ""):
    return SimpleNamespace(source_json={"abstract": value})


def test_paywall_reason_is_explicit_for_consultant():
    result = {
        "ok": False,
        "status": "paywall_blocked",
        "full_text_status": "missing_or_blocked_fulltext",
        "needs_legal_recovery": True,
    }

    evidence = _classify(article_with_abstract("Résumé scientifique"), result)

    assert evidence["evidence_status"] == "ABSTRACT_READY"
    assert evidence["reason_code"] == "PAYWALL_BLOCKED"
    assert evidence["access_kind"] == "paid"
    assert "payant" in evidence["evidence_label"].lower()


def test_identity_mismatch_is_not_reported_as_paywall():
    result = {
        "ok": False,
        "status": "pdf_identity_mismatch",
        "full_text_status": "missing_or_blocked_fulltext",
        "needs_legal_recovery": True,
    }

    evidence = _classify(article_with_abstract(), result)

    assert evidence["evidence_status"] == "METADATA_ONLY"
    assert evidence["reason_code"] == "DOCUMENT_IDENTITY_MISMATCH"
    assert evidence["access_kind"] == "identity_mismatch"


def test_db_worker_count_keeps_connections_for_coordinator_and_errors(monkeypatch):
    from db import database

    pool = SimpleNamespace(size=lambda: 5, _max_overflow=10)
    monkeypatch.setattr(database, "engine", SimpleNamespace(pool=pool))

    assert service._safe_db_worker_count(16) == 13
    assert service._safe_db_worker_count(8) == 8


def test_default_preflight_scans_every_presented_article(monkeypatch):
    articles = [
        SimpleNamespace(
            id=index,
            verrou_id=1 + (index % 2),
            score=100 - index,
            title=f"Article {index}",
            source_json={},
        )
        for index in range(1, 41)
    ]

    class Query:
        def filter(self, *args, **kwargs):
            return self

        def order_by(self, *args, **kwargs):
            return self

        def all(self):
            return articles

    class DB:
        def query(self, *args, **kwargs):
            return Query()

        def add(self, *args, **kwargs):
            return None

        def commit(self):
            return None

        def rollback(self):
            return None

        def expire_all(self):
            return None

    monkeypatch.setattr(
        service,
        "_process_cache_stage_batch",
        lambda db, rows: [
            {
                "article_id": article.id,
                "verrou_id": article.verrou_id,
                "evidence_status": "EXTRACTION_QUEUED",
                "terminal": False,
            }
            for article in rows
        ],
    )

    monkeypatch.setattr(
        service,
        "_process_direct_stage",
        lambda project_id, article_id: {
            "article_id": article_id,
            "verrou_id": 1 + (article_id % 2),
            "evidence_status": "FULLTEXT_READY",
            "terminal": True,
        },
    )
    from services import scholar_deterministic_oa_service as oa_service
    from services import scholar_fulltext_cache_service as cache_service

    monkeypatch.setattr(cache_service, "ensure_fulltext_cache_ready", lambda: None)

    monkeypatch.setattr(
        oa_service,
        "enrich_articles_with_deterministic_oa",
        lambda db, rows: {
            "stage": "OA_DISCOVERY",
            "input_count": len(list(rows)),
            "articles_with_candidates": 30,
            "resolved_doi_count": 30,
            "elapsed_seconds": 0.01,
        },
    )

    report = service.preflight_scholar_run(
        DB(),
        SimpleNamespace(id=1),
        SimpleNamespace(id=99),
    )

    assert report["mode"] == "staged_fulltext_preflight_v6"
    assert report["exhaustive"] is True
    assert report["processed"] == 40
    assert report["available_candidates"] == 40
    assert report["pipeline_stages"] == [
        "CACHE",
        "DIRECT_KNOWN",
        "OA_DISCOVERY",
        "DIRECT_OA",
        "MCP_TARGETED",
        "MCP_LARGE",
        "FINALIZE_FAST",
    ]
    assert report["batches"][0]["input_count"] == 40
    assert report["batches"][1]["input_count"] == 40
    assert report["batches"][2]["input_count"] == 0
    assert report["batches"][3]["input_count"] == 0
