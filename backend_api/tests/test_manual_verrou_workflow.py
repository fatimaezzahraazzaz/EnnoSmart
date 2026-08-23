from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from db.database import Base
from db.models import DiagnosticRun, Project, User, Verrou
from services.consultant_verrou_service import (
    create_or_reuse_consultant_verrou,
    get_latest_diagnostic_verrous,
)
from services.diagnostic_service import sync_verrous_from_diagnostic
from services.scholar_service import verrou_to_scholar_payload


def _project(db):
    user = User(
        full_name="Consultante Test",
        email="manual-verrou@example.test",
        hashed_password="not-used",
    )
    db.add(user)
    db.flush()
    project = Project(
        consultant_id=user.id,
        organisme="Test",
        project_name="Verrous manuels",
        year="2026",
        domain_label="Matériaux",
    )
    db.add(project)
    db.commit()
    db.refresh(project)
    return project


def _session():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    return sessionmaker(bind=engine)()


def test_manual_verrou_is_auto_kept_and_enriches_scholar_payload():
    db = _session()
    try:
        project = _project(db)
        result = create_or_reuse_consultant_verrou(
            db,
            project,
            title="Variabilité non maîtrisée du procédé thermique",
            justification="La stabilité reste à démontrer selon la température et la cadence.",
            keywords=["stabilité", "température", "cadence", "stabilité"],
            added_via="ennodiagnostic_manual_form",
            created_by_user_id=1,
        )

        verrou = db.query(Verrou).filter(Verrou.id == result["verrou_id"]).one()
        assert verrou.consultant_status == "garde"
        assert verrou.source_json["manual_verrou"] is True
        assert verrou.source_json["keywords"] == ["stabilité", "température", "cadence"]

        scholar_payload = verrou_to_scholar_payload(
            verrou,
            project_name=project.project_name,
            domain_label=project.domain_label or "",
        )
        source_text = scholar_payload["raw_item"]["source_text"]
        assert "stabilité reste à démontrer" in source_text
        assert "température" in source_text
        assert scholar_payload["consultant_status"] == "garde"
    finally:
        db.close()


def test_manual_verrou_survives_same_run_resynchronization():
    db = _session()
    try:
        project = _project(db)
        result = create_or_reuse_consultant_verrou(
            db,
            project,
            title="Incertitude déclarée par le consultant",
            justification="Description humaine conservée.",
            keywords=["incertitude"],
            added_via="ennodiagnostic_manual_form",
        )
        manual = db.query(Verrou).filter(Verrou.id == result["verrou_id"]).one()
        run = db.query(DiagnosticRun).filter(DiagnosticRun.id == manual.diagnostic_run_id).one()
        run.raw_result_json = {
            "report": {
                "verrou_synthesis_report": {
                    "llm_reformulated_verrous": [
                        {
                            "title": "Verrou généré par l’agent",
                            "justification": "Preuve agent.",
                            "score": 0.72,
                        }
                    ]
                }
            }
        }
        db.commit()

        synced = sync_verrous_from_diagnostic(db, run)
        titles = {row.title for row in synced}
        assert "Incertitude déclarée par le consultant" in titles
        assert "Verrou généré par l’agent" in titles
        assert db.query(Verrou).filter(Verrou.id == manual.id).one().consultant_status == "garde"
    finally:
        db.close()


def test_manual_verrou_is_merged_with_a_new_latest_run():
    db = _session()
    try:
        project = _project(db)
        result = create_or_reuse_consultant_verrou(
            db,
            project,
            title="Verrou humain persistant",
            justification="Toujours disponible pour la recherche et la rédaction.",
            keywords=["persistance"],
            added_via="ennodiagnostic_manual_form",
        )
        manual = db.query(Verrou).filter(Verrou.id == result["verrou_id"]).one()

        latest_run = DiagnosticRun(
            project_id=project.id,
            status="completed",
            raw_result_json={"report": {}},
        )
        db.add(latest_run)
        db.flush()
        generated = Verrou(
            diagnostic_run_id=latest_run.id,
            title="Verrou du nouveau diagnostic",
            consultant_status="garde",
        )
        db.add(generated)
        db.commit()

        current = get_latest_diagnostic_verrous(db, project.id)
        assert {row.id for row in current} == {manual.id, generated.id}
    finally:
        db.close()
