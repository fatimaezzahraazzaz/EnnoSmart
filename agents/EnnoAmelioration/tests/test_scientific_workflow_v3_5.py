from types import SimpleNamespace

from agents.EnnoScholar.state_of_art.existing_review_enrichment_service import (
    _addition_completeness_errors,
)
from backend_api.services.improvement_service import _working_version


def _version(version_id: str, number: int, status: str, content: str):
    return SimpleNamespace(
        id=version_id,
        version_number=number,
        status=status,
        content=content,
    )


def test_unvalidated_candidate_is_not_default_working_base():
    original = _version("v1", 1, "original", "SOURCE ACTIVE")
    candidate = _version("v2", 2, "candidate", "CANDIDATE NON VALIDEE")
    session = SimpleNamespace(versions=[original, candidate], active_version_id="v1")
    selected = _working_version(session)
    assert selected.id == "v1"


def test_candidate_is_used_only_for_explicit_continuation():
    original = _version("v1", 1, "original", "SOURCE ACTIVE")
    candidate = _version("v2", 2, "candidate", "CANDIDATE")
    session = SimpleNamespace(versions=[original, candidate], active_version_id="v1")
    selected = _working_version(session, prefer_candidate=True)
    assert selected.id == "v2"


def test_scientific_addition_rejects_uncited_sentence():
    content = (
        "Les travaux montrent une variabilité importante selon les conditions "
        "d'observation et les configurations de cible, ce qui limite la "
        "généralisation directe des modèles [A1]. "
        "Cette difficulté impose une validation expérimentale complémentaire "
        "pour les nouvelles conditions opérationnelles."
    )
    errors = _addition_completeness_errors(content)
    assert any("sans_citation" in error for error in errors)


def test_scientific_addition_rejects_incomplete_tail():
    content = (
        "Les travaux montrent une sensibilité aux conditions hors distribution "
        "et recommandent une analyse spécifique de robustesse [A1]. "
        "Par conséquent, la validation expérimentale spécifique au cadre du projet"
    )
    errors = _addition_completeness_errors(content)
    assert "phrase_finale_incomplete" in errors


def test_complete_cited_addition_is_accepted_by_guard():
    content = (
        "Les travaux montrent que les performances des systèmes SAR ATR peuvent "
        "varier lorsque les conditions opérationnelles diffèrent de celles "
        "couvertes pendant l'apprentissage [A1]."
    )
    errors = _addition_completeness_errors(content)
    assert errors == []
