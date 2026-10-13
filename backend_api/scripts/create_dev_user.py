import os
import sys
from pathlib import Path

# Permet de lancer :
# python scripts/create_dev_user.py
# sans erreur "No module named db"
ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from db.database import SessionLocal, Base, engine
from db.models import User
from core.security import hash_password


email = os.environ.get("ENNOSMART_DEV_EMAIL", "")
password = os.environ.get("ENNOSMART_DEV_PASSWORD", "")
if not email or not password:
    raise SystemExit("Définissez ENNOSMART_DEV_EMAIL et ENNOSMART_DEV_PASSWORD avant de créer un utilisateur.")

Base.metadata.create_all(bind=engine)

db = SessionLocal()
try:
    legacy_email = os.environ.get("ENNOSMART_DEV_LEGACY_EMAIL", "")
    existing = db.query(User).filter(User.email == email).first()

    if not existing:
        legacy = db.query(User).filter(User.email == legacy_email).first() if legacy_email else None
        if legacy:
            legacy.email = email
            db.commit()
            existing = legacy
            print("Adresse de développement corrigée :", email)

    if existing:
        print("Utilisateur déjà existant :", email)
    else:
        user = User(
            full_name=os.environ.get("ENNOSMART_DEV_FULL_NAME", "Development User"),
            email=email,
            hashed_password=hash_password(password),
            role="consultant",
            is_active=True,
        )
        db.add(user)
        db.commit()
        print("Utilisateur créé :", email)
finally:
    db.close()
