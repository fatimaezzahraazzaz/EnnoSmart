"""Extraction légère du score d'éligibilité produit par EnnoDiagnostic."""

from __future__ import annotations

import math
from typing import Any


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _nested_dict(value: Any, *keys: str) -> dict[str, Any]:
    current = _as_dict(value)
    for key in keys:
        current = _as_dict(current.get(key))
        if not current:
            return {}
    return current


def _normalize_score(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None

    try:
        score = float(value)
    except (TypeError, ValueError):
        return None

    if not math.isfinite(score) or score < 0:
        return None
    if score > 1:
        if score > 100:
            return None
        score /= 100
    return min(score, 1.0)


def extract_diagnostic_eligibility_score(payload: Any) -> float | None:
    """Retourne le score global EnnoDiagnostic sous forme normalisée (0..1).

    Les runs historiques n'ont pas tous la même enveloppe JSON. L'ordre des
    sources reprend celui du frontend EnnoDiagnostic : indice de défendabilité
    R&D, score d'éligibilité, puis anciens indicateurs Frascati de compatibilité.
    """

    raw = _as_dict(payload)
    if not raw:
        return None

    # Les nouveaux producteurs peuvent exposer directement la valeur canonique.
    direct = _normalize_score(raw.get("eligibility_score"))
    if direct is not None:
        return direct

    snapshot = _as_dict(raw.get("diagnostic_snapshot"))
    report = _as_dict(raw.get("report"))
    script_report = _nested_dict(raw, "script_or_pipeline_result", "report")
    bundle_report = _nested_dict(raw, "bundle", "report")
    display = _as_dict(raw.get("display"))

    frascati_sources = [
        _as_dict(raw.get("frascati_summary")),
        _as_dict(snapshot.get("frascati_summary")),
        _as_dict(report.get("frascati_summary")),
        _as_dict(script_report.get("frascati_summary")),
        _as_dict(bundle_report.get("frascati_summary")),
        _as_dict(display.get("frascati_summary")),
    ]

    prepare_report = _as_dict(raw.get("prepare_sources_report"))
    nlp_sources = [
        _as_dict(raw.get("nlp_stats")),
        _as_dict(prepare_report.get("nlp_stats")),
        _nested_dict(report, "pipeline_before_agent", "nlp_stats"),
        _nested_dict(script_report, "pipeline_before_agent", "nlp_stats"),
        _nested_dict(raw, "script_or_pipeline_result", "pipeline_metadata", "nlp_stats"),
    ]

    for key in ("rnd_defensibility_index", "eligibility_assessment_score"):
        for source in (*frascati_sources, *nlp_sources):
            score = _normalize_score(source.get(key))
            if score is not None:
                return score

    for source in frascati_sources:
        score = _normalize_score(source.get("average_frascati_score"))
        if score is not None:
            return score

    score = _normalize_score(display.get("frascati_score"))
    if score is not None:
        return score

    for source in nlp_sources:
        score = _normalize_score(source.get("global_frascati_score"))
        if score is not None:
            return score

    return None
