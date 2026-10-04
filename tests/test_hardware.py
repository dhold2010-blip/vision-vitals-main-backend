from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

from PIL import Image

from tests.conftest import auth_headers, register
from vision_vitals.db import SessionLocal
from vision_vitals.models import AnalysisImage, AuditEvent, DeviceCapture, DeviceSession


def image_bytes(color=(96, 120, 140), size=(64, 64), format="JPEG"):
    output = io.BytesIO()
    Image.new("RGB", size, color).save(output, format=format)
    return output.getvalue()


def register_and_authenticate_device(client, owner):
    user_headers = auth_headers(owner)
    registered = client.post(
        "/api/v1/devices/register",
        headers=user_headers,
        json={
            "device_identifier": f"esp32-{owner['user']['id']}",
            "device_name": "Test camera",
            "device_type": "VISION_VITALS_CAMERA",
        },
    )
    assert registered.status_code == 201, registered.text
    device = registered.json()["data"]
    authenticated = client.post(
        "/api/v1/devices/authenticate",
        json={"device_id": device["id"], "device_secret": device["device_secret"]},
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
    revoked_capture = client.post(
        f"/api/v1/devices/{device['id']}/capture",
        headers={**device_headers, "Idempotency-Key": "revoked-capture"},
        files={"image": ("capture.jpg", image_bytes(), "image/jpeg")},
    )
    assert revoked_capture.status_code == 401


def test_capture_uses_unified_pipeline_and_is_idempotent(client):
    owner = register(client, "capture-owner@example.com")
    device, headers = register_and_authenticate_device(client, owner)
    request_headers = {**headers, "Idempotency-Key": "capture-test-1"}
    files = {"image": ("capture.jpg", image_bytes(), "image/jpeg")}

    first = client.post(
        f"/api/v1/devices/{device['id']}/capture",
        headers=request_headers,
        files=files,
        data={"capture_type": "camera"},
    )
    assert first.status_code == 200, first.text
    assert first.json()["data"]["capture"]["status"] == "COMPLETED"
    assert first.json()["data"]["capture"]["capture_metadata"] == {
        "image_source": "HARDWARE_CAMERA",
        "camera_type": "ESP32_CAM",
        "image_width": 64,
        "image_height": 64,
    }
    assert first.json()["data"]["analysis"]["result"]["result_status"] == "UNAVAILABLE"
    with SessionLocal() as db:
        image = db.query(AnalysisImage).filter(
            AnalysisImage.analysis_id == first.json()["data"]["analysis"]["id"]
        ).one()
        assert image.source == "HARDWARE_CAMERA"
        capture = db.query(DeviceCapture).filter(
            DeviceCapture.id == first.json()["data"]["capture"]["id"]
        ).one()
        assert capture.status == "COMPLETED"
        transitions = {
            event.action
            for event in db.query(AuditEvent).filter(AuditEvent.resource_id == capture.id).all()
        }
        assert {
            "DEVICE_CAPTURE_RECEIVED",
            "DEVICE_CAPTURE_VALIDATING",
            "DEVICE_CAPTURE_VALID",
            "DEVICE_CAPTURE_PROCESSING",
            "DEVICE_CAPTURE_COMPLETED",
        }.issubset(transitions)

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
        files={"image": ("capture.jpg", b"not-an-image", "image/jpeg")},
    )
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "UPLOAD_INVALID"

    too_bright = client.post(
        f"/api/v1/devices/{device['id']}/capture",
        headers={**headers, "Idempotency-Key": "bright-image-1"},
        files={"image": ("capture.jpg", image_bytes((255, 255, 255)), "image/jpeg")},
    )
    assert too_bright.status_code == 422
    assert too_bright.json()["error"]["code"] == "IMAGE_RECAPTURE_REQUIRED"

    png = io.BytesIO()
    Image.new("RGB", (64, 64), (96, 120, 140)).save(png, format="PNG")
    unsupported = client.post(
        f"/api/v1/devices/{device['id']}/capture",
        headers={**headers, "Idempotency-Key": "png-image-1"},
        files={"image": ("capture.png", png.getvalue(), "image/png")},
    )
    assert unsupported.status_code == 422
    assert unsupported.json()["error"]["code"] == "UPLOAD_INVALID"

    panorama = io.BytesIO()
    Image.new("RGB", (256, 64), (96, 120, 140)).save(panorama, format="JPEG")
    bad_framing = client.post(
        f"/api/v1/devices/{device['id']}/capture",
        headers={**headers, "Idempotency-Key": "panorama-image-1"},
        files={"image": ("capture.jpg", panorama.getvalue(), "image/jpeg")},
    )
    assert bad_framing.status_code == 422
    assert bad_framing.json()["error"]["code"] == "IMAGE_RECAPTURE_REQUIRED"


def test_capture_metadata_is_validated_and_dimensions_are_server_derived(client):
    owner = register(client, "capture-metadata@example.com")
    device, headers = register_and_authenticate_device(client, owner)
    response = client.post(
        f"/api/v1/devices/{device['id']}/capture",
        headers={**headers, "Idempotency-Key": "metadata-test"},
        files={"image": ("camera.jpg", image_bytes(size=(80, 48)), "image/jpeg")},
        data={
            "capture_metadata": (
                '{"camera_type":"ESP32_CAM","firmware_version":"1.2.0",'
                '"capture_timestamp":"2026-01-15T10:30:00Z",'
                '"sensor_distance_mm":350,"image_width":1,"image_height":1}'
            )
        },
    )
    assert response.status_code == 200, response.text
    metadata = response.json()["data"]["capture"]["capture_metadata"]
    assert metadata["image_width"] == 80
    assert metadata["image_height"] == 48
    assert metadata["sensor_distance_mm"] == 350

    invalid = client.post(
        f"/api/v1/devices/{device['id']}/capture",
        headers={**headers, "Idempotency-Key": "metadata-invalid"},
        files={"image": ("camera.jpg", image_bytes(), "image/jpeg")},
        data={"capture_metadata": '{"sensor_distance_mm":99999}'},
    )
    assert invalid.status_code == 422

def test_capture_enforces_upload_size_limit(client, monkeypatch):
    from dataclasses import replace

    from vision_vitals.config import settings

    owner = register(client, "oversized-capture@example.com")
    device, headers = register_and_authenticate_device(client, owner)
    monkeypatch.setattr(
        "vision_vitals.api.settings", replace(settings, max_upload_size_mb=1)
    )
    response = client.post(
        f"/api/v1/devices/{device['id']}/capture",
        headers={**headers, "Idempotency-Key": "oversized-capture"},
        files={"image": ("camera.jpg", b"x" * (1024 * 1024 + 1), "image/jpeg")},
    )
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "UPLOAD_TOO_LARGE"


def test_registration_rejects_client_supplied_owner(client):
    owner = register(client, "owner-spoof@example.com")
    response = client.post(
        "/api/v1/devices/register",
        headers=auth_headers(owner),
        json={
            "device_identifier": "esp32-owner-spoof",
            "device_name": "Camera",
            "device_type": "VISION_VITALS_CAMERA",
            "owner_user_id": "attacker-selected-owner",
        },
    )
    assert response.status_code == 422


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

    zero_distance = client.post(
        f"/api/v1/devices/{device['id']}/sensor-readings",
        headers=headers,
        json={"sensor_type": "distance", "value": 0, "unit": "mm", "timestamp": timestamp},
    )
    assert zero_distance.status_code == 422


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