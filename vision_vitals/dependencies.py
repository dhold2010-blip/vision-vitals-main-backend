from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, Request, Security
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from .config import settings
from .db import get_db
from .errors import AppError
from .models import Device, DeviceSession, SessionRecord, User
from .security import decode_token, token_hash

bearer_scheme = HTTPBearer(auto_error=False, description="Short-lived user access token")
device_session_scheme = APIKeyHeader(
    name="X-Device-Session",
    auto_error=False,
    description="Short-lived session token issued to a registered device",
)


@dataclass(frozen=True)
class AuthContext:
    user: User
    session: SessionRecord
    token_payload: dict


@dataclass(frozen=True)
class DeviceAuthContext:
    device: Device
    session: DeviceSession
    user: User


def request_id(request: Request) -> str:
    return request.state.request_id


def current_auth(
    request: Request,
    db: Session = Depends(get_db),
    credentials: HTTPAuthorizationCredentials | None = Security(bearer_scheme),
) -> AuthContext:
    authorization = request.headers.get("Authorization", "")
    if credentials is not None:
        authorization = f"{credentials.scheme} {credentials.credentials}"
    if not authorization.lower().startswith("bearer "):
        raise AppError("AUTH_UNAUTHORIZED", "Authentication is required", 401)
    payload = decode_token(authorization[7:].strip(), "access")
    session = db.scalar(select(SessionRecord).where(SessionRecord.id == payload["sid"]))
    user = db.scalar(select(User).where(User.id == payload["sub"]))
    expires_at = session.expires_at if session and session.expires_at.tzinfo else (
        session.expires_at.replace(tzinfo=timezone.utc) if session else None
    )
    if not session or session.revoked_at or expires_at <= datetime.now(timezone.utc):
        raise AppError("AUTH_UNAUTHORIZED", "The session is revoked or expired", 401)
    if not user or not user.is_active:
        raise AppError("AUTH_UNAUTHORIZED", "The account is inactive", 401)
    return AuthContext(user=user, session=session, token_payload=payload)


def admin_auth(auth: AuthContext = Depends(current_auth)) -> AuthContext:
    if auth.user.role != "ADMIN":
        raise AppError("AUTH_FORBIDDEN", "Administrator access is required", 403)
    return auth


def current_device_auth(
    request: Request,
    device_id: str,
    db: Session = Depends(get_db),
    session_header: str | None = Security(device_session_scheme),
) -> DeviceAuthContext:
    if not settings.device_auth_enabled:
        raise AppError("DEVICE_AUTH_DISABLED", "Device authentication is disabled", 503)
    raw_token = session_header or request.headers.get("X-Device-Session", "")
    if not raw_token:
        raise AppError("DEVICE_UNAUTHORIZED", "A device session is required", 401)
    device = db.scalar(select(Device).where(Device.id == device_id))
    session = db.scalar(
        select(DeviceSession).where(
            DeviceSession.device_id == device_id,
            DeviceSession.token_hash == token_hash(raw_token),
        )
    )
    user = db.scalar(select(User).where(User.id == device.owner_user_id)) if device else None
    expires_at = (
        session.expires_at
        if session and session.expires_at.tzinfo
        else session.expires_at.replace(tzinfo=timezone.utc) if session else None
    )
    if (
        not device
        or not session
        or session.revoked_at
        or not expires_at
        or expires_at <= datetime.now(timezone.utc)
        or device.status == "REVOKED"
        or not user
        or not user.is_active
    ):
        raise AppError("DEVICE_UNAUTHORIZED", "The device session is invalid or expired", 401)
    return DeviceAuthContext(device=device, session=session, user=user)