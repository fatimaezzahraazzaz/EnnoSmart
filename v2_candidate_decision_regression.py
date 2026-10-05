import sys
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


BACKEND = Path(__file__).resolve().parent / "backend_api"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from db.database import Base
from db.models import DiagnosticRun, Project, User, Verrou
from routers.diagnostic import update_v2_entity_decision
from schemas.diagnostic import VerrouDecisionRequest


def test_decision_on_weak_candidate_preserves_existing_verrou():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    user = User(full_name="Consultant", email="candidate@example.com", hashed_password="unused")
    db.add(user)
    db.flush()
    project = Project(
        consultant_id=user.id,
        organisme="Test",
        project_name="Projet",
        year="2026",
    )
    db.add(project)
    db.flush()
    entities = [
        {
            "entity_id": entity_id,
            "kind": "lock",
            "statement": statement,
            "evidences": [{"document_id": "d1", "document_name": "rapport.docx", "quote": statement}],
            "metadata": {
                "qualification_status": qualification,
                "has_project_evidence": True,
                "source_roles": ["project_evidence"],
                "qualification_reason": "Avis de l'agent.",
            },
        }
        for entity_id, qualification, statement in [
            ("v1", "VALID_LOCK", "Une incertitude étayée."),
            ("v2", "REQUIREMENT_ONLY", "Une exigence à examiner."),
        ]
    ]
    run = DiagnosticRun(
        project_id=project.id,
        status="completed",
        raw_result_json={"report": {
            "engine": "ennodiagnostic_v2",
            "diagnostic_v2": {"core": {"entities": entities}},
        }},
    )
    db.add(run)
    db.flush()
    existing = Verrou(
        diagnostic_run_id=run.id,
        title="Une incertitude étayée.",
        tag_cir="PERTINENT",
        consultant_status="garde",
        source_json={"entity_id": "v1"},
    )
    db.add(existing)
    db.commit()

    weak = update_v2_entity_decision(
        project.id, "v2", VerrouDecisionRequest(consultant_status="rejete"), db, user
    )
    assert weak.consultant_status == "rejete"
    assert weak.source_json["qualification_status"] == "REQUIREMENT_ONLY"
    assert db.query(Verrou).filter(Verrou.diagnostic_run_id == run.id).count() == 2
    assert db.get(Verrou, existing.id).consultant_status == "garde"

    retained = update_v2_entity_decision(
        project.id, "v2", VerrouDecisionRequest(consultant_status="garde"), db, user
    )
    assert retained.id == weak.id
    assert retained.consultant_status == "garde"
    assert db.query(Verrou).filter(Verrou.diagnostic_run_id == run.id).count() == 2
    db.close()
    engine.dispose()
