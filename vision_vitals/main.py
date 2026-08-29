from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from fastapi.responses import JSONResponse

from .api import router
from .config import settings
from .db import Base, engine
from .errors import AppError
from .models import (  # noqa: F401 - register all models before create_all
    Analysis,
    AnalysisImage,
    AnalysisResult,
    AuditEvent,
    ConsentRecord,
    HealthMetric,
    SessionRecord,
    User,
    UserProfile,
)

logger = logging.getLogger("vision_vitals")
logging.basicConfig(level=getattr(logging, settings.log_level.upper(), logging.INFO), format="%(message)s")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings.validate()
    Base.metadata.create_all(bind=engine)
    settings.storage_path.mkdir(parents=True, exist_ok=True)
    yield


app = FastAPI(
    title="Vision Vitals API",
    version="1.0.0",
    description="Secure Part 1 analysis and health-data backend. AI output is decision support, not diagnosis.",
    lifespan=lifespan,
)
if settings.trusted_hosts:
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=list(settings.trusted_hosts))
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
    )


@app.middleware("http")
async def request_context(request: Request, call_next):
    rid = request.headers.get("X-Request-ID", str(uuid.uuid4()))
    request.state.request_id = rid[:64]
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            '{"event":"request_error","request_id":"%s","method":"%s","path":"%s"}',
            request.state.request_id,
            request.method,
            request.url.path,
        )
        raise
    response.headers["X-Request-ID"] = request.state.request_id
    logger.info(
        '{"event":"request","request_id":"%s","method":"%s","path":"%s","status":%s,"latency_ms":%.2f}',
        request.state.request_id,
        request.method,
        request.url.path,
        response.status_code,
        (time.perf_counter() - started) * 1000,
    )
    return response


@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError):
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "success": False,
            "error": {"code": exc.code, "message": exc.message, "details": exc.details},
            "request_id": getattr(request.state, "request_id", str(uuid.uuid4())),
        },
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(request: Request, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={
            "success": False,
            "error": {"code": "VALIDATION_ERROR", "message": "Request validation failed"},
            "request_id": getattr(request.state, "request_id", str(uuid.uuid4())),
        },
    )


@app.exception_handler(Exception)
async def internal_error_handler(request: Request, exc: Exception):
    logger.exception("Unhandled internal error")
    return JSONResponse(
        status_code=500,
        content={
            "success": False,
            "error": {"code": "INTERNAL_ERROR", "message": "An internal error occurred"},
            "request_id": getattr(request.state, "request_id", str(uuid.uuid4())),
        },
    )


app.include_router(router)