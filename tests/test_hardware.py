from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

from PIL import Image

from tests.conftest import auth_headers, register
from vision_vitals.db import SessionLocal
from vision_vitals.models import DeviceSession


def image_bytes(color=(96, 120, 140), size=(64, 64), format="PNG"):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format=format)
    return output.getvalue()


def register_and_authenticate_device(client, owner):
    user_headers = auth_headers(owner)
    registered = client.post(
        "/api/v1/devices/register",
        headers=user_headers,
        json={
            "device_identifier": f"pi-{owner['user']['id']}",
            "device_name": "Test camera",
            "device_type": "VISION_VITALS_CAMERA",
        },
    )
    assert registered.status_code == 201, registered.text
    device = registered.json()["data"]
    authenticated = client.post(
        f"/api/v1/devices/{device['id']}/authenticate",
        json={"device_secret": device["device_secret"]},
    )
    assert authenticated.status_code == 200, authenticated.text
    session = authenticated.json()["data"]
    return device, {"X-Device-Session": session["device_session_token"]}


def test_device_registration_ownership_and_revocation(client):
    owner = register(client, "hardware-owner@example.com")
    other = register(client, "hardware-other@example.com")
    device, device_headers = register_and_authenticate_device(client, owner)

    assert client.get(
        f"/api/v1/devices/{device['id']}", headers=auth_headers(other)
    ).status_code == 404
    assert client.post(
        f"/api/v1/devices/{device['id']}/heartbeat",
        headers=device_headers,
        json={"software_version": "0.2.0"},
    ).status_code == 200
    assert client.delete(
        f"/api/v1/devices/{device['id']}", headers=auth_headers(owner)
    ).status_code == 200
    assert client.get(
        f"/api/v1/devices/{device['id']}/status", headers=device_headers
    ).status_code == 401


def test_capture_uses_unified_pipeline_and_is_idempotent(client):
    owner = register(client, "capture-owner@example.com")
    device, headers = register_and_authenticate_device(client, owner)
    request_headers = {**headers, "Idempotency-Key": "capture-test-1"}
    files = {"image": ("capture.png", image_bytes(), "image/png")}

    first = client.post(
        f"/api/v1/devices/{device['id']}/capture",
        headers=request_headers,
        files=files,
        data={"capture_type": "camera"},
    )
    assert first.status_code == 200, first.text
    assert first.json()["data"]["capture"]["status"] == "COMPLETED"
    assert first.json()["data"]["analysis"]["result"]["result_status"] == "UNAVAILABLE"

    duplicate = client.post(
        f"/api/v1/devices/{device['id']}/capture",
        headers=request_headers,
        files=files,
        data={"capture_type": "camera"},
    )
    assert duplicate.status_code == 200
    assert duplicate.json()["data"]["duplicate"] is True
    assert duplicate.json()["data"]["capture"]["id"] == first.json()["data"]["capture"]["id"]


def test_capture_rejects_corrupt_or_unsafe_images(client):
    owner = register(client, "capture-validation@example.com")
    device, headers = register_and_authenticate_device(client, owner)
    response = client.post(
        f"/api/v1/devices/{device['id']}/capture",
        headers={**headers, "Idempotency-Key": "invalid-image-1"},
        files={"image": ("capture.png", b"not-an-image", "image/png")},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UPLOAD_INVALID"

    too_bright = client.post(
        f"/api/v1/devices/{device['id']}/capture",
        headers={**headers, "Idempotency-Key": "bright-image-1"},
        files={"image": ("capture.png", image_bytes((255, 255, 255)), "image/png")},
    )
    assert too_bright.status_code == 422
    assert too_bright.json()["error"]["code"] == "IMAGE_RECAPTURE_REQUIRED"


def test_sensor_validation_and_capture_ownership(client):
    owner = register(client, "sensor-owner@example.com")
    device, headers = register_and_authenticate_device(client, owner)
    timestamp = datetime.now(timezone.utc).isoformat()
    valid = client.post(
        f"/api/v1/devices/{device['id']}/sensor-readings",
        headers=headers,
        json={"sensor_type": "distance", "value": 350, "unit": "mm", "timestamp": timestamp},
    )
    assert valid.status_code == 201
    assert valid.json()["data"]["unit"] == "mm"

    invalid = client.post(
        f"/api/v1/devices/{device['id']}/sensor-readings",
        headers=headers,
        json={"sensor_type": "distance", "value": 2501, "unit": "mm", "timestamp": timestamp},
    )
    assert invalid.status_code == 422
    assert invalid.json()["error"]["code"] == "VALIDATION_ERROR"


def test_expired_device_session_is_rejected(client):
    owner = register(client, "expired-device@example.com")
    device, headers = register_and_authenticate_device(client, owner)
    with SessionLocal() as db:
        session = db.query(DeviceSession).filter(DeviceSession.device_id == device["id"]).one()
        session.expires_at = datetime.now(timezone.utc) - timedelta(minutes=1)
        db.commit()
    response = client.post(
        f"/api/v1/devices/{device['id']}/heartbeat", headers=headers, json={}
    )
    assert response.status_code == 401