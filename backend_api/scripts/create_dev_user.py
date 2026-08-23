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


Base.metadata.create_all(bind=engine)

db = SessionLocal()
try:
    email = "fatimaezzahra@ennosmart.fr"
    legacy_email = "fatimaezzahra@ennosmart.local"
    password = "12345678"
    existing = db.query(User).filter(User.email == email).first()

    if not existing:
        legacy = db.query(User).filter(User.email == legacy_email).first()
        if legacy:
            legacy.email = email
            db.commit()
            existing = legacy
            print("Adresse de développement corrigée :", email)

    if existing:
        print("Utilisateur déjà existant :", email)
    else:
        user = User(
            full_name="Fatima Ezzahra",
            email=email,
            hashed_password=hash_password(password),
            role="consultant",
            is_active=True,
        )
        db.add(user)
        db.commit()
        print("Utilisateur créé :", email)
        print("Mot de passe :", password)
finally:
    db.close()
