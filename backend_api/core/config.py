from functools import lru_cache
from pathlib import Path
from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    APP_NAME: str = "EnnoSmart Backend API"
    ENV: str = "dev"

    SECRET_KEY: str = "CHANGE_ME_WITH_A_LONG_RANDOM_SECRET_KEY"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 60
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    DATABASE_URL: str = "sqlite:///./ennosmart_dev.db"
    CORS_ORIGINS: str = "http://localhost:3000,http://127.0.0.1:3000"

    UPLOAD_ROOT: str = "C:/EnnoSmart/storage/uploads"
    AI_OUTPUT_ROOT: str = "C:/EnnoSmart/outputs/safe_rag_upload"

    MAX_UPLOAD_SIZE_MB: int = 50
    ALLOWED_EXTENSIONS: str = ".pdf,.docx,.doc,.pptx,.ppt,.xlsx,.xls,.txt,.png,.jpg,.jpeg,.msg"

    ENNODIAGNOSTIC_SCRIPT: str | None = None
    ENNOSCHOLAR_SCRIPT: str | None = None
    AI_RUN_TIMEOUT_SECONDS: int = 3600

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    @property
    def cors_origins_list(self) -> List[str]:
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",") if origin.strip()]

    @property
    def allowed_extensions_set(self) -> set[str]:
        return {ext.strip().lower() for ext in self.ALLOWED_EXTENSIONS.split(",") if ext.strip()}

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
