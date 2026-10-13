import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from db.database import SessionLocal
from db.models import DiagnosticRun, Verrou
from services.diagnostic_service import sanitize_json_value


def main():
    db = SessionLocal()

    try:
        diagnostic_count = 0
        verrou_count = 0

        for run in db.query(DiagnosticRun).all():
            cleaned = sanitize_json_value(run.raw_result_json)

            if cleaned != run.raw_result_json:
                run.raw_result_json = cleaned
                diagnostic_count += 1

        for verrou in db.query(Verrou).all():
            cleaned = sanitize_json_value(verrou.source_json)

            if cleaned != verrou.source_json:
                verrou.source_json = cleaned
                verrou_count += 1

            if verrou.score is not None:
                cleaned_score = sanitize_json_value(verrou.score)
                if cleaned_score is None:
                    verrou.score = None
                    verrou_count += 1

        db.commit()

        print(f"DiagnosticRun nettoyés : {diagnostic_count}")
        print(f"Verrous nettoyés : {verrou_count}")
        print("Nettoyage NaN terminé.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
