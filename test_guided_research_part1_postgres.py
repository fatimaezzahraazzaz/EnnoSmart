from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(r"C:\EnnoSmart")
for path in [ROOT, ROOT / "backend_api"]:
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from db.database import SessionLocal
from db.models import Project
from modules.guided_research.application.session_state_manager import GuidedResearchSessionStateManager
from modules.guided_research.domain.enums import (
    ArtifactType,
    ConversationRole,
    EntryModule,
    GuidedResearchState,
    TargetMode,
)
from modules.guided_research.domain.models import ArtifactWrite, MessageCreate, SessionCreate
from modules.guided_research.infrastructure.persistence.session_repository import GuidedResearchSessionRepository


def main() -> int:
    db = SessionLocal()
    repo = GuidedResearchSessionRepository()
    manager = GuidedResearchSessionStateManager(repo)
    try:
        project = db.query(Project).order_by(Project.id.asc()).first()
        if project is None:
            print("[TEST] Aucun Project disponible dans PostgreSQL.")
            return 2

        session = repo.create_session(
            db,
            SessionCreate(
                project_id=project.id,
                entry_module=EntryModule.ENNOSCHOLAR,
                target_mode=TargetMode.GLOBAL,
                initial_context={
                    "test": True,
                    "project_name": getattr(project, "project_name", None),
                    "year": getattr(project, "year", None),
                },
            ),
        )
        repo.append_message(
            db,
            session.id,
            MessageCreate(
                role=ConversationRole.CONSULTANT,
                content="Test PostgreSQL du module guided_research.",
                intent="integration_test",
                payload={"source": "test_guided_research_part1_postgres.py"},
            ),
        )
        repo.save_artifact(
            db,
            session.id,
            ArtifactWrite(
                artifact_type=ArtifactType.CONSULTANT_BRIEF,
                payload={
                    "requested_topics": ["CNN", "MOCEM"],
                    "requested_plan": ["Historique", "Méthodes", "Limites"],
                },
                created_by="postgres_integration_test",
            ),
        )
        manager.transition(
            db,
            session.id,
            GuidedResearchState.BRIEF_PARSED,
            reason="Test PostgreSQL",
        )
        db.commit()

        bundle = repo.get_bundle(db, session.id)
        print("[TEST] OK")
        print(json.dumps(bundle.model_dump(mode="json"), ensure_ascii=False, indent=2))

        repo.delete_session(db, session.id)
        db.commit()
        print("[TEST] Nettoyage OK")
        return 0
    except Exception as exc:
        db.rollback()
        print(f"[TEST] ERREUR: {type(exc).__name__}: {exc}")
        return 1
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
