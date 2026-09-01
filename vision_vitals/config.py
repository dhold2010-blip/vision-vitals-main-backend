from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


def _csv(name: str, default: str = "") -> tuple[str, ...]:
    return tuple(item.strip() for item in os.getenv(name, default).split(",") if item.strip())


@dataclass(frozen=True)
class Settings:
    app_env: str = os.getenv("APP_ENV", "development")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./vision_vitals.db")
    jwt_secret: str = os.getenv("JWT_SECRET", "")
    jwt_refresh_secret: str = os.getenv("JWT_REFRESH_SECRET", "")
    access_token_expire_minutes: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", "15"))
    refresh_token_expire_days: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", "30"))
    ai_provider: str = os.getenv("AI_PROVIDER", "mock").lower()
    gemini_api_key: str = os.getenv("GEMINI_API_KEY", "")
    gemini_model: str = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
    max_upload_size_mb: int = int(os.getenv("MAX_UPLOAD_SIZE_MB", "10"))
    storage_path: Path = Path(os.getenv("STORAGE_PATH", "./storage"))
    cors_origins: tuple[str, ...] = _csv("CORS_ORIGINS")
    trusted_hosts: tuple[str, ...] = _csv("TRUSTED_HOSTS", "localhost,127.0.0.1")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")

    def validate(self) -> None:
        if len(self.jwt_secret) < 32 or len(self.jwt_refresh_secret) < 32:
            raise RuntimeError("JWT_SECRET and JWT_REFRESH_SECRET must be set and at least 32 characters")
        if self.jwt_secret == self.jwt_refresh_secret:
            raise RuntimeError("JWT signing secrets must be different")
        if self.max_upload_size_mb < 1 or self.max_upload_size_mb > 100:
            raise RuntimeError("MAX_UPLOAD_SIZE_MB must be between 1 and 100")
        if self.ai_provider not in {"mock", "gemini"}:
            raise RuntimeError("AI_PROVIDER must be mock or gemini")


settings = Settings()
