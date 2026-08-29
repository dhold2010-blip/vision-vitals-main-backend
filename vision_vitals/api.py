from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Request, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select, update
from sqlalchemy.orm import Session, joinedload

from .ai import get_ai_provider
from .config import settings
from .db import get_db
from .dependencies import AuthContext, admin_auth, current_auth, request_id
from .errors import AppError
from .models import Analysis, AuditEvent, HealthMetric, SessionRecord, User, UserProfile
from .schemas import (
    AnalysisData,
    AIAnalysisResponse,
    Envelope,
    HealthMetricCreate,
    HealthMetricData,
    LoginRequest,
    MeData,
    PasswordChange,
    ProfileData,
    ProfileUpdate,
    RefreshRequest,
    RegisterRequest,
    SessionData,
    TokenData,
    UserData,
)
from .security import create_token, hash_password, token_hash, verify_password
from .services import VisionAnalysisService
from .storage import LocalStorageProvider

router = APIRouter(prefix="/api/v1")


def envelope(data, rid: str):
    return {"success": True, "data": data, "request_id": rid}


def _tokens(db: Session, user: User, user_agent: str | None) -> TokenData:
    session = SessionRecord(
        user_id=user.id,
        refresh_token_hash="pending",
        expires_at=datetime.now(timezone.utc) + timedelta(days=settings.refresh_token_expire_days),
        user_agent=(user_agent or "")[:256] or None,
    )
    db.add(session)
    db.flush()
    access = create_token(
        user.id, session.id, "access", timedelta(minutes=settings.access_token_expire_minutes)
    )
    refresh = create_token(
        user.id, session.id, "refresh", timedelta(days=settings.refresh_token_expire_days)
    )
    session.refresh_token_hash = token_hash(refresh)
    db.commit()
    return TokenData(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_expire_minutes * 60,
    )


@router.get("/health", response_model=Envelope)
def health(db: Session = Depends(get_db), rid: str = Depends(request_id)):
    try:
        db.execute(select(1))
        status = "ok"
    except Exception:
        status = "degraded"
    return envelope({"status": status, "environment": settings.app_env}, rid)


@router.get("/health/live", response_model=Envelope)
def health_live(rid: str = Depends(request_id)):
    return envelope({"status": "ok"}, rid)


@router.get("/health/ready", response_model=Envelope)
def health_ready(db: Session = Depends(get_db), rid: str = Depends(request_id)):
    db.execute(select(1))
    return envelope({"status": "ready"}, rid)


@router.post("/auth/register", response_model=Envelope, status_code=201)
def register(body: RegisterRequest, request: Request, db: Session = Depends(get_db), rid: str = Depends(request_id)):
    if db.scalar(select(User).where(User.email == body.email)):
        raise AppError("VALIDATION_ERROR", "An account with this email already exists", 409)
    user = User(email=body.email, password_hash=hash_password(body.password), role="USER")
    user.profile = UserProfile()
    db.add(user)
    db.flush()
    tokens = _tokens(db, user, request.headers.get("user-agent"))
    db.add(
        AuditEvent(
            user_id=user.id,
            action="ACCOUNT_REGISTERED",
            resource_type="user",
            resource_id=user.id,
            request_id=rid,
            metadata_json={},
        )
    )
    db.commit()
    return envelope({"user": UserData.model_validate(user), "tokens": tokens}, rid)


@router.post("/auth/login", response_model=Envelope)
def login(body: LoginRequest, request: Request, db: Session = Depends(get_db), rid: str = Depends(request_id)):
    user = db.scalar(select(User).where(User.email == body.email))
    if not user or not verify_password(body.password, user.password_hash) or not user.is_active:
        raise AppError("AUTH_INVALID_CREDENTIALS", "Email or password is incorrect", 401)
    tokens = _tokens(db, user, request.headers.get("user-agent"))
    db.add(
        AuditEvent(
            user_id=user.id,
            action="LOGIN",
            resource_type="user",
            resource_id=user.id,
            request_id=rid,
            metadata_json={},
        )
    )
    db.commit()
    return envelope({"user": UserData.model_validate(user), "tokens": tokens}, rid)


@router.post("/auth/refresh", response_model=Envelope)
def refresh(body: RefreshRequest, request: Request, db: Session = Depends(get_db), rid: str = Depends(request_id)):
    from .security import decode_token

    payload = decode_token(body.refresh_token, "refresh")
    session = db.scalar(select(SessionRecord).where(SessionRecord.id == payload["sid"]))
    user = db.scalar(select(User).where(User.id == payload["sub"]))
    expires_at = session.expires_at if session and session.expires_at.tzinfo else (
        session.expires_at.replace(tzinfo=timezone.utc) if session else None
    )
    if (
        not session
        or session.revoked_at
        or session.refresh_token_hash != token_hash(body.refresh_token)
        or expires_at <= datetime.now(timezone.utc)
        or not user
        or not user.is_active
    ):
        raise AppError("AUTH_UNAUTHORIZED", "The refresh token is revoked or invalid", 401)
    session.revoked_at = datetime.now(timezone.utc)
    session.last_used_at = datetime.now(timezone.utc)
    tokens = _tokens(db, user, request.headers.get("user-agent"))
    db.add(
        AuditEvent(
            user_id=user.id,
            action="REFRESH_ROTATED",
            resource_type="session",
            resource_id=session.id,
            request_id=rid,
            metadata_json={},
        )
    )
    db.commit()
    return envelope({"user": UserData.model_validate(user), "tokens": tokens}, rid)


@router.post("/auth/logout", response_model=Envelope)
def logout(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db), rid: str = Depends(request_id)):
    auth.session.revoked_at = datetime.now(timezone.utc)
    db.add(
        AuditEvent(
            user_id=auth.user.id,
            action="LOGOUT",
            resource_type="session",
            resource_id=auth.session.id,
            request_id=rid,
            metadata_json={},
        )
    )
    db.commit()
    return envelope({"logged_out": True}, rid)


@router.post("/auth/logout-all", response_model=Envelope)
def logout_all(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db), rid: str = Depends(request_id)):
    now = datetime.now(timezone.utc)
    db.execute(
        update(SessionRecord)
        .where(SessionRecord.user_id == auth.user.id, SessionRecord.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    db.add(
        AuditEvent(
            user_id=auth.user.id,
            action="LOGOUT_ALL",
            resource_type="user",
            resource_id=auth.user.id,
            request_id=rid,
            metadata_json={},
        )
    )
    db.commit()
    return envelope({"logged_out": True}, rid)


@router.get("/auth/sessions", response_model=Envelope)
def sessions(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db), rid: str = Depends(request_id)):
    records = db.scalars(
        select(SessionRecord).where(SessionRecord.user_id == auth.user.id).order_by(SessionRecord.created_at.desc())
    ).all()
    data = [
        SessionData(
            id=item.id,
            created_at=item.created_at,
            expires_at=item.expires_at,
            last_used_at=item.last_used_at,
            revoked=item.revoked_at is not None,
        )
        for item in records
    ]
    return envelope(data, rid)


@router.get("/users/me", response_model=Envelope)
def me(auth: AuthContext = Depends(current_auth), rid: str = Depends(request_id)):
    return envelope(
        MeData(user=UserData.model_validate(auth.user), profile=ProfileData.model_validate(auth.user.profile)),
        rid,
    )


@router.patch("/users/me/profile", response_model=Envelope)
def update_profile(
    body: ProfileUpdate,
    auth: AuthContext = Depends(current_auth),
    db: Session = Depends(get_db),
    rid: str = Depends(request_id),
):
    profile = auth.user.profile
    profile.display_name = body.display_name
    profile.timezone = body.timezone
    db.add(profile)
    db.add(
        AuditEvent(
            user_id=auth.user.id,
            action="PROFILE_UPDATED",
            resource_type="profile",
            resource_id=profile.id,
            request_id=rid,
            metadata_json={},
        )
    )
    db.commit()
    return envelope(ProfileData.model_validate(profile), rid)


@router.post("/users/me/password", response_model=Envelope)
def change_password(
    body: PasswordChange,
    auth: AuthContext = Depends(current_auth),
    db: Session = Depends(get_db),
    rid: str = Depends(request_id),
):
    if not verify_password(body.current_password, auth.user.password_hash):
        raise AppError("AUTH_INVALID_CREDENTIALS", "Current password is incorrect", 401)
    auth.user.password_hash = hash_password(body.new_password)
    now = datetime.now(timezone.utc)
    db.execute(update(SessionRecord).where(SessionRecord.user_id == auth.user.id).values(revoked_at=now))
    db.add(
        AuditEvent(
            user_id=auth.user.id,
            action="PASSWORD_CHANGED",
            resource_type="user",
            resource_id=auth.user.id,
            request_id=rid,
            metadata_json={},
        )
    )
    db.commit()
    return envelope({"changed": True}, rid)


@router.delete("/users/me", response_model=Envelope)
def delete_account(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db), rid: str = Depends(request_id)):
    analyses = db.scalars(select(Analysis).where(Analysis.user_id == auth.user.id)).all()
    storage = LocalStorageProvider()
    for analysis in analyses:
        if analysis.image:
            storage.delete(analysis.image.storage_key)
    db.delete(auth.user)
    db.commit()
    return envelope({"deleted": True}, rid)


@router.post("/analyses", response_model=Envelope, status_code=201)
async def create_analysis(
    request: Request,
    image: UploadFile = File(...),
    auth: AuthContext = Depends(current_auth),
    db: Session = Depends(get_db),
    rid: str = Depends(request_id),
):
    content = await image.read(settings.max_upload_size_mb * 1024 * 1024 + 1)
    service = VisionAnalysisService(db, LocalStorageProvider(), get_ai_provider())
    analysis = service.create(auth.user.id, rid, content, image.filename or "image", image.content_type or "")
    return envelope(_analysis_data(analysis), rid)


@router.get("/analyses", response_model=Envelope)
def list_analyses(
    limit: int = 20,
    offset: int = 0,
    auth: AuthContext = Depends(current_auth),
    db: Session = Depends(get_db),
    rid: str = Depends(request_id),
):
    limit = max(1, min(limit, 100))
    offset = max(0, offset)
    records = db.scalars(
        select(Analysis)
        .options(joinedload(Analysis.result))
        .where(Analysis.user_id == auth.user.id)
        .order_by(Analysis.created_at.desc())
        .offset(offset)
        .limit(limit)
    ).unique().all()
    return envelope([_analysis_data(item) for item in records], rid)


@router.get("/analyses/{analysis_id}", response_model=Envelope)
def get_analysis(analysis_id: str, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db), rid: str = Depends(request_id)):
    analysis = db.scalar(
        select(Analysis).options(joinedload(Analysis.result)).where(Analysis.id == analysis_id)
    )
    if not analysis:
        raise AppError("RESOURCE_NOT_FOUND", "Analysis not found", 404)
    if analysis.user_id != auth.user.id:
        raise AppError("RESOURCE_FORBIDDEN", "You cannot access this analysis", 403)
    return envelope(_analysis_data(analysis), rid)


@router.get("/analyses/{analysis_id}/image")
def get_analysis_image(
    analysis_id: str, auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db)
):
    analysis = db.scalar(select(Analysis).options(joinedload(Analysis.image)).where(Analysis.id == analysis_id))
    if not analysis:
        raise AppError("RESOURCE_NOT_FOUND", "Analysis not found", 404)
    if analysis.user_id != auth.user.id:
        raise AppError("RESOURCE_FORBIDDEN", "You cannot access this image", 403)
    path = LocalStorageProvider().open(analysis.image.storage_key)
    return FileResponse(path, media_type=analysis.image.mime_type, filename=analysis.image.original_filename)


@router.delete("/analyses/{analysis_id}", response_model=Envelope)
def delete_analysis(
    analysis_id: str,
    auth: AuthContext = Depends(current_auth),
    db: Session = Depends(get_db),
    rid: str = Depends(request_id),
):
    analysis = db.scalar(select(Analysis).options(joinedload(Analysis.image)).where(Analysis.id == analysis_id))
    if not analysis:
        raise AppError("RESOURCE_NOT_FOUND", "Analysis not found", 404)
    if analysis.user_id != auth.user.id:
        raise AppError("RESOURCE_FORBIDDEN", "You cannot delete this analysis", 403)
    VisionAnalysisService(db, LocalStorageProvider(), get_ai_provider()).delete(analysis, auth.user.id, rid)
    return envelope({"deleted": True}, rid)


@router.post("/metrics", response_model=Envelope, status_code=201)
def create_metric(
    body: HealthMetricCreate,
    auth: AuthContext = Depends(current_auth),
    db: Session = Depends(get_db),
    rid: str = Depends(request_id),
):
    metric = HealthMetric(user_id=auth.user.id, **body.model_dump())
    db.add(metric)
    db.add(
        AuditEvent(
            user_id=auth.user.id,
            action="METRIC_CREATED",
            resource_type="health_metric",
            resource_id=metric.id,
            request_id=rid,
            metadata_json={"source": body.source},
        )
    )
    db.commit()
    db.refresh(metric)
    return envelope(HealthMetricData.model_validate(metric), rid)


@router.get("/metrics", response_model=Envelope)
def list_metrics(auth: AuthContext = Depends(current_auth), db: Session = Depends(get_db), rid: str = Depends(request_id)):
    records = db.scalars(
        select(HealthMetric)
        .where(HealthMetric.user_id == auth.user.id)
        .order_by(HealthMetric.measured_at.desc())
        .limit(100)
    ).all()
    return envelope([HealthMetricData.model_validate(item) for item in records], rid)


@router.get("/admin/audit-events", response_model=Envelope)
def audit_events(auth: AuthContext = Depends(admin_auth), db: Session = Depends(get_db), rid: str = Depends(request_id)):
    events = db.scalars(select(AuditEvent).order_by(AuditEvent.created_at.desc()).limit(100)).all()
    return envelope(
        [
            {
                "id": event.id,
                "user_id": event.user_id,
                "action": event.action,
                "resource_type": event.resource_type,
                "resource_id": event.resource_id,
                "request_id": event.request_id,
                "created_at": event.created_at,
            }
            for event in events
        ],
        rid,
    )


def _analysis_data(analysis: Analysis) -> AnalysisData:
    result = None
    if analysis.result:
        result = AIAnalysisResponse(
            provider=analysis.result.provider,
            model=analysis.result.model,
            observation=analysis.result.observation,
            result_status=analysis.result.result_status,
            confidence=analysis.result.confidence,
            warnings=analysis.result.warnings,
            limitations=analysis.result.limitations,
            request_id=analysis.result.request_id,
        )
    return AnalysisData(
        id=analysis.id,
        status=analysis.status,
        created_at=analysis.created_at,
        updated_at=analysis.updated_at,
        result=result,
    )