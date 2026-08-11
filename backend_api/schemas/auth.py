from datetime import datetime
from pydantic import BaseModel, ConfigDict, EmailStr, Field


class RegisterRequest(BaseModel):
    full_name: str = Field(min_length=2, max_length=255)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)
    company: str | None = Field(default=None, max_length=255)
    job_title: str | None = Field(default=None, max_length=255)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserRead(BaseModel):
    id: int
    full_name: str
    email: EmailStr
    role: str
    is_active: bool
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str = Field(min_length=20, max_length=512)
    password: str = Field(min_length=8, max_length=128)


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8, max_length=128)


class ProfileUpdate(BaseModel):
    full_name: str | None = Field(default=None, min_length=2, max_length=255)
    email: EmailStr | None = None
    job_title: str | None = Field(default=None, max_length=255)
    company: str | None = Field(default=None, max_length=255)
    phone: str | None = Field(default=None, max_length=50)
    bio: str | None = Field(default=None, max_length=2000)
    avatar_url: str | None = Field(default=None, max_length=2000)


class PreferencesUpdate(BaseModel):
    language: str = Field(default="fr", min_length=2, max_length=10)
    timezone: str = Field(default="Africa/Casablanca", min_length=2, max_length=100)
    theme: str = Field(default="system", pattern="^(light|dark|system)$")
    compact_sidebar: bool = False
    email_notifications: bool = True
    project_notifications: bool = True
    weekly_summary: bool = True
