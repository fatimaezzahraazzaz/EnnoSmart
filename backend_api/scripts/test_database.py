import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from db.database import Base, engine, SessionLocal
from db.models import Article, DiagnosticRun, Document, Project, ScholarRun, User, Verrou


def main():
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()

    try:
        print("Connexion DB OK")
        print("users:", db.query(User).count())
        print("projects:", db.query(Project).count())
        print("documents:", db.query(Document).count())
        print("diagnostic_runs:", db.query(DiagnosticRun).count())
        print("verrous:", db.query(Verrou).count())
        print("scholar_runs:", db.query(ScholarRun).count())
        print("articles:", db.query(Article).count())
    finally:
        db.close()


if __name__ == "__main__":
    main()
