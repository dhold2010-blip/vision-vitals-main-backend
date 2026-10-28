import io
import json

import pytest
from PIL import Image
from pydantic import ValidationError

from tests.conftest import register, auth_headers
from vision_vitals.pose import PoseCapture
from vision_vitals.supplemental import MeasurementInputs, HeightReference, supplemental_metrics


def standing_pose():
    points = [{"x": 0.5, "y": 0.15, "visibility": 0.99, "presence": 0.99} for _ in range(33)]
    for i, x, y in [
        (11, 0.4, 0.25),
        (12, 0.6, 0.25),
        (23, 0.43, 0.5),
        (24, 0.57, 0.5),
        (25, 0.43, 0.7),
        (26, 0.57, 0.7),
        (27, 0.43, 0.87),
        (28, 0.57, 0.87),
        (31, 0.43, 0.9),
        (32, 0.57, 0.9),
    ]:
        points[i].update(x=x, y=y)
    return PoseCapture(
        version=1,
        model="mediapipe-pose-landmarker-lite-float16-v1",
        status="DETECTED",
        width=600,
        height=1000,
        landmarks=points,
    )


def reference():
    return HeightReference(
        reference_cm=100,
        width=600,
        height=1000,
        same_plane_confirmed=True,
        points=[
            {"x": 0.2, "y": 0.3},
            {"x": 0.2, "y": 0.8},
            {"x": 0.5, "y": 0.1},
            {"x": 0.5, "y": 0.95},
        ],
    )


def rows(inputs=None, pose=None):
    return {m.key: m for m in supplemental_metrics(inputs, pose, 600, 1000).metrics}


def test_entered_values_and_bmi_have_distinct_sources():
    r = rows(MeasurementInputs(age_years=30, height_cm=170, weight_kg=65, pulse_bpm=72))
    assert r["age"].value == 30
    assert r["height"].source == "USER_PROVIDED"
    assert r["heart-rate"].status == "RECORDED"
    assert r["bmi"].value == 22.5
    assert r["bmi"].source == "CALCULATED"
    assert r["height-estimate"].value is None


def test_reference_height_and_entered_height_do_not_replace_each_other():
    r = rows(
        MeasurementInputs(age_years=30, weight_kg=65, height_reference=reference()), standing_pose()
    )
    assert r["height-estimate"].value == 170
    assert r["height-estimate"].status == "ESTIMATED"
    assert "100 cm reference" in r["height-estimate"].reason
    assert r["height"].value is None
    assert r["bmi"].value is None  # never use rough photo estimates for BMI


@pytest.mark.parametrize("age", [None, 0, 19])
def test_adult_bmi_requires_confirmed_adult_age(age):
    r = rows(MeasurementInputs(age_years=age, height_cm=170, weight_kg=65))
    assert r["bmi"].value is None
    assert r["weight"].value == 65


def test_no_inputs_never_create_numbers():
    assert all(m.value is None for m in rows().values())


@pytest.mark.parametrize(
    "field,value",
    [
        ("age_years", 121),
        ("age_years", 20.5),
        ("height_cm", 0),
        ("height_cm", True),
        ("weight_kg", float("nan")),
        ("pulse_bpm", 251),
        ("pulse_bpm", 50.5),
        ("health_score", 90),
    ],
)
def test_invalid_measurements_rejected(field, value):
    with pytest.raises(ValidationError):
        MeasurementInputs.model_validate({field: value})


@pytest.mark.parametrize(
    "case",
    [
        "no_pose",
        "wrong_dimensions",
        "cropped",
        "uncertain",
        "bent_leg",
        "too_small",
        "wrong_order",
        "tilted",
        "wrong_head",
        "outside_range",
    ],
)
def test_reference_failure_has_no_estimate(case):
    pose = standing_pose()
    ref = reference()
    if case == "no_pose":
        pose = None
    if case == "wrong_dimensions":
        ref.width = 1200
    if case == "cropped":
        pose.landmarks[31].y = 1.1
    if case == "uncertain":
        pose.landmarks[0].visibility = 0.2
    if case == "bent_leg":
        pose.landmarks[25].x = 0.8
    if case == "too_small":
        ref.points[1].y = 0.31
    if case == "wrong_order":
        ref.points[0].y = 0.9
    if case == "tilted":
        ref.points[1].x = 0.8
    if case == "wrong_head":
        ref.points[2].y = 0.3
    if case == "outside_range":
        ref.reference_cm = 250
    r = rows(MeasurementInputs(height_reference=ref), pose)["height-estimate"]
    assert r.value is None
    assert r.status == "UNAVAILABLE"
    assert r.reason


def test_saved_report_preserves_values_and_ownership(client):
    user = auth_headers(register(client, "readings@example.com"))
    other = auth_headers(register(client, "other-readings@example.com"))
    image = io.BytesIO()
    Image.new("RGB", (600, 1000), "#8295aa").save(image, "JPEG")
    inputs = MeasurementInputs(
        age_years=30, height_cm=170, weight_kg=65, pulse_bpm=72, height_reference=reference()
    )
    response = client.post(
        "/api/v1/analyses",
        headers=user,
        files={"image": ("photo.jpg", image.getvalue(), "image/jpeg")},
        data={
            "source": "APP_CAMERA",
            "pose_capture": standing_pose().model_dump_json(),
            "measurement_inputs": inputs.model_dump_json(),
        },
    )
    assert response.status_code == 201, response.text
    saved = response.json()["data"]
    report = saved["result"]["supplemental_metrics"]
    assert len(report["metrics"]) == 6
    assert (
        client.get("/api/v1/analyses/" + saved["id"], headers=user).json()["data"]["result"][
            "supplemental_metrics"
        ]
        == report
    )
    assert (
        client.get("/api/v1/analyses", headers=user).json()["data"][0]["result"][
            "supplemental_metrics"
        ]
        == report
    )
    assert client.get("/api/v1/analyses/" + saved["id"], headers=other).status_code == 403
    bad = client.post(
        "/api/v1/analyses",
        headers=user,
        files={"image": ("photo.jpg", image.getvalue(), "image/jpeg")},
        data={"measurement_inputs": json.dumps({"height_cm": -1})},
    )
    assert bad.status_code == 422


def test_entered_readings_survive_description_failure_without_pose(client, monkeypatch):
    from vision_vitals.ai import MockAIProvider
    from vision_vitals.errors import AppError

    def fail(*args):
        raise AppError("AI_PROVIDER_ERROR", "offline", 503)

    monkeypatch.setattr(MockAIProvider, "analyze", fail)
    user = auth_headers(register(client, "entered-fallback@example.com"))
    image = io.BytesIO()
    Image.new("RGB", (600, 1000), "#8295aa").save(image, "JPEG")
    response = client.post(
        "/api/v1/analyses",
        headers=user,
        files={"image": ("photo.jpg", image.getvalue(), "image/jpeg")},
        data={
            "measurement_inputs": json.dumps({"age_years": 30, "height_cm": 170, "weight_kg": 65})
        },
    )
    assert response.status_code == 201
    result = response.json()["data"]["result"]
    assert result["provider"] == "measurement_pipeline"
    assert result["camera_metrics"]["status"] == "UNAVAILABLE"
    assert (
        next(m for m in result["supplemental_metrics"]["metrics"] if m["key"] == "bmi")["value"]
        == 22.5
    )
