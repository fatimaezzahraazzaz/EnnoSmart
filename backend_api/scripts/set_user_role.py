"""Attribue explicitement un rôle Ennoma à un compte existant.

Usage depuis ``backend_api`` :
    python -m scripts.set_user_role olivier@ennoma.fr admin
    python -m scripts.set_user_role it@ennoma.fr superadmin
"""

from __future__ import annotations

import argparse

from db.database import Base, SessionLocal, engine
from db.models import User


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("email")
    parser.add_argument("role", choices=("consultant", "admin", "superadmin"))
    args = parser.parse_args()

    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        user = db.query(User).filter(User.email == args.email.lower().strip()).first()
        if not user:
            raise SystemExit(f"Compte introuvable : {args.email}")
        previous = user.role
        user.role = args.role
        db.commit()
        print(f"Rôle mis à jour : {user.email} — {previous} -> {user.role}")
    finally:
        db.close()


if __name__ == "__main__":
    main()
