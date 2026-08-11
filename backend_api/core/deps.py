from typing import Callable

from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from core.security import decode_token
from db.database import SessionLocal
from db.models import PlatformSetting, User


bearer_scheme = HTTPBearer(auto_error=True)
ADMIN_ROLES = {"admin", "superadmin"}
VALID_ROLES = {"consultant", "admin", "superadmin"}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    token = credentials.credentials
    payload = decode_token(token, expected_type="access")
    user_id = int(payload["sub"])

    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Utilisateur introuvable.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Compte désactivé.",
        )

    return user


def require_roles(*roles: str) -> Callable:
    allowed = {role.strip().lower() for role in roles}

    def dependency(current_user: User = Depends(get_current_user)) -> User:
        if (current_user.role or "").strip().lower() not in allowed:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Vous n'avez pas les droits nécessaires pour cette action.",
            )
        return current_user

    return dependency


require_admin = require_roles("admin", "superadmin")
require_superadmin = require_roles("superadmin")


def require_agent_enabled(agent_key: str) -> Callable:
    def dependency(db: Session = Depends(get_db)) -> None:
        setting = db.query(PlatformSetting).filter(PlatformSetting.key == "ai_models").first()
        enabled_agents = dict((setting.value_json or {}).get("enabled_agents") or {}) if setting else {}
        if enabled_agents.get(agent_key, True) is False:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"Le module {agent_key} est temporairement désactivé par l'administration.",
            )

    return dependency
