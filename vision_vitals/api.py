from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, File, Form, Header, Request, UploadFile
from fastapi.responses import FileResponse
from starlette.concurrency import run_in_threadpool
from sqlalchemy import select, update
from sqlalchemy.orm import Session, joinedload

from .ai import get_ai_provider
from .config import settings
from .db import get_db
from .dependencies import (
    AuthContext,
    DeviceAuthContext,
    admin_auth,
    current_auth,
    current_device_auth,
    request_id,
)
from .errors import AppError
from .models import (
    Analysis,
    AuditEvent,
    Device,
    DeviceCapture,
    DeviceSession,
    HealthMetric,
    SensorReading,
    SessionRecord,
    User,
    UserProfile,
)
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
    DeleteAccountRequest,
    DeviceAuthenticateRequest,
    DeviceCaptureData,
    DeviceData,
    DeviceHeartbeatRequest,
    DeviceRegisterData,
    DeviceRegisterRequest,
    DeviceSessionData,
    SessionData,
    SensorReadingCreate,
    SensorReadingData,
    TokenData,
    UserData,
)
from .security import (
    create_token,
    generate_device_secret,
    generate_device_session_token,
    hash_password,
    token_hash,
    verify_password,
    verify_device_secret,
)
from .quality import ImageQualityService
from .rate_limit import device_rate_limiter
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
def delete_account(
    body: DeleteAccountRequest,
    auth: AuthContext = Depends(current_auth),
    db: Session = Depends(get_db),
    rid: str = Depends(request_id),
):
    if not verify_password(body.current_password, auth.user.password_hash):
        raise AppError("AUTH_INVALID_CREDENTIALS", "Current password is incorrect", 401)
    analyses = db.scalars(select(Analysis).where(Analysis.user_id == auth.user.id)).all()
    storage = LocalStorageProvider()
    for analysis in analyses:
        if analysis.image:
            storage.delete(analysis.image.storage_key)
    db.add(
        AuditEvent(
            user_id=auth.user.id,
            action="ACCOUNT_DELETED",
            resource_type="user",
            resource_id=auth.user.id,
            request_id=rid,
            metadata_json={},
        )
    )
    db.delete(auth.user)
    db.commit()
    return envelope({"deleted": True}, rid)


def _device_status(device: Device) -> str:
    if device.status == "REVOKED":
        return "REVOKED"
    if not device.last_seen_at:
        return "REGISTERED"
    last_seen = (
        device.last_seen_at
        if device.last_seen_at.tzinfo
        else device.last_seen_at.replace(tzinfo=timezone.utc)
    )
    if (datetime.now(timezone.utc) - last_seen).total_seconds() <= settings.device_heartbeat_timeout_seconds:
        return "ONLINE"
    return "OFFLINE"


def _device_data(device: Device) -> DeviceData:
    return DeviceData(
        id=device.id,
        device_identifier=device.device_identifier,
        device_name=device.device_name,
        device_type=device.device_type,
        status=_device_status(device),
        firmware_version=device.firmware_version,
        software_version=device.software_version,
        last_seen_at=device.last_seen_at,
        created_at=device.created_at,
        updated_at=device.updated_at,
    )


def _capture_data(capture: DeviceCapture) -> DeviceCaptureData:
    return DeviceCaptureData(
        id=capture.id,
        device_id=capture.device_id,
        user_id=capture.user_id,
        analysis_id=capture.analysis_id,
        capture_type=capture.capture_type,
        status=capture.status,
        idempotency_key=capture.idempotency_key,
        created_at=capture.created_at,
        completed_at=capture.completed_at,
    )


@router.post("/devices/register", response_model=Envelope, status_code=201)
def register_device(
    body: DeviceRegisterRequest,
    auth: AuthContext = Depends(current_auth),
    db: Session = Depends(get_db),
    rid: str = Depends(request_id),
):
    device_rate_limiter.check(
        f"register:{auth.user.id}", settings.device_registration_rate_limit
    )
    existing = db.scalar(
        select(Device).where(
            Device.owner_user_id == auth.user.id,
            Device.device_identifier == body.device_identifier,
        )
    )
    if existing:
        raise AppError("DEVICE_EXISTS", "A device with this identifier is already registered", 409)
    secret = generate_device_secret()
    device = Device(
        owner_user_id=auth.user.id,
        device_identifier=body.device_identifier,
        device_name=body.device_name,
        device_type=body.device_type,
        firmware_version=body.firmware_version,
        software_version=body.software_version,
        credential_hash=token_hash(secret),
    )
    db.add(device)
    db.flush()
    db.add(
        AuditEvent(
            user_id=auth.user.id,
            action="DEVICE_REGISTERED",
            resource_type="device",
            resource_id=device.id,
            request_id=rid,
            metadata_json={"device_type": device.device_type},
        )
    )
    db.commit()
    db.refresh(device)
    data = DeviceRegisterData(**_device_data(device).model_dump(), device_secret=secret)
    return envelope(data, rid)


@router.get("/devices", response_model=Envelope)
def list_devices(
    auth: AuthContext = Depends(current_auth),
    db: Session = Depends(get_db),
    rid: str = Depends(request_id),
):
    devices = db.scalars(
        select(Device).where(Device.owner_user_id == auth.user.id).order_by(Device.created_at.desc())
    ).all()
    return envelope([_device_data(device) for device in devices], rid)


def _owned_device(device_id: str, auth: AuthContext, db: Session) -> Device:
    device = db.scalar(
        select(Device).where(Device.id == device_id, Device.owner_user_id == auth.user.id)
    )
    if not device:
        raise AppError("RESOURCE_NOT_FOUND", "Device not found", 404)
    return device


@router.get("/devices/{device_id}", response_model=Envelope)
def get_device(
    device_id: str,
    auth: AuthContext = Depends(current_auth),
    db: Session = Depends(get_db),
    rid: str = Depends(request_id),
):
    return envelope(_device_data(_owned_device(device_id, auth, db)), rid)


@router.delete("/devices/{device_id}", response_model=Envelope)
def revoke_device(
    device_id: str,
    auth: AuthContext = Depends(current_auth),
    db: Session = Depends(get_db),
    rid: str = Depends(request_id),
):
    device = _owned_device(device_id, auth, db)
    device.status = "REVOKED"
    now = datetime.now(timezone.utc)
    db.execute(
        update(DeviceSession)
        .where(DeviceSession.device_id == device.id, DeviceSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    db.add(
        AuditEvent(
            user_id=auth.user.id,
            action="DEVICE_REVOKED",
            resource_type="device",
            resource_id=device.id,
            request_id=rid,
            metadata_json={},
        )
    )
    db.commit()
    return envelope({"revoked": True}, rid)


@router.post("/devices/{device_id}/rotate-credential", response_model=Envelope)
def rotate_device_credential(
    device_id: str,
    auth: AuthContext = Depends(current_auth),
    db: Session = Depends(get_db),
    rid: str = Depends(request_id),
):
    device_rate_limiter.check(
        f"rotate:{auth.user.id}", settings.device_registration_rate_limit
    )
    device = _owned_device(device_id, auth, db)
    if device.status == "REVOKED":
        raise AppError("DEVICE_REVOKED", "The device has been revoked", 409)
    secret = generate_device_secret()
    device.credential_hash = token_hash(secret)
    now = datetime.now(timezone.utc)
    db.execute(
        update(DeviceSession)
        .where(DeviceSession.device_id == device.id, DeviceSession.revoked_at.is_(None))
        .values(revoked_at=now)
    )
    db.add(
        AuditEvent(
            user_id=auth.user.id,
            action="DEVICE_CREDENTIAL_ROTATED",
            resource_type="device",
            resource_id=device.id,
            request_id=rid,
            metadata_json={},
        )
    )
    db.commit()
    return envelope({"device_id": device.id, "device_secret": secret}, rid)


@router.post("/devices/{device_id}/authenticate", response_model=Envelope)
def authenticate_device(
    device_id: str,
    body: DeviceAuthenticateRequest,
    request: Request,
    db: Session = Depends(get_db),
    rid: str = Depends(request_id),
):
    device_rate_limiter.check(
        f"auth:{request.client.host if request.client else 'unknown'}:{device_id}",
        settings.device_auth_rate_limit,
    )
    device = db.scalar(select(Device).where(Device.id == device_id))
    if not device or device.status == "REVOKED" or not verify_device_secret(
        body.device_secret, device.credential_hash if device else ""
    ):
        db.add(
            AuditEvent(
                user_id=device.owner_user_id if device else None,
                action="DEVICE_AUTHENTICATION_FAILED",
                resource_type="device",
                resource_id=device_id,
                request_id=rid,
                metadata_json={},
            )
        )
        db.commit()
        raise AppError("DEVICE_INVALID_CREDENTIALS", "Device credentials are invalid", 401)
    session_token = generate_device_session_token()
    now = datetime.now(timezone.utc)
    session = DeviceSession(
        device_id=device.id,
        token_hash=token_hash(session_token),
        expires_at=now + timedelta(minutes=settings.device_session_expire_minutes),
        last_seen_at=now,
    )
    device.last_seen_at = now
    db.add(session)
    db.add(
        AuditEvent(
            user_id=device.owner_user_id,
            action="DEVICE_AUTHENTICATED",
            resource_type="device_session",
            resource_id=session.id,
            request_id=rid,
            metadata_json={"device_id": device.id},
        )
    )
    db.commit()
    db.refresh(session)
    return envelope(
        DeviceSessionData(
            device_id=device.id,
            session_identifier=session.id,
            device_session_token=session_token,
            created_at=session.created_at,
            expires_at=session.expires_at,
        ),
        rid,
    )


@router.post("/devices/{device_id}/heartbeat", response_model=Envelope)
def device_heartbeat(
    device_id: str,
    body: DeviceHeartbeatRequest,
    device_auth: DeviceAuthContext = Depends(current_device_auth),
    db: Session = Depends(get_db),
    rid: str = Depends(request_id),
):
    device_rate_limiter.check(f"heartbeat:{device_id}", settings.device_heartbeat_rate_limit)
    now = datetime.now(timezone.utc)
    device_auth.device.last_seen_at = now
    device_auth.session.last_seen_at = now
    if body.software_version is not None:
        device_auth.device.software_version = body.software_version
    if body.firmware_version is not None:
        device_auth.device.firmware_version = body.firmware_version
    db.commit()
    return envelope(_device_data(device_auth.device), rid)


@router.get("/devices/{device_id}/status", response_model=Envelope)
def device_status(
    device_id: str,
    device_auth: DeviceAuthContext = Depends(current_device_auth),
    rid: str = Depends(request_id),
):
    return envelope(_device_data(device_auth.device), rid)


@router.post("/devices/{device_id}/logout", response_model=Envelope)
def device_logout(
    device_id: str,
    device_auth: DeviceAuthContext = Depends(current_device_auth),
    db: Session = Depends(get_db),
    rid: str = Depends(request_id),
):
    device_auth.session.revoked_at = datetime.now(timezone.utc)
    db.add(
        AuditEvent(
            user_id=device_auth.user.id,
            action="DEVICE_SESSION_REVOKED",
            resource_type="device_session",
            resource_id=device_auth.session.id,
            request_id=rid,
            metadata_json={"device_id": device_id},
        )
    )
    db.commit()
    return envelope({"logged_out": True}, rid)


@router.post("/devices/{device_id}/capture", response_model=Envelope)
async def device_capture(
    device_id: str,
    request: Request,
    image: UploadFile = File(...),
    capture_type: str = Form(default="camera", max_length=32),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
    device_auth: DeviceAuthContext = Depends(current_device_auth),
    db: Session = Depends(get_db),
    rid: str = Depends(request_id),
):
    device_rate_limiter.check(f"capture:{device_id}", settings.device_capture_rate_limit)
    if not idempotency_key or len(idempotency_key) > 128:
        raise AppError("IDEMPOTENCY_REQUIRED", "Idempotency-Key is required", 422)
    existing = db.scalar(
        select(DeviceCapture).where(
            DeviceCapture.device_id == device_id,
            DeviceCapture.idempotency_key == idempotency_key,
        )
    )
    if existing:
        analysis = db.scalar(
            select(Analysis).options(joinedload(Analysis.result)).where(Analysis.id == existing.analysis_id)
        ) if existing.analysis_id else None
        return envelope(
            {
                "capture": _capture_data(existing),
                "analysis": _analysis_data(analysis) if analysis else None,
                "duplicate": True,
            },
            rid,
        )

    capture = DeviceCapture(
        device_id=device_id,
        user_id=device_auth.user.id,
        capture_type=capture_type,
        status="RECEIVED",
        idempotency_key=idempotency_key,
    )
    db.add(capture)
    db.flush()
    content = await image.read(settings.max_upload_size_mb * 1024 * 1024 + 1)
    capture.status = "VALIDATING"
    try:
        ImageQualityService().validate_or_raise(content, enforce_exposure=True)
        validation_storage = LocalStorageProvider(
            max_bytes=settings.max_upload_size_mb * 1024 * 1024
        )
        stored_check = validation_storage.save_image(
            content, image.filename or "capture", image.content_type or ""
        )
        validation_storage.delete(stored_check.storage_key)
    except AppError as exc:
        capture.status = "INVALID"
        db.add(
            AuditEvent(
                user_id=device_auth.user.id,
                action="DEVICE_CAPTURE_REJECTED",
                resource_type="device_capture",
                resource_id=capture.id,
                request_id=rid,
                metadata_json={"code": exc.code},
            )
        )
        db.commit()
        raise
    capture.status = "VALID"
    db.commit()
    try:
        capture.status = "PROCESSING"
        db.commit()
        analysis = await run_in_threadpool(
            VisionAnalysisService(db, LocalStorageProvider(), get_ai_provider()).create,
            device_auth.user.id,
            rid,
            content,
            image.filename or "capture",
            image.content_type or "",
            "HARDWARE_CAMERA",
        )
        capture = db.get(DeviceCapture, capture.id)
        capture.analysis_id = analysis.id
        capture.status = "COMPLETED"
        capture.completed_at = datetime.now(timezone.utc)
        db.add(
            AuditEvent(
                user_id=device_auth.user.id,
                action="DEVICE_CAPTURE_SUBMITTED",
                resource_type="device_capture",
                resource_id=capture.id,
                request_id=rid,
                metadata_json={"analysis_id": analysis.id, "device_id": device_id},
            )
        )
        db.commit()
    except Exception:
        capture = db.get(DeviceCapture, capture.id)
        if capture:
            capture.status = "FAILED"
            db.commit()
        raise
    return envelope(
        {"capture": _capture_data(capture), "analysis": _analysis_data(analysis), "duplicate": False},
        rid,
    )


@router.post("/devices/{device_id}/sensor-readings", response_model=Envelope, status_code=201)
def create_sensor_reading(
    device_id: str,
    body: SensorReadingCreate,
    device_auth: DeviceAuthContext = Depends(current_device_auth),
    db: Session = Depends(get_db),
    rid: str = Depends(request_id),
):
    device_rate_limiter.check(f"sensor:{device_id}", settings.device_sensor_rate_limit)
    if body.capture_id:
        capture = db.scalar(
            select(DeviceCapture).where(
                DeviceCapture.id == body.capture_id, DeviceCapture.device_id == device_id
            )
        )
        if not capture:
            raise AppError("SENSOR_INVALID", "The capture does not belong to this device", 422)
    reading = SensorReading(
        device_id=device_id,
        capture_id=body.capture_id,
        sensor_type=body.sensor_type,
        value=body.value,
        unit=body.unit,
        quality=body.quality,
        timestamp=body.timestamp,
    )
    db.add(reading)
    db.commit()
    db.refresh(reading)
    return envelope(SensorReadingData.model_validate(reading), rid)


@router.post("/analyses", response_model=Envelope, status_code=201)
async def create_analysis(
    request: Request,
    image: UploadFile = File(...),
    source: str = Form(default="UPLOAD"),
    auth: AuthContext = Depends(current_auth),
    db: Session = Depends(get_db),
    rid: str = Depends(request_id),
):
    if source not in {"APP_CAMERA", "UPLOAD"}:
        raise AppError("VALIDATION_ERROR", "Unsupported image source", 422)
    content = await image.read(settings.max_upload_size_mb * 1024 * 1024 + 1)
    service = VisionAnalysisService(db, LocalStorageProvider(), get_ai_provider())
    analysis = await run_in_threadpool(
        service.create,
        auth.user.id,
        rid,
        content,
        image.filename or "image",
        image.content_type or "",
        source,
    )
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
    VisionAnalysisService(db, LocalStorageProvider()).delete(analysis, auth.user.id, rid)
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