from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from .db import get_db
from .errors import AppError
from .models import SessionRecord, User
from .security import decode_token


@dataclass(frozen=True)
class AuthContext:
    user: User
    session: SessionRecord
    token_payload: dict


def request_id(request: Request) -> str:
    return request.state.request_id


def current_auth(
    request: Request, db: Session = Depends(get_db)
) -> AuthContext:
    authorization = request.headers.get("Authorization", "")
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