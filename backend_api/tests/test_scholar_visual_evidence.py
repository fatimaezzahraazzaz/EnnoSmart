from __future__ import annotations

import hashlib
import io
from pathlib import Path
from types import SimpleNamespace

import fitz
from PIL import Image, ImageDraw

from services.scholar_visual_evidence_service import (
    extract_article_visual_evidence,
    resolve_visual_asset,
)


def _chart_png() -> bytes:
    image = Image.new("RGB", (900, 430), "white")
    draw = ImageDraw.Draw(image)
    draw.line((70, 350, 820, 350), fill="black", width=3)
    draw.line((70, 350, 70, 50), fill="black", width=3)
    draw.line((70, 320, 260, 230, 450, 155, 640, 105, 820, 80), fill="blue", width=8)
    draw.line((70, 80, 260, 145, 450, 220, 640, 285, 820, 325), fill="red", width=8)
    draw.text((90, 30), "Accuracy / loss", fill="black")
    output = io.BytesIO()
    image.save(output, format="PNG")
    return output.getvalue()


def _article_pdf() -> bytes:
    document = fitz.open()
    page = document.new_page(width=595, height=842)
    page.insert_textbox(
        fitz.Rect(55, 50, 540, 135),
        "Experimental results compare predictive performance under operating-regime shift.",
        fontsize=11,
    )
    page.insert_image(fitz.Rect(65, 175, 530, 430), stream=_chart_png())
    page.insert_textbox(
        fitz.Rect(65, 445, 530, 505),
        "Figure 3: Accuracy and loss curves under operating-regime shift for the evaluated models.",
        fontsize=10,
    )
    payload = document.tobytes()
    document.close()
    return payload


def test_uploaded_article_figure_is_extracted_with_caption_and_provenance(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setenv("ENNOSMART_STORAGE_ROOT", str(tmp_path / "storage"))
    pdf_path = tmp_path / "article.pdf"
    pdf_path.write_bytes(_article_pdf())
    project = SimpleNamespace(
        id=3,
        organisme="Scalian",
        project_name="THERMO-PREDICT",
        year="2026",
    )
    article = SimpleNamespace(
        id=14010,
        title="Reliability under operating-regime shift",
        url="https://example.test/article",
        doi="10.1000/example",
        source_json={
            "uploaded_pdf_path": str(pdf_path),
            "target_verrous": ["SV-THERMO"],
        },
    )

    items = extract_article_visual_evidence(
        project=project,
        article=article,
        citation_label="A4",
        fulltext_info={"source_kind": "uploaded", "path": ""},
    )

    assert items
    item = items[0]
    assert item["citation_label"] == "A4"
    assert item["page"] == 1
    assert "Accuracy and loss" in item["caption"]
    assert item["target_verrous"] == ["SV-THERMO"]
    assert item["provenance"]["original_figure_preserved"] is True
    assert resolve_visual_asset(project, item["visual_id"]).is_file()


def test_database_cached_verified_pdf_is_materialized_for_visual_extraction(
    tmp_path,
    monkeypatch,
):
    """Le cache db:// doit conserver l'accès au PDF ayant produit le texte."""

    from services import http_client, scholar_visual_evidence_service

    pdf_bytes = _article_pdf()
    pdf_url = "https://example.test/cached-article.pdf"

    def fake_fetch_bytes(*, url, headers, max_bytes):
        assert url == pdf_url
        assert headers["Accept"].startswith("application/pdf")
        assert max_bytes >= len(pdf_bytes)
        return True, {"final_url": pdf_url}, pdf_bytes

    monkeypatch.setenv("ENNOSMART_STORAGE_ROOT", str(tmp_path / "storage"))
    monkeypatch.setattr(scholar_visual_evidence_service, "REMOTE_FETCH_ENABLED", True)
    monkeypatch.setattr(http_client.GLOBAL_FETCHER, "fetch_bytes", fake_fetch_bytes)

    project = SimpleNamespace(
        id=4,
        organisme="Scalian",
        project_name="CACHE-VISUAL",
        year="2026",
    )
    article = SimpleNamespace(
        id=14011,
        title="Reliability under operating-regime shift",
        url=pdf_url,
        doi="10.1000/cache-visual",
        source_json={"target_verrous": ["SV-CACHE"]},
    )
    items = extract_article_visual_evidence(
        project=project,
        article=article,
        citation_label="A5",
        fulltext_info={
            "path": "db://scholar_fulltext_cache/42",
            "source_kind": "direct",
            "visual_source": {
                "ok": True,
                "status": "pdf_fulltext_extracted",
                "full_text_status": "text_extracted",
                "content_source_kind": "pdf",
                "fulltext_final_url": pdf_url,
                "remote_sha256": hashlib.sha256(pdf_bytes).hexdigest(),
                "identity_verification": {
                    "verified": True,
                    "same_article": True,
                },
            },
        },
    )

    assert items
    assert items[0]["citation_label"] == "A5"
    materialized = Path(article.source_json["legal_pdf_path"])
    assert materialized.is_file()
    assert materialized.read_bytes() == pdf_bytes


def test_fulltext_cache_adapter_exposes_verified_visual_source(monkeypatch):
    from db import database
    from services import article_card_builder, scholar_fulltext_cache_service

    class DummySession:
        def close(self):
            return None

    cached = {
        "ok": True,
        "status": "pdf_fulltext_extracted",
        "full_text_status": "text_extracted",
        "full_text": "Scientific full text. " * 300,
        "content_source_kind": "pdf",
        "fulltext_cache_id": 77,
        "fulltext_final_url": "https://example.test/verified.pdf",
        "remote_sha256": "a" * 64,
        "identity_verification": {
            "verified": True,
            "same_article": True,
        },
    }
    monkeypatch.setattr(database, "SessionLocal", lambda: DummySession())
    monkeypatch.setattr(
        scholar_fulltext_cache_service,
        "get_cached_fulltext",
        lambda db, article: dict(cached),
    )

    info = article_card_builder._load_fulltext(
        SimpleNamespace(organisme="Org", project_name="Project", year="2026"),
        SimpleNamespace(id=12, title="Scientific article", doi="10.1/test", year=2026),
    )

    assert info["path"] == "db://scholar_fulltext_cache/77"
    assert info["visual_source"]["verified_pdf"] is True
    assert info["visual_source"]["same_article"] is True
    assert info["visual_source"]["fulltext_final_url"].endswith("verified.pdf")


def test_keep_decision_backfills_visuals_on_reused_article_card(monkeypatch):
    from services import article_card_builder

    class DummySession:
        def add(self, value):
            return None

        def commit(self):
            return None

    project = SimpleNamespace(id=8)
    article = SimpleNamespace(
        id=91,
        consultant_status="garde",
        source_json={
            "evidence_preflight": {"evidence_status": "FULLTEXT_READY"},
            "target_verrous": ["SV-1"],
        },
    )
    reused_card = {
        "article_id": 91,
        "citation_label": "A91",
        "evidence": {"full_text_available": True},
        "quality_guard": {"status": "valid"},
    }
    visual_source = {
        "found": True,
        "path": "db://scholar_fulltext_cache/91",
        "visual_source": {"verified_pdf": True, "same_article": True},
    }
    calls = []

    monkeypatch.setattr(
        article_card_builder,
        "_load_fulltext",
        lambda project, article: dict(visual_source),
    )
    monkeypatch.setattr(
        article_card_builder,
        "_load_reusable_card",
        lambda project, article, citation_id: (dict(reused_card), "valid_existing_card"),
    )

    def fake_visual_sync(card, **kwargs):
        calls.append(kwargs)
        card["visual_evidence"] = [{"visual_id": "Vkept"}]
        return card

    monkeypatch.setattr(
        article_card_builder,
        "_sync_card_visual_evidence",
        fake_visual_sync,
    )
    monkeypatch.setattr(
        article_card_builder,
        "_current_scholar_run_for_cards",
        lambda db, project: None,
    )
    monkeypatch.setattr(
        article_card_builder,
        "_save_article_cards_payload_to_db",
        lambda db, project, payload, **kwargs: dict(payload),
    )

    result = article_card_builder.sync_article_cards_after_consultant_decision(
        DummySession(),
        project,
        article,
    )

    assert result["article_card_created"] is True
    assert calls[0]["citation_id"] == "A91"
    assert calls[0]["fulltext_info"] == visual_source
    assert article.source_json["article_card"]["visual_evidence"] == [
        {"visual_id": "Vkept"}
    ]


def test_phase5_places_original_figure_only_in_section_citing_same_article():
    from agents.EnnoScholar.state_of_art.phase_5_state_of_art_writer_service import (
        build_visual_placements,
        draft_to_markdown,
    )

    visual = {
        "visual_id": "Vexample123",
        "citation_label": "A1",
        "source_kind": "scientific_article",
        "source_title": "Reliability study",
        "page": 7,
        "figure_label": "Figure 3",
        "caption": "Accuracy and loss curves under operating-regime shift",
        "context": "experimental validation predictive performance",
        "quality_score": 0.9,
        "target_verrous": ["SV-THERMO"],
    }
    cards = [{"citation_label": "A1", "visual_evidence": [visual]}]
    draft = {
        "title": "État de l’art",
        "sections": [
            {
                "section_id": "results",
                "title": "Résultats expérimentaux",
                "content": "La fiabilité varie sous changement de régime [A1].",
                "subsections": [],
            },
            {
                "section_id": "conclusion",
                "title": "Conclusion",
                "content": "Le verrou reste ouvert.",
                "subsections": [],
            },
        ],
    }
    blueprint = {
        "sections": [
            {
                "section_id": "results",
                "objective": "Comparer les performances expérimentales.",
                "verrou_ids": ["SV-THERMO"],
            },
            {"section_id": "conclusion", "objective": "Conclure."},
        ]
    }

    placements = build_visual_placements(draft, blueprint, {}, cards)
    markdown = draft_to_markdown(
        draft,
        {"ok": True},
        visual_placements=placements,
    )

    assert len(placements) == 1
    assert placements[0]["section_id"] == "results"
    assert "![Figure 3 — Accuracy and loss curves" in markdown
    assert "ennoscholar-visual://Vexample123" in markdown
    assert "source [A1]" in markdown
