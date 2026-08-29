from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


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

    @field_validator("value")
    @classmethod
    def value_required_when_available(cls, value: float | None, info):
        if info.data.get("availability") == "AVAILABLE" and value is None:
            raise ValueError("value is required when availability is AVAILABLE")
        return value


class HealthMetricData(HealthMetricCreate):
    model_config = ConfigDict(from_attributes=True)
    id: str
    analysis_id: str | None
    created_at: datetime