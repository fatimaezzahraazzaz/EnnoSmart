from types import SimpleNamespace

import pytest

from EnnoAmelioration.application import diagnostic_orchestration_service as dos
from EnnoAmelioration.application import research_orchestration_service as ros
from EnnoAmelioration.domain.models import ImprovementRequest, TargetScope


def _request(text: str = "données synthétiques SAR et généralisation ATR") -> ImprovementRequest:
    return ImprovementRequest(
        instruction="Renforce scientifiquement cette section et cherche des preuves.",
        full_text=text,
        target_text=text,
        target_scope=TargetScope.SECTION,
        target_section_id="1.3.2.3",
        target_section_title="Discordance entre données mesurées et simulées",
        project_name="Projet test",
    )


def test_scoped_diagnostic_is_primary_even_if_project_diagnostic_exists(monkeypatch):
    scoped = {
        "available": True,
        "diagnostic_run_id": "scoped:abc",
        "domain_detection": {"main_domain_label": "Radar"},
        "verrous": [{"id": "scope-1", "title": "Verrou scoped"}],
        "evidence_items": [{"type": "diagnostic_lock", "title": "Verrou scoped"}],
    }
    meta = {"mode": "fresh_scoped_ennoamel_input", "executed": True, "project_raw_documents_used": False}
    monkeypatch.setattr(dos, "_run_scoped_diagnostic", lambda *args, **kwargs: (scoped, meta))
    got, got_meta = dos.ensure_diagnostic_context(object(), SimpleNamespace(id=1), _request())
    assert got is scoped
    assert got_meta["mode"] == "fresh_scoped_ennoamel_input"
    assert got_meta["project_raw_documents_used"] is False


def test_full_project_diagnostic_entrypoint_is_not_used(monkeypatch):
    scoped = {
        "available": True,
        "diagnostic_run_id": "scoped:def",
        "domain_detection": {"main_domain_label": "Radar"},
        "verrous": [{"id": "scope-2", "title": "Verrou section"}],
        "evidence_items": [{"type": "diagnostic_lock", "title": "Verrou section"}],
    }
    monkeypatch.setattr(
        dos,
        "_run_scoped_diagnostic",
        lambda *args, **kwargs: (
            scoped,
            {"mode": "fresh_scoped_ennoamel_input", "executed": True, "project_raw_documents_used": False},
        ),
    )

    class FakeService:
        @staticmethod
        def run_ennodiagnostic(db, project):
            raise AssertionError("run_ennodiagnostic(db, project) must not be used by EnnoAmel V2.3")

    monkeypatch.setattr(dos, "_diagnostic_service", lambda: FakeService)
    got, got_meta = dos.ensure_diagnostic_context(object(), SimpleNamespace(id=1), _request())
    assert got["diagnostic_run_id"] == "scoped:def"
    assert got_meta["project_raw_documents_used"] is False


def test_research_never_builds_pseudo_lock_from_section_title():
    with pytest.raises(RuntimeError, match="Aucun verrou EnnoDiagnostic"):
        ros._research_verrous(_request(), {"domain_detection": {}}, [])


def test_section_is_matched_to_specific_diagnostic_lock():
    request = _request(
        "Les données SAR synthétiques manquent de représentativité et les modèles ATR "
        "se généralisent difficilement aux mesures réelles."
    )
    diagnostic = {
        "available": True,
        "domain_detection": {"main_domain_label": "Radar"},
        "evidence_items": [
            {
                "evidence_id": "D:verrou:700",
                "type": "diagnostic_lock",
                "title": "Incertitude sur la représentativité des données SAR synthétiques pour la généralisation des modèles ATR",
                "text": "SAR synthétique, ATR, généralisation vers les mesures réelles",
            },
            {
                "evidence_id": "D:verrou:702",
                "type": "diagnostic_lock",
                "title": "Compromis précision-vitesse de génération multi-configuration radar",
                "text": "temps de calcul et précision numérique",
            },
        ],
    }
    context, ids, items = ros._matched_diagnostic_context(
        object(),
        SimpleNamespace(id=1),
        request,
        diagnostic_override=diagnostic,
    )
    assert ids[0] == "700"
    assert items[0]["evidence_id"] == "D:verrou:700"
    assert context["domain_detection"]["main_domain_label"] == "Radar"
