from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

from .config import settings
from .errors import AppError

password_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    return password_hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return password_hasher.verify(password_hash, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _secret(token_type: str) -> str:
    secret = settings.jwt_secret if token_type == "access" else settings.jwt_refresh_secret
    if not secret:
        # Development gets a process-local secret; production validation rejects this.
        secret = "development-only-vision-vitals-" + token_type
    return secret


def create_token(user_id: str, session_id: str, token_type: str, expires_delta: timedelta) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "sid": session_id,
        "type": token_type,
        "iat": now,
        "exp": now + expires_delta,
        "jti": secrets.token_urlsafe(18),
    }
    return jwt.encode(payload, _secret(token_type), algorithm="HS256")


def decode_token(token: str, expected_type: str) -> dict:
    try:
        payload = jwt.decode(token, _secret(expected_type), algorithms=["HS256"])
    except jwt.ExpiredSignatureError as exc:
        raise AppError("AUTH_TOKEN_EXPIRED", "The token has expired", 401) from exc
    except jwt.InvalidTokenError as exc:
        raise AppError("AUTH_UNAUTHORIZED", "The token is invalid", 401) from exc
    if payload.get("type") != expected_type or not payload.get("sub") or not payload.get("sid"):
        raise AppError("AUTH_UNAUTHORIZED", "The token is invalid", 401)
    return payload
