from datetime import datetime, timedelta
from email.message import EmailMessage
import hashlib
import secrets
import smtplib
import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from core.config import settings
from core.deps import get_current_user, get_db
from core.security import (
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_password,
    verify_password,
)
from db.models import PasswordResetToken, User, UserPreference, UserProfile
from schemas.auth import (
    ChangePasswordRequest,
    ForgotPasswordRequest,
    LoginRequest,
    PreferencesUpdate,
    ProfileUpdate,
    RefreshRequest,
    RegisterRequest,
    ResetPasswordRequest,
    TokenResponse,
    UserRead,
)


router = APIRouter(prefix="/auth", tags=["auth"])


def _clean_optional(value: str | None) -> str | None:
    cleaned = str(value or "").strip()
    return cleaned or None


def _get_or_create_profile(db: Session, user: User) -> UserProfile:
    profile = db.query(UserProfile).filter(UserProfile.user_id == user.id).first()
    if not profile:
        profile = UserProfile(user_id=user.id)
        db.add(profile)
        db.flush()
    return profile


def _get_or_create_preferences(db: Session, user: User) -> UserPreference:
    preferences = db.query(UserPreference).filter(UserPreference.user_id == user.id).first()
    if not preferences:
        preferences = UserPreference(user_id=user.id)
        db.add(preferences)
        db.flush()
    return preferences


def _account_payload(db: Session, user: User) -> dict:
    profile = _get_or_create_profile(db, user)
    preferences = _get_or_create_preferences(db, user)
    db.commit()
    return {
        "user": UserRead.model_validate(user).model_dump(mode="json"),
        "profile": {
            "job_title": profile.job_title,
            "company": profile.company,
            "phone": profile.phone,
            "bio": profile.bio,
            "avatar_url": profile.avatar_url,
            "updated_at": profile.updated_at.isoformat() if profile.updated_at else None,
        },
        "preferences": {
            "language": preferences.language,
            "timezone": preferences.timezone,
            "theme": preferences.theme,
            "compact_sidebar": preferences.compact_sidebar,
            "email_notifications": preferences.email_notifications,
            "project_notifications": preferences.project_notifications,
            "weekly_summary": preferences.weekly_summary,
            "updated_at": preferences.updated_at.isoformat() if preferences.updated_at else None,
        },
    }


def _send_reset_email(email: str, reset_url: str) -> bool:
    if not settings.SMTP_HOST:
        return False
    message = EmailMessage()
    message["Subject"] = "Réinitialisation de votre mot de passe Ennoma"
    message["From"] = settings.SMTP_FROM
    message["To"] = email
    message.set_content(
        "Une demande de réinitialisation a été reçue pour votre compte Ennoma.\n\n"
        f"Ouvrez ce lien (valable {settings.PASSWORD_RESET_EXPIRE_MINUTES} minutes) :\n"
        f"{reset_url}\n\nSi vous n'êtes pas à l'origine de cette demande, ignorez cet e-mail."
    )
    try:
        with smtplib.SMTP(settings.SMTP_HOST, settings.SMTP_PORT, timeout=15) as client:
            if settings.SMTP_USE_TLS:
                client.starttls()
            if settings.SMTP_USER:
                client.login(settings.SMTP_USER, settings.SMTP_PASSWORD or "")
            client.send_message(message)
        return True
    except Exception:
        return False


@router.post("/register", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)):
    email = payload.email.lower().strip()

    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Cet email est déjà utilisé.",
        )

    user = User(
        full_name=payload.full_name.strip(),
        email=email,
        hashed_password=hash_password(payload.password),
        role="consultant",
        is_active=True,
    )

    db.add(user)
    db.flush()
    db.add(
        UserProfile(
            user_id=user.id,
            company=_clean_optional(payload.company),
            job_title=_clean_optional(payload.job_title),
        )
    )
    db.add(UserPreference(user_id=user.id))
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, db: Session = Depends(get_db)):
    email = payload.email.lower().strip()
    user = db.query(User).filter(User.email == email).first()

    if not user or not verify_password(payload.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email ou mot de passe incorrect.",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Compte désactivé.",
        )

    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@router.post("/refresh", response_model=TokenResponse)
def refresh_token(payload: RefreshRequest, db: Session = Depends(get_db)):
    decoded = decode_token(payload.refresh_token, expected_type="refresh")
    user_id = int(decoded["sub"])

    user = db.query(User).filter(User.id == user_id).first()
    if not user or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token invalide.",
        )

    return TokenResponse(
        access_token=create_access_token(user.id),
        refresh_token=create_refresh_token(user.id),
    )


@router.get("/me", response_model=UserRead)
def me(current_user: User = Depends(get_current_user)):
    return current_user


@router.get("/me/account")
def get_account(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    return _account_payload(db, current_user)


@router.patch("/me/profile")
def update_profile(
    payload: ProfileUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    fields = payload.model_fields_set
    if "email" in fields and payload.email:
        email = str(payload.email).lower().strip()
        duplicate = db.query(User).filter(User.email == email, User.id != current_user.id).first()
        if duplicate:
            raise HTTPException(status_code=409, detail="Cet email est déjà utilisé.")
        current_user.email = email
    if "full_name" in fields and payload.full_name:
        current_user.full_name = payload.full_name.strip()

    profile = _get_or_create_profile(db, current_user)
    for field in ("job_title", "company", "phone", "bio", "avatar_url"):
        if field in fields:
            setattr(profile, field, _clean_optional(getattr(payload, field)))
    profile.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(current_user)
    return _account_payload(db, current_user)


@router.put("/me/preferences")
def update_preferences(
    payload: PreferencesUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    preferences = _get_or_create_preferences(db, current_user)
    for field, value in payload.model_dump().items():
        setattr(preferences, field, value)
    preferences.updated_at = datetime.utcnow()
    db.commit()
    return _account_payload(db, current_user)


@router.post("/change-password")
def change_password(
    payload: ChangePasswordRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if not verify_password(payload.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="Le mot de passe actuel est incorrect.")
    if payload.current_password == payload.new_password:
        raise HTTPException(status_code=400, detail="Le nouveau mot de passe doit être différent.")
    current_user.hashed_password = hash_password(payload.new_password)
    db.commit()
    return {"status": "ok", "message": "Mot de passe mis à jour."}


@router.post("/forgot-password")
def forgot_password(payload: ForgotPasswordRequest, db: Session = Depends(get_db)):
    """Réponse neutre pour empêcher l'énumération des comptes."""
    email = str(payload.email).lower().strip()
    user = db.query(User).filter(User.email == email, User.is_active.is_(True)).first()
    response = {
        "status": "ok",
        "message": "Si ce compte existe, un lien de réinitialisation vient d'être envoyé.",
    }
    if not user:
        return response

    now = datetime.utcnow()
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used_at.is_(None),
    ).update({"used_at": now}, synchronize_session=False)
    raw_token = secrets.token_urlsafe(48)
    token = PasswordResetToken(
        id=str(uuid.uuid4()),
        user_id=user.id,
        token_hash=hashlib.sha256(raw_token.encode("utf-8")).hexdigest(),
        expires_at=now + timedelta(minutes=settings.PASSWORD_RESET_EXPIRE_MINUTES),
    )
    db.add(token)
    db.commit()
    reset_url = f"{settings.FRONTEND_URL.rstrip('/')}?reset_token={raw_token}"
    _send_reset_email(email, reset_url)
    if settings.ENV.lower() not in {"prod", "production"}:
        response["preview_token"] = raw_token
        response["reset_url"] = reset_url
    return response


@router.post("/reset-password")
def reset_password(payload: ResetPasswordRequest, db: Session = Depends(get_db)):
    token_hash = hashlib.sha256(payload.token.encode("utf-8")).hexdigest()
    reset = (
        db.query(PasswordResetToken)
        .filter(
            PasswordResetToken.token_hash == token_hash,
            PasswordResetToken.used_at.is_(None),
        )
        .first()
    )
    if not reset or reset.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Ce lien est invalide ou a expiré.")
    user = db.query(User).filter(User.id == reset.user_id, User.is_active.is_(True)).first()
    if not user:
        raise HTTPException(status_code=400, detail="Ce lien est invalide ou a expiré.")
    user.hashed_password = hash_password(payload.password)
    reset.used_at = datetime.utcnow()
    db.query(PasswordResetToken).filter(
        PasswordResetToken.user_id == user.id,
        PasswordResetToken.used_at.is_(None),
    ).update({"used_at": datetime.utcnow()}, synchronize_session=False)
    db.commit()
    return {"status": "ok", "message": "Mot de passe réinitialisé. Vous pouvez vous connecter."}


@router.post("/logout")
def logout():
    return {
        "status": "ok",
        "message": "Déconnexion côté frontend : supprimer les tokens stockés.",
    }
