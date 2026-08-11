from __future__ import annotations

import io
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

