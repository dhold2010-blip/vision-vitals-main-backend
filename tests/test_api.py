from __future__ import annotations

import io
from datetime import datetime, timezone

from PIL import Image

from tests.conftest import auth_headers, register


def test_health_and_openapi(client):
    assert client.get("/api/v1/health/live").json()["data"]["status"] == "ok"
    assert client.get("/api/v1/health/ready").status_code == 200
    spec = client.get("/openapi.json")
    assert spec.status_code == 200
    assert "/api/v1/analyses" in spec.json()["paths"]


def test_registration_login_and_invalid_credentials(client):
    data = register(client, "person@example.com")
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