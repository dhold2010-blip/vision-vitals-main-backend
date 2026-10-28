from __future__ import annotations

import io
from datetime import datetime, timezone

from PIL import Image

from tests.conftest import auth_headers, register
from vision_vitals.db import SessionLocal
from vision_vitals.models import AnalysisImage


def test_health_and_openapi(client):
    assert client.get("/api/v1/health/live").json()["data"]["status"] == "ok"
    assert client.get("/api/v1/health/ready").status_code == 200
    spec = client.get("/openapi.json")
    assert spec.status_code == 200
    assert "/api/v1/analyses" in spec.json()["paths"]
    assert "/api/v1/devices/authenticate" in spec.json()["paths"]


def test_registration_login_and_invalid_credentials(client):
    response = client.post(
        "/api/v1/auth/register",
        json={
            "email": "person@example.com",
            "password": "correct horse battery staple",
            "role": "ADMIN",
        },
    )
    assert response.status_code == 201
    data = response.json()["data"]
    assert data["user"]["role"] == "USER"
    response = client.post(
        "/api/v1/auth/login",
        json={"email": "person@example.com", "password": "wrong password here"},
    )
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "AUTH_INVALID_CREDENTIALS"


def test_refresh_rotation_and_revocation(client):
    data = register(client, "rotate@example.com")
    old_refresh = data["tokens"]["refresh_token"]
    response = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert response.status_code == 200
    new_refresh = response.json()["data"]["tokens"]["refresh_token"]
    assert new_refresh != old_refresh
    replay = client.post("/api/v1/auth/refresh", json={"refresh_token": old_refresh})
    assert replay.status_code == 401
    assert replay.json()["error"]["code"] == "AUTH_UNAUTHORIZED"


def test_logout_all_invalidates_access(client):
    data = register(client, "logout@example.com")
    headers = auth_headers(data)
    assert client.post("/api/v1/auth/logout-all", headers=headers).status_code == 200
    response = client.get("/api/v1/users/me", headers=headers)
    assert response.status_code == 401


def test_sensitive_account_deletion_requires_reauthentication(client):
    data = register(client, "delete@example.com")
    headers = auth_headers(data)
    denied = client.request(
        "DELETE",
        "/api/v1/users/me",
        headers=headers,
        json={"current_password": "wrong password"},
    )
    assert denied.status_code == 401
    deleted = client.request(
        "DELETE",
        "/api/v1/users/me",
        headers=headers,
        json={"current_password": "correct horse battery staple"},
    )
    assert deleted.status_code == 200
    assert client.get("/api/v1/users/me", headers=headers).status_code == 401


def test_analysis_upload_and_idor_protection(client):
    owner = register(client, "owner@example.com")
    other = register(client, "other@example.com")
    image = Image.new("RGB", (8, 8), "white")
    content = io.BytesIO()
    image.save(content, format="PNG")
    response = client.post(
        "/api/v1/analyses",
        headers=auth_headers(owner),
        files={"image": ("safe.png", content.getvalue(), "image/png")},
    )
    assert response.status_code == 201, response.text
    analysis_id = response.json()["data"]["id"]
    assert response.json()["data"]["result"]["result_status"] == "UNAVAILABLE"
    forbidden = client.get(f"/api/v1/analyses/{analysis_id}", headers=auth_headers(other))
    assert forbidden.status_code == 403
    assert client.get(
        f"/api/v1/analyses/{analysis_id}/image", headers=auth_headers(other)
    ).status_code == 403
    assert client.get(f"/api/v1/analyses/{analysis_id}", headers=auth_headers(owner)).status_code == 200


def test_upload_validation_and_metric_sources(client):
    data = register(client, "metric@example.com")
    headers = auth_headers(data)
    invalid = client.post(
        "/api/v1/analyses",
        headers=headers,
        files={"image": ("not.png", b"not-an-image", "image/png")},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "UPLOAD_INVALID"
    metric = client.post(
        "/api/v1/metrics",
        headers=headers,
        json={
            "metric_type": "heart_rate",
            "source": "USER_PROVIDED",
            "value": 72,
            "unit": "bpm",
            "availability": "AVAILABLE",
            "measured_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert metric.status_code == 201
    assert client.get("/api/v1/metrics", headers=headers).json()["data"][0]["source"] == "USER_PROVIDED"
    unavailable = client.post(
        "/api/v1/metrics",
        headers=headers,
        json={
            "metric_type": "blood_pressure",
            "source": "AI_INFERRED",
            "value": None,
            "availability": "UNAVAILABLE",
            "measured_at": datetime.now(timezone.utc).isoformat(),
        },
    )
    assert unavailable.status_code == 201


def test_app_camera_and_upload_keep_their_source_labels(client):
    data = register(client, "analysis-sources@example.com")
    headers = auth_headers(data)
    for source in ("APP_CAMERA", "UPLOAD"):
        image = io.BytesIO()
        Image.new("RGB", (64, 64), (96, 120, 140)).save(image, format="JPEG")
        response = client.post(
            "/api/v1/analyses",
            headers=headers,
            data={"source": source},
            files={"image": (f"{source.lower()}.jpg", image.getvalue(), "image/jpeg")},
        )
        assert response.status_code == 201, response.text
        with SessionLocal() as db:
            stored = db.query(AnalysisImage).filter(
                AnalysisImage.analysis_id == response.json()["data"]["id"]
            ).one()
            assert stored.source == source


def test_phone_scan_requires_user_but_no_device_and_preserves_ownership(client, monkeypatch):
    from dataclasses import replace

    from vision_vitals import dependencies
    from vision_vitals.models import Device, DeviceSession

    # Phone scans remain usable when the separate hardware feature is disabled.
    monkeypatch.setattr(
        dependencies, "settings", replace(dependencies.settings, device_auth_enabled=False)
    )
    owner = register(client, "phone-owner@example.com")
    other = register(client, "phone-other@example.com")
    content = io.BytesIO()
    Image.new("RGB", (64, 64), (96, 120, 140)).save(content, format="JPEG")
    upload = {"image": ("phone.jpg", content.getvalue(), "image/jpeg")}
    unauthorized = client.post(
        "/api/v1/analyses", data={"source": "APP_CAMERA"}, files=upload
    )
    assert unauthorized.status_code == 401
    assert unauthorized.json()["error"]["code"] == "AUTH_UNAUTHORIZED"
    response = client.post(
        "/api/v1/analyses", headers=auth_headers(owner),
        data={"source": "APP_CAMERA"}, files=upload,
    )
    assert response.status_code == 201, response.text
    analysis = response.json()["data"]
    assert analysis["status"] == "COMPLETED"
    assert analysis["result"]["observation"]
    assert "analysis" not in analysis  # The frontend consumes the analysis directly.
    assert client.get(
        f"/api/v1/analyses/{analysis['id']}", headers=auth_headers(other)
    ).status_code == 403
    with SessionLocal() as db:
        assert db.query(Device).count() == 0
        assert db.query(DeviceSession).count() == 0


def test_phone_scan_rate_limit_is_per_user(client, monkeypatch):
    from dataclasses import replace

    from vision_vitals import api

    monkeypatch.setattr(api, "settings", replace(api.settings, app_capture_rate_limit=1))
    owner = register(client, "phone-limit@example.com")
    other = register(client, "phone-limit-other@example.com")
    content = io.BytesIO()
    Image.new("RGB", (64, 64), (96, 120, 140)).save(content, format="JPEG")

    def scan(user):
        return client.post(
            "/api/v1/analyses", headers=auth_headers(user), data={"source": "APP_CAMERA"},
            files={"image": ("phone.jpg", content.getvalue(), "image/jpeg")},
        )

    assert scan(owner).status_code == 201
    limited = scan(owner)
    assert limited.status_code == 429
    assert limited.json()["error"]["code"] == "RATE_LIMITED"
    assert scan(other).status_code == 201


def test_phone_scan_errors_keep_their_code_and_request_id(client, monkeypatch):
    from dataclasses import replace

    from vision_vitals import api

    monkeypatch.setattr(api, "settings", replace(api.settings, max_upload_size_mb=1))
    owner = register(client, "phone-errors@example.com")
    headers = {**auth_headers(owner), "X-Request-ID": "phone-error-review"}
    tiny = io.BytesIO()
    Image.new("RGB", (1, 1)).save(tiny, format="JPEG")
    for content, status, code in (
        (tiny.getvalue(), 422, "IMAGE_RECAPTURE_REQUIRED"),
        (b"not an image", 422, "UPLOAD_INVALID"),
        (b"x" * (1024 * 1024 + 1), 413, "UPLOAD_TOO_LARGE"),
    ):
        response = client.post(
            "/api/v1/analyses", headers=headers, data={"source": "APP_CAMERA"},
            files={"image": ("phone.jpg", content, "image/jpeg")},
        )
        assert response.status_code == status, response.text
        assert response.json()["error"]["code"] == code
        assert response.json()["request_id"] == "phone-error-review"


def test_visual_assessment_survives_save_and_read(client, monkeypatch):
    from vision_vitals.schemas import AIAnalysisResponse, VisualAssessment

    visual = VisualAssessment(
        subject_visible=True, framing="Head and shoulders visible.", lighting="Light from the left.",
        posture="The head is tilted to one side.", capture_tips=["Include your full body."],
    )

    class PhotoProvider:
        def analyze(self, request, image):
            return AIAnalysisResponse(
                provider="test", model="visual-report", observation="A seated person is visible.",
                result_status="OBSERVATION", request_id=request.request_id, visual_assessment=visual,
            )

    monkeypatch.setattr("vision_vitals.api.get_ai_provider", PhotoProvider)
    owner = register(client, "visual-report@example.com")
    content = io.BytesIO()
    Image.new("RGB", (64, 64), (100, 120, 150)).save(content, format="JPEG")
    response = client.post(
        "/api/v1/analyses", headers=auth_headers(owner), data={"source": "APP_CAMERA"},
        files={"image": ("photo.jpg", content.getvalue(), "image/jpeg")},
    )
    assert response.status_code == 201, response.text
    analysis = response.json()["data"]
    assert analysis["result"]["visual_assessment"] == visual.model_dump()
    saved = client.get(f"/api/v1/analyses/{analysis['id']}", headers=auth_headers(owner))
    assert saved.status_code == 200
    assert saved.json()["data"]["result"] == analysis["result"]
