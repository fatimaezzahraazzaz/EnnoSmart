from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


# Chemin robuste vers le .env du backend :
# C:\EnnoSmart\backend_api\.env
BACKEND_DIR = Path(__file__).resolve().parents[1]
ENV_FILE = BACKEND_DIR / ".env"


class Settings(BaseSettings):
    APP_NAME: str = "EnnoSmart Backend API"
    ENV: str = "dev"

    SECRET_KEY: str = "CHANGE_ME_WITH_A_LONG_RANDOM_SECRET_KEY"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7
    PASSWORD_RESET_EXPIRE_MINUTES: int = 30
    FRONTEND_URL: str = "http://127.0.0.1:3000"

    SMTP_HOST: str | None = None
    SMTP_PORT: int = 587
    SMTP_USER: str | None = None
    SMTP_PASSWORD: str | None = None
    SMTP_FROM: str = "no-reply@ennoma.app"
    SMTP_USE_TLS: bool = True

    DATABASE_URL: str = "sqlite:///./ennosmart_dev.db"
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    UPLOAD_ROOT: str = "C:/EnnoSmart/storage/uploads"
    AI_OUTPUT_ROOT: str = "C:/EnnoSmart/outputs/safe_rag_upload"

    MAX_UPLOAD_SIZE_MB: int = 50
    ALLOWED_EXTENSIONS: str = ".pdf,.docx,.doc,.pptx,.ppt,.xlsx,.xls,.txt,.png,.jpg,.jpeg,.msg"

    ENNODIAGNOSTIC_SCRIPT: str | None = None
    ENNOSCHOLAR_SCRIPT: str | None = None
    AI_RUN_TIMEOUT_SECONDS: int = 3600

    # Bibliothèque professionnelle synchronisée, parcourue strictement en lecture seule.
    POWER_AUTOMATE_IMPORT_ROOT: str = "C:/Users/dell/OneDrive - Ennodev/ENNODEV - Clients"
    POWER_AUTOMATE_FAKE_ROOT: str = "C:/EnnoSmart/tests/fixtures/fake_power_automate_inbox"
    POWER_AUTOMATE_AUDIT_ROOT: str = "C:/EnnoSmart/storage/power_automate_import"
    POWER_AUTOMATE_MAX_FILE_MB: int = 100

    # IMPORTANT :
    # extra="ignore" évite que Pydantic bloque les variables IA/LLM/EnnoScholar
    # comme GEMINI_API_KEY, ENNOSCHOLAR_ENABLE_BGE_RERANKER, OLLAMA_MODEL, etc.
    model_config = SettingsConfigDict(
        env_file=str(ENV_FILE),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    @property
    def cors_origins_list(self) -> List[str]:
        return [
            origin.strip()
            for origin in self.CORS_ORIGINS.split(",")
            if origin.strip()
        ]

    @property
    def allowed_extensions_set(self) -> set[str]:
        return {
            ext.strip().lower()
            for ext in self.ALLOWED_EXTENSIONS.split(",")
            if ext.strip()
        }

    @property
    def upload_root_path(self) -> Path:
        return Path(self.UPLOAD_ROOT)

    @property
    def ai_output_root_path(self) -> Path:
        return Path(self.AI_OUTPUT_ROOT)


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
