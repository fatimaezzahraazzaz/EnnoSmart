from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


ROOT = Path(__file__).resolve().parent
BACKEND = ROOT / "backend_api"
for candidate in (ROOT, BACKEND):
    if str(candidate) not in sys.path:
        sys.path.insert(0, str(candidate))

from db.database import Base
from db.models import DiagnosticRun, Project, User, Verrou
from services import diagnostic_service


def _v2_report(with_lock: bool = True) -> dict:
    entities = []
    families = []
    if with_lock:
        entities = [
            {
                "entity_id": "lock_1",
                "kind": "lock",
                "statement": "La stabilité reste imprévisible selon le corpus.",
                "evidences": [
                    {
                        "evidence_id": "ev_1",
                        "document_id": "doc_1",
                        "document_name": "rapport.docx",
                        "chunk_id": "chunk_1",
                        "quote": "Les résultats varient fortement.",
                    }
                ],
                "metadata": {
                    "qualification_status": "VALID_LOCK",
                    "qualification_reason": "Incertitude documentée.",
                },
            },
            {
                "entity_id": "engineering_1",
                "kind": "lock",
                "statement": "Le calendrier est difficile à tenir.",
                "evidences": [],
                "metadata": {
                    "qualification_status": "ENGINEERING_DIFFICULTY",
                },
            },
        ]
        families = [
            {
                "family_id": "family_1",
                "title": "Robustesse du modèle",
                "status": "VALID_LOCK",
                "member_entity_ids": ["lock_1"],
                "evidence_ids": ["ev_1"],
            }
        ]

    v2 = {
        "version": "ennodiagnostic_v2_full_runtime_1",
        "core": {"entities": entities, "extractions": [], "chunks": []},
        "lock_families": {"families": families},
        "project_synthesis": {
            "project_summary": "Projet d'étude de robustesse.",
            "main_objective": "Stabiliser les résultats.",
        },
        "rnd_approach": {"status": "RESEARCH_APPROACH"},
        "project_frascati": {"criteria": [], "summary": "Partiel"},
        "scoring": {
            "eligibility": {"score": 82.0},
            "defensibility": {"score": 68.0},
            "risk_level": "Modéré",
            "potential_level": "Fort potentiel CIR",
        },
    }
    return {
        "ok": True,
        "status": "completed",
        "engine": "ennodiagnostic_v2",
        "project_context": {"domain_tags": ["IA & Data"]},
        "documents_count": 1,
        "diagnostic_v2": v2,
        "eligibility": v2["scoring"]["eligibility"],
        "defensibility": v2["scoring"]["defensibility"],
        "risk_level": v2["scoring"]["risk_level"],
        "potential_level": v2["scoring"]["potential_level"],
        "scoring": v2["scoring"],
        "lock_families": v2["lock_families"],
        "entities": entities,
        "extractions": [],
        "project_synthesis": v2["project_synthesis"],
        "rnd_approach": v2["rnd_approach"],
        "project_frascati": v2["project_frascati"],
    }


def test_v2_entities_are_mapped_without_legacy_fields():
    verrous = diagnostic_service._extract_final_verrous_from_report(_v2_report())
    assert len(verrous) == 1
    assert verrous[0]["entity_id"] == "lock_1"
    assert verrous[0]["family_id"] == "family_1"
    assert verrous[0]["tag_cir"] == "PERTINENT"


def test_v2_family_never_promotes_an_excluded_qualification_status():
    report = _v2_report()
    # Garde-fou : même si une famille malformée référence accidentellement
    # une difficulté d'ingénierie, la famille reste une vue d'affichage.
    report["diagnostic_v2"]["lock_families"]["families"][0]["member_entity_ids"].append(
        "engineering_1"
    )

    verrous = diagnostic_service._extract_final_verrous_from_report(report)

    assert [item["entity_id"] for item in verrous] == ["lock_1"]
    assert all(
        item["qualification_status"] in {"VALID_LOCK", "NEEDS_REFORMULATION"}
        for item in verrous
    )


def test_v2_snapshot_saves_all_business_outputs():
    snapshot = diagnostic_service.extract_complete_diagnostic_snapshot(_v2_report())
    assert snapshot["engine"] == "ennodiagnostic_v2"
    assert snapshot["eligibility"]["score"] == 82.0
    assert snapshot["defensibility"]["score"] == 68.0
    assert snapshot["lock_families"]["families"][0]["family_id"] == "family_1"
    assert snapshot["entities"][0]["entity_id"] == "lock_1"
    assert snapshot["project_synthesis"]["main_objective"]
    assert snapshot["rnd_approach"]["status"] == "RESEARCH_APPROACH"
    assert "project_frascati" in snapshot


def test_v2_persistence_does_not_fail_when_no_lock_is_qualified():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    user = User(full_name="Test", email="v2@example.com", hashed_password="unused")
    db.add(user)
    db.flush()
    project = Project(
        consultant_id=user.id,
        organisme="Test Org",
        project_name="Projet V2",
        year="2026",
    )
    db.add(project)
    db.commit()

    with patch.object(diagnostic_service, "_cleanup_diagnostic_json_workfiles", return_value=[]):
        run = diagnostic_service._persist_complete_run(
            db,
            project,
            report=_v2_report(with_lock=False),
            status_value="completed_ennodiagnostic_v2",
            pipeline_name="fulltext_ennodiagnostic_v2_complete_db",
            button="test",
            report_path=None,
            nlp_result_path=None,
            selected_verrous_path=None,
        )

    assert isinstance(run, DiagnosticRun)
    assert db.query(Verrou).filter(Verrou.diagnostic_run_id == run.id).count() == 0
    assert run.raw_result_json["diagnostic_snapshot"]["eligibility"]["score"] == 82.0


def test_backend_entrypoint_runs_ai_on_fulltext_without_v1_nlp():
    project = type(
        "ProjectStub",
        (),
        {
            "id": 7,
            "organisme": "Test Org",
            "project_name": "Projet V2",
            "subproject_name": None,
            "year": "2026",
            "domain_label": "IA & Data",
        },
    )()

    with (
        patch(
            "services.ennodiagnostic_v2_service.run_ennodiagnostic_v2",
            return_value=_v2_report(),
        ),
        patch.object(
            diagnostic_service,
            "run_ai_detector_if_enabled",
            return_value={
                "ok": True,
                "source": "fulltext_rag_v1",
                "summary": {
                    "average_ai_percentage": 12.5,
                    "risk_level": "faible",
                    "passages_count": 2,
                },
                "ai_detection": {"passages": []},
            },
        ) as ai_detector,
        patch.object(
            diagnostic_service,
            "run_nlp_and_rag",
            side_effect=AssertionError("NLP V1 lancé par erreur"),
            create=True,  # The retired function is absent; trap any accidental old call.
        ),
    ):
        report = diagnostic_service.run_true_ennodiagnostic_agent(
            project,
            prior_pipeline={
                "documents_loaded_count": 1,
                "fulltext_corpus": {"documents_count": 1},
            },
        )

    assert report["engine"] == "ennodiagnostic_v2"
    assert report["independent_modules"]["ai_detector"] == "completed_fulltext"
    assert report["ai_detection_report"]["source"] == "fulltext_rag_v1"
    ai_detector.assert_called_once()
    assert report["pipeline_before_agent"]["pipeline"] == "fulltext_to_ennodiagnostic_v2"


def test_single_button_pipeline_indexes_fulltext_before_v2():
    project = type(
        "ProjectStub",
        (),
        {
            "id": 7,
            "organisme": "Test Org",
            "project_name": "Projet V2",
            "subproject_name": None,
            "year": "2026",
        },
    )()
    calls = []

    def fake_rag(_project):
        calls.append("rag")
        return {
            "ok": True,
            "source": "fulltext_rag_v1",
            "nlp_result_used": False,
            "chunks_indexed": 12,
        }

    def fake_v2(_project, prior_pipeline=None):
        calls.append("v2")
        report = _v2_report()
        report["independent_modules"] = {
            "ai_detector": "completed_fulltext",
            "document_compare": "not_run",
            "cir_memory": "not_run",
            "rag": "not_run",
        }
        return report

    with (
        patch.object(
            diagnostic_service,
            "_prepare_ennodiagnostic_v2_sources",
            return_value={
                "documents_used_count": 1,
                "documents_loaded_count": 1,
                "documents_used_paths": ["rapport.docx"],
                "corpus_manifest": {"fingerprint": "abc"},
                "fulltext_corpus": {"documents_count": 1},
                "fulltext_corpus_path": "documents/processed/fulltext_rag_v1",
            },
        ),
        patch.object(diagnostic_service, "save_json_artifact", return_value=None),
        patch.object(diagnostic_service, "run_rag_fulltext_index_if_enabled", side_effect=fake_rag),
        patch.object(diagnostic_service, "run_true_ennodiagnostic_agent", side_effect=fake_v2),
    ):
        result = diagnostic_service.run_full_ennodiagnostic_pipeline(object(), project)

    assert calls == ["rag", "v2"]
    assert result["rag_index_report"]["source"] == "fulltext_rag_v1"
    assert result["rag_index_report"]["nlp_result_used"] is False
    assert result["report"]["independent_modules"]["rag"] == "completed_fulltext"
    assert result["v2_preparation"]["rag_index"]["chunks_indexed"] == 12
