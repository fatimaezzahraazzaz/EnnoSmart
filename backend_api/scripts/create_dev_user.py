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
    email = "fatimaezzahra@ennosmart.local"
    existing = db.query(User).filter(User.email == email).first()

    if existing:
        print("Utilisateur déjà existant :", email)
    else:
        user = User(
            full_name="Fatima Ezzahra",
            email=email,
            hashed_password=hash_password("12345678"),
            role="consultant",
            is_active=True,
        )
        db.add(user)
        db.commit()
        print("Utilisateur créé :", email)
        print("Mot de passe : password123")
finally:
    db.close()
