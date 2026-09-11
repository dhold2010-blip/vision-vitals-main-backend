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
    device_auth_enabled: bool = os.getenv("DEVICE_AUTH_ENABLED", "true").lower() in {"1", "true", "yes"}
    device_session_expire_minutes: int = int(os.getenv("DEVICE_SESSION_EXPIRE_MINUTES", "60"))
    device_heartbeat_timeout_seconds: int = int(os.getenv("DEVICE_HEARTBEAT_TIMEOUT_SECONDS", "300"))
    device_capture_rate_limit: int = int(os.getenv("DEVICE_CAPTURE_RATE_LIMIT", "12"))
    device_sensor_rate_limit: int = int(os.getenv("DEVICE_SENSOR_RATE_LIMIT", "60"))
    device_auth_rate_limit: int = int(os.getenv("DEVICE_AUTH_RATE_LIMIT", "10"))
    device_registration_rate_limit: int = int(os.getenv("DEVICE_REGISTRATION_RATE_LIMIT", "5"))
    device_heartbeat_rate_limit: int = int(os.getenv("DEVICE_HEARTBEAT_RATE_LIMIT", "30"))
    auth_login_rate_limit: int = int(os.getenv("AUTH_LOGIN_RATE_LIMIT", "10"))
    auth_registration_rate_limit: int = int(os.getenv("AUTH_REGISTRATION_RATE_LIMIT", "5"))
    auth_refresh_rate_limit: int = int(os.getenv("AUTH_REFRESH_RATE_LIMIT", "20"))
    password_rate_limit: int = int(os.getenv("PASSWORD_RATE_LIMIT", "5"))
    device_min_image_width: int = int(os.getenv("DEVICE_MIN_IMAGE_WIDTH", "8"))
    device_min_image_height: int = int(os.getenv("DEVICE_MIN_IMAGE_HEIGHT", "8"))
    max_image_width: int = int(os.getenv("MAX_IMAGE_WIDTH", "8192"))
    max_image_height: int = int(os.getenv("MAX_IMAGE_HEIGHT", "8192"))
    max_image_pixels: int = int(os.getenv("MAX_IMAGE_PIXELS", "40000000"))
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
        if self.device_session_expire_minutes < 1:
            raise RuntimeError("DEVICE_SESSION_EXPIRE_MINUTES must be positive")
        if self.device_heartbeat_timeout_seconds < 1:
            raise RuntimeError("DEVICE_HEARTBEAT_TIMEOUT_SECONDS must be positive")
        for name in (
            "device_capture_rate_limit",
            "device_sensor_rate_limit",
            "device_auth_rate_limit",
            "device_registration_rate_limit",
            "device_heartbeat_rate_limit",
            "auth_login_rate_limit",
            "auth_registration_rate_limit",
            "auth_refresh_rate_limit",
            "password_rate_limit",
        ):
            if getattr(self, name) < 1:
                raise RuntimeError(f"{name.upper()} must be positive")
        if self.device_min_image_width < 1 or self.device_min_image_height < 1:
            raise RuntimeError("Device image minimum dimensions must be positive")
        if (
            self.max_image_width < self.device_min_image_width
            or self.max_image_height < self.device_min_image_height
            or self.max_image_pixels < 1
        ):
            raise RuntimeError("Maximum image dimensions and pixel count must be positive and usable")
        if self.ai_provider not in {"mock", "gemini"}:
            raise RuntimeError("AI_PROVIDER must be mock or gemini")


settings = Settings()
