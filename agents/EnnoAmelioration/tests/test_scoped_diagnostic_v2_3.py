from __future__ import annotations

from types import SimpleNamespace

from EnnoAmelioration.application import diagnostic_orchestration_service as svc
from EnnoAmelioration.domain.models import ImprovementRequest, TargetScope


def _request() -> ImprovementRequest:
    target = (
        "Les données simulées ne reproduisent pas complètement les fonds mesurés. "
        "Cette discordance peut biaiser l'apprentissage et limiter la généralisation."
    )
    full = "Introduction générale.\n\n" + target + "\n\nTravaux expérimentaux et résultats."
    return ImprovementRequest(
        instruction="Renforce ce verrou et cherche des preuves scientifiques.",
        full_text=full,
        target_text=target,
        target_scope=TargetScope.SECTION,
        target_section_id="1.3.2.3",
        target_section_title="Discordance données mesurées et simulées",
        project_name="Projet test",
    )


def test_virtual_documents_use_only_current_ennoamel_input():
    req = _request()
    docs, modes = svc._virtual_documents(req, "abc123")
    assert docs
    assert docs[0]["text"] == req.target_text
    assert docs[0]["source_path"].startswith("ennoamel://scope/")
    assert docs[0]["content_origin"] == "ennoamel_cir_target"
    assert all(modes[row["document"]] == "pre_cir" for row in docs)
    # Aucun chemin DB / RAW projet ne doit être injecté.
    blob = repr(docs).lower()
    assert "documents_raw" not in blob
    assert "postgres" not in blob


def test_context_from_scoped_nlp_exposes_scoped_lock_only():
    req = _request()
    nlp = {
        "domain_detection": {"display_label": "Imagerie radar"},
        "stats": {"qualified_lock_groups": 1},
        "frascati_guard": {
            "verrous_probables": [
                {
                    "lock_group_id": "L1",
                    "candidate_group_label": "Incertitude sur la représentativité des données synthétiques",
                    "text": "Les données simulées ne reproduisent pas complètement les fonds mesurés.",
                    "frascati_decision": "verrou_probable",
                    "frascati_score": 0.8,
                    "display_as_main_lock": True,
                }
            ]
        },
        "multi_document_evidence_pack_for_ennodiagnostic": {},
    }
    context = svc._context_from_scoped_run(
        request=req,
        scope_key="abc",
        scope_project="scope_project",
        nlp_result=nlp,
        report={},
        index_report={},
        report_path="x.json",
        cache_hit=False,
        project_background={
            "available": True,
            "diagnostic_run_id": 99,
            "verrous": [{"id": 702, "title": "Autre verrou projet"}],
            "domain_detection": {"display_label": "Radar"},
        },
    )
    assert context["available"] is True
    assert context["project_raw_documents_used"] is False
    assert context["verrous"][0]["title"].startswith("Incertitude")
    assert context["evidence_items"][0]["evidence_id"].startswith("D:verrou:scope-")
    # Le verrou historique n'est pas mélangé aux preuves principales.
    assert all("Autre verrou projet" not in str(row) for row in context["evidence_items"])
    assert context["project_background"]["verrous_count"] == 1


def test_ensure_diagnostic_context_never_calls_full_project_pipeline(monkeypatch):
    req = _request()
    project = SimpleNamespace(id=1, project_name="P", organisme="Org", year=2025)
    expected = ({"available": True, "evidence_items": [{"type": "diagnostic_lock"}]}, {"mode": "fresh_scoped_ennoamel_input"})

    monkeypatch.setattr(svc, "_run_scoped_diagnostic", lambda *args, **kwargs: expected)

    # Si V2.2 revenait, cette fonction serait utilisée ; elle ne doit jamais l'être.
    class BadService:
        def run_ennodiagnostic(self, *args, **kwargs):
            raise AssertionError("full project pipeline must not run")

    monkeypatch.setattr(svc, "_diagnostic_service", lambda: BadService())
    got = svc.ensure_diagnostic_context(object(), project, req)
    assert got == expected
