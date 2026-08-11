from __future__ import annotations

from agents.EnnoScholar.scholar_agent import (
    _cir_publication_window,
    _filter_articles_to_cir_window,
    _select_relevant_articles_for_output,
)


def test_cir_window_is_dynamic_n_minus_1_not_absolute_1900():
    assert _cir_publication_window(2024) == (1994, 2023, 30)
    assert _cir_publication_window(2030) == (2000, 2029, 30)


def test_cir_filter_excludes_project_year_future_too_old_and_unknown():
    articles = [
        {"title": "ok-new", "year": 2023},
        {"title": "ok-foundational", "year": 1998},
        {"title": "same-year", "year": 2024},
        {"title": "future", "year": 2026},
        {"title": "too-old", "year": 1980},
        {"title": "unknown"},
    ]
    kept, report = _filter_articles_to_cir_window(articles, 2024)
    assert [row["title"] for row in kept] == ["ok-new", "ok-foundational"]
    assert report["min_year"] == 1994
    assert report["max_year"] == 2023
    assert report["removed_reasons"] == {"too_recent": 2, "too_old": 1, "unknown_year": 1}


def test_presentation_filter_rejects_generic_low_precision_and_caps_results(monkeypatch):
    monkeypatch.setenv("ENNOSCHOLAR_PRESENTATION_TOP_K", "3")
    rows = [
        {"title": "D1", "tag": "Direct", "relevance_score": 0.90, "score_details": {"primary_core_hit_count": 2, "specific_anchor_count": 3}},
        {"title": "C1", "tag": "Connexe", "relevance_score": 0.60, "score_details": {"primary_core_hit_count": 1, "specific_anchor_count": 2}},
        {"title": "F1", "tag": "Fondamental", "relevance_score": 0.31, "score_details": {"primary_core_hit_count": 0, "specific_anchor_count": 2}},
        {"title": "generic", "tag": "Fondamental", "relevance_score": 0.20, "score_details": {"primary_core_hit_count": 0, "specific_anchor_count": 1}},
        {"title": "off", "tag": "Hors sujet", "relevance_score": 0.99},
    ]
    selected, report = _select_relevant_articles_for_output(rows, top_n=20)
    assert [row["title"] for row in selected] == ["D1", "C1", "F1"]
    assert report["output_count"] == 3
    assert report["presentation_cap"] == 3
