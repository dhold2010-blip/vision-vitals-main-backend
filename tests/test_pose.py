import io
import json
import math

import pytest
from PIL import Image
from pydantic import ValidationError

from tests.conftest import register, auth_headers
from vision_vitals.pose import PoseCapture, camera_metrics


def capture():
    points = [{"x": 0.5, "y": 0.5, "visibility": 0.99, "presence": 0.99} for _ in range(33)]
    for index, x, y in [
        (11, 0.35, 0.2),
        (12, 0.65, 0.2),
        (13, 0.25, 0.4),
        (14, 0.75, 0.4),
        (15, 0.35, 0.6),
        (16, 0.65, 0.6),
        (23, 0.4, 0.5),
        (24, 0.6, 0.5),
        (25, 0.4, 0.7),
        (26, 0.6, 0.7),
        (27, 0.4, 0.9),
        (28, 0.6, 0.9),
    ]:
        points[index].update(x=x, y=y)
    return PoseCapture(
        version=1,
        model="mediapipe-pose-landmarker-lite-float16-v1",
        status="DETECTED",
        width=800,
        height=400,
        landmarks=points,
    )


def test_pixel_aspect_ratio_and_known_angles():
    report = camera_metrics(capture(), 800, 400)
    values = {m.key: m.value for m in report.metrics}
    assert report.status == "AVAILABLE"
    assert values == {
        "shoulder_tilt": 0.0,
        "hip_tilt": 0.0,
        "torso_lean": 0.0,
        "left_elbow": 90.0,
        "right_elbow": 90.0,
        "left_knee": 180.0,
        "right_knee": 180.0,
    }
    # Normalized coordinates alone would incorrectly give ~127 degrees here.


def test_visibility_crop_and_collapsed_segments_withhold_only_affected_angles():
    pose = capture()
    pose.landmarks[15].visibility = 0.2
    pose.landmarks[28].x = 1.2
    pose.landmarks[14] = pose.landmarks[12].model_copy()
    result = camera_metrics(pose, 800, 400)
    assert result.status == "PARTIAL"
    values = {m.key: m.value for m in result.metrics}
    assert values["left_elbow"] is None
    assert values["right_knee"] is None
    assert values["right_elbow"] is None
    assert values["left_knee"] == 180
    assert all(m.reason for m in result.metrics)


@pytest.mark.parametrize("status", ["NO_POSE", "MULTIPLE_POSES", "MODEL_UNAVAILABLE"])
def test_unavailable_detector_never_produces_numbers(status):
    pose = capture().model_copy(update={"status": status, "landmarks": []})
    report = camera_metrics(pose, 800, 400)
    assert report.status == "UNAVAILABLE"
    assert all(m.value is None for m in report.metrics)


def test_missing_or_mismatched_capture_is_explicit():
    assert camera_metrics(None, 800, 400).status == "UNAVAILABLE"
    assert camera_metrics(capture(), 400, 800).status == "UNAVAILABLE"


@pytest.mark.parametrize(
    "change",
    [
        {"landmarks": []},
        {"version": 2},
        {"width": 0},
        {"width": True},
        {"status": "NO_POSE"},
        {"model": "invented"},
        {"metrics": {"heart_rate": 70}},
    ],
)
def test_untrusted_input_rejected(change):
    payload = capture().model_dump()
    payload.update(change)
    with pytest.raises(ValidationError):
        PoseCapture.model_validate(payload)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_nonfinite_coordinates_rejected(value):
    payload = capture().model_dump()
    payload["landmarks"][11]["x"] = value
    with pytest.raises(ValidationError):
        PoseCapture.model_validate(payload)


def upload(client, headers, pose):
    image = io.BytesIO()
    Image.new("RGB", (800, 400), "#8699ab").save(image, "JPEG")
    return client.post(
        "/api/v1/analyses",
        headers=headers,
        files={"image": ("camera.jpg", image.getvalue(), "image/jpeg")},
        data={"source": "APP_CAMERA", "pose_capture": json.dumps(pose)},
    )


def test_geometry_persists_in_owned_report_list_and_get(client):
    owner = auth_headers(register(client, "pose-owner@example.com"))
    other = auth_headers(register(client, "pose-other@example.com"))
    response = upload(client, owner, capture().model_dump())
    assert response.status_code == 201, response.text
    saved = response.json()["data"]
    metrics = saved["result"]["camera_metrics"]
    assert metrics["metrics"][3]["value"] == 90.0
    assert (
        client.get("/api/v1/analyses/" + saved["id"], headers=owner).json()["data"]["result"][
            "camera_metrics"
        ]
        == metrics
    )
    assert (
        client.get("/api/v1/analyses", headers=owner).json()["data"][0]["result"]["camera_metrics"]
        == metrics
    )
    assert client.get("/api/v1/analyses/" + saved["id"], headers=other).status_code == 403
    assert upload(client, owner, {"bad": "metadata"}).status_code == 422


def test_valid_geometry_survives_description_service_outage(client, monkeypatch):
    from vision_vitals.ai import MockAIProvider
    from vision_vitals.errors import AppError

    def fail(*args):
        raise AppError("AI_PROVIDER_ERROR", "upstream offline", 503)

    monkeypatch.setattr(MockAIProvider, "analyze", fail)
    headers = auth_headers(register(client, "pose-fallback@example.com"))
    response = upload(client, headers, capture().model_dump())
    assert response.status_code == 201
    result = response.json()["data"]["result"]
    assert result["camera_metrics"]["status"] == "AVAILABLE"
    assert result["provider"] == "camera_geometry"
    assert result["confidence"] is None
    assert result["warnings"] == ["PHOTO_DESCRIPTION_UNAVAILABLE"]
