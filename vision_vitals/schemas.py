from __future__ import annotations

from datetime import datetime, timedelta, timezone
import math
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class Envelope(BaseModel):
    success: bool = True
    data: object
    request_id: str


class ErrorBody(BaseModel):
    code: str
    message: str
    details: object | None = None


class ErrorEnvelope(BaseModel):
    success: bool = False
    error: ErrorBody
    request_id: str


class RegisterRequest(BaseModel):
    email: str = Field(min_length=3, max_length=320)
    password: str = Field(min_length=12, max_length=128)

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        value = value.strip().lower()
        if "@" not in value or value.startswith("@") or value.endswith("@"):
            raise ValueError("A valid email address is required")
        return value


class LoginRequest(RegisterRequest):
    pass


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=20, max_length=4096)


class TokenData(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int


class UserData(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: str
    email: str
    role: Literal["USER", "ADMIN"]
    is_active: bool
    created_at: datetime


class ProfileData(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    display_name: str | None = None
    timezone: str | None = None


class ProfileUpdate(BaseModel):
    display_name: str | None = Field(default=None, max_length=120)
    timezone: str | None = Field(default=None, max_length=64)


class MeData(BaseModel):
    user: UserData
    profile: ProfileData


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=12, max_length=128)


class DeleteAccountRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)


class SessionData(BaseModel):
    id: str
    created_at: datetime
    expires_at: datetime
    last_used_at: datetime | None
    revoked: bool


class AIAnalysisRequest(BaseModel):
    request_id: str
    mime_type: Literal["image/jpeg", "image/png"]
    image_sha256: str


class AIAnalysisResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(min_length=1, max_length=128)
    observation: str = Field(min_length=1, max_length=4000)
    result_status: Literal["OBSERVATION", "ESTIMATION", "UNAVAILABLE", "WARNING"]
    confidence: float | None = Field(default=None, ge=0, le=1)
    warnings: list[str] = Field(default_factory=list, max_length=20)
    limitations: list[str] = Field(default_factory=list, max_length=20)
    request_id: str = Field(min_length=1, max_length=64)


class AnalysisData(BaseModel):
    id: str
    status: str
    created_at: datetime
    updated_at: datetime
    result: AIAnalysisResponse | None = None


class HealthMetricCreate(BaseModel):
    metric_type: str = Field(min_length=1, max_length=64)
    source: Literal[
        "USER_PROVIDED", "HARDWARE_SENSOR", "CAMERA_DERIVED", "AI_INFERRED", "SYSTEM_DERIVED"
    ]
    value: float | None = None
    unit: str | None = Field(default=None, max_length=32)
    quality: str | None = Field(default=None, max_length=32)
    confidence: float | None = Field(default=None, ge=0, le=1)
    availability: Literal["AVAILABLE", "UNAVAILABLE", "UNCERTAIN"] = "AVAILABLE"
    measured_at: datetime

    @model_validator(mode="after")
    def value_required_when_available(self):
        if self.availability == "AVAILABLE" and self.value is None:
            raise ValueError("value is required when availability is AVAILABLE")
        return self


class HealthMetricData(HealthMetricCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    analysis_id: str | None
    created_at: datetime


class DeviceRegisterRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_identifier: str = Field(min_length=1, max_length=128)
    device_name: str = Field(min_length=1, max_length=120)
    device_type: Literal["VISION_VITALS_CAMERA"] = "VISION_VITALS_CAMERA"
    firmware_version: str | None = Field(default=None, max_length=64)
    software_version: str | None = Field(default=None, max_length=64)


class DeviceData(BaseModel):
    id: str
    device_identifier: str
    device_name: str
    device_type: str
    status: str
    firmware_version: str | None
    software_version: str | None
    last_seen_at: datetime | None
    created_at: datetime
    updated_at: datetime


class DeviceRegisterData(DeviceData):
    device_secret: str


class DeviceAuthenticateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_secret: str = Field(min_length=20, max_length=256)


class DeviceAuthenticateByIdRequest(DeviceAuthenticateRequest):
    device_id: str = Field(min_length=1, max_length=36)


class DeviceSessionData(BaseModel):
    device_id: str
    session_identifier: str
    device_session_token: str
    created_at: datetime
    expires_at: datetime


class DeviceHeartbeatRequest(BaseModel):
    software_version: str | None = Field(default=None, max_length=64)
    firmware_version: str | None = Field(default=None, max_length=64)


class DeviceCaptureData(BaseModel):
    id: str
    device_id: str
    user_id: str
    analysis_id: str | None
    capture_type: str
    status: str
    idempotency_key: str
    capture_metadata: dict[str, Any]
    created_at: datetime
    completed_at: datetime | None


class DeviceCaptureMetadata(BaseModel):
    """Small, non-sensitive hardware metadata envelope; image facts are server-derived."""

    model_config = ConfigDict(extra="forbid")

    image_source: Literal["HARDWARE_CAMERA"] = "HARDWARE_CAMERA"
    camera_type: Literal["ESP32_CAM"] = "ESP32_CAM"
    firmware_version: str | None = Field(default=None, max_length=64)
    software_version: str | None = Field(default=None, max_length=64)
    capture_timestamp: datetime | None = None
    sensor_distance_mm: float | None = Field(default=None, gt=0, le=2000)
    image_width: int | None = Field(default=None, ge=1, le=8192)
    image_height: int | None = Field(default=None, ge=1, le=8192)

    @field_validator("capture_timestamp")
    @classmethod
    def validate_capture_timestamp(cls, value: datetime | None) -> datetime | None:
        if value is not None and value.tzinfo is None:
            raise ValueError("capture_timestamp must include a timezone")
        return value.astimezone(timezone.utc) if value is not None else None


class SensorReadingCreate(BaseModel):
    sensor_type: Literal["distance"]
    value: float
    unit: Literal["mm"]
    quality: str | None = Field(default=None, max_length=32)
    timestamp: datetime
    capture_id: str | None = None

    @field_validator("value")
    @classmethod
    def validate_distance(cls, value: float) -> float:
        if not math.isfinite(value) or value <= 0 or value > 2000:
            raise ValueError("distance must be a finite value greater than 0 and at most 2000 mm")
        return value

    @field_validator("timestamp")
    @classmethod
    def validate_timestamp(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must include a timezone")
        now = datetime.now(timezone.utc)
        normalized = value.astimezone(timezone.utc)
        if normalized > now + timedelta(minutes=5):
            raise ValueError("timestamp cannot be more than five minutes in the future")
        return normalized


class SensorReadingData(BaseModel):
    id: str
    device_id: str
    capture_id: str | None
    sensor_type: str
    value: float
    unit: str
    quality: str | None
    timestamp: datetime
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)