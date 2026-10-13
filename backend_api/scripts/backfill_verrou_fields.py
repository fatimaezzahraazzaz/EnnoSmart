import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from db.database import SessionLocal
from db.models import Verrou


def extract_tag(data):
    if not isinstance(data, dict):
        return None

    for key in ("tag_cir", "manual_cir_tag", "decision", "manual_decision", "tag", "label", "cir_tag"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return None


def extract_justification(data):
    if not isinstance(data, dict):
        return None

    for key in ("justification", "manual_justification", "reason", "analysis", "explanation"):
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    return None


def extract_score(data):
    if not isinstance(data, dict):
        return None

    for key in ("score", "confidence", "final_score", "frascati_score", "score_frascati"):
        value = data.get(key)
        try:
            if value is not None:
                return float(value)
        except Exception:
            pass

    frascati = data.get("frascati")
    if isinstance(frascati, dict):
        for key in ("score", "final_score", "frascati_score"):
            value = frascati.get(key)
            try:
                if value is not None:
                    return float(value)
            except Exception:
                pass

    return None


db = SessionLocal()
try:
    verrous = db.query(Verrou).all()
    updated = 0

    for verrou in verrous:
        data = verrou.source_json or {}
        changed = False

        if not verrou.tag_cir:
            tag = extract_tag(data)
            if tag:
                verrou.tag_cir = tag
                changed = True

        if not verrou.justification:
            justification = extract_justification(data)
            if justification:
                verrou.justification = justification
                changed = True

        if verrou.score is None:
            score = extract_score(data)
            if score is not None:
                verrou.score = score
                changed = True

        if changed:
            updated += 1

    db.commit()
    print(f"Verrous mis à jour : {updated}")
finally:
    db.close()
