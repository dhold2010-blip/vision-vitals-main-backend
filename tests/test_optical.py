import io
import math
import random

import pytest
from PIL import Image
from pydantic import ValidationError

from tests.conftest import register, auth_headers
from vision_vitals.optical import PulseCapture, FaceCapture, pulse_metric, face_metric, PAIRS


def pulse(bpm=72, fps=30):
    return PulseCapture(kind="pulse", version=1, samples=[
        {"t": i / fps, "r": 190, "g": 95 + 1.5 * math.sin(2 * math.pi * bpm / 60 * i / fps),
         "b": 45, "coverage": 1, "clipped": 0} for i in range(fps * 30 + 1)
    ])


def face():
    points = [{"x": .5, "y": .5, "z": 0} for _ in range(478)]
    points[10]["y"] = .15
    points[152]["y"] = .85
    for j, (a, b) in enumerate(PAIRS):
        points[a] = {"x": .3, "y": .3 + j * .06, "z": 0}
        points[b] = {"x": .7, "y": .3 + j * .06, "z": 0}
    return FaceCapture(kind="face", version=1, width=640, height=480, landmarks=points,
                       matrix=[1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1, 0, 0, 0, 0, 1],
                       expression=.1, brightness=120, sharpness=90)


@pytest.mark.parametrize("bpm,fps", [(48, 30), (72, 30), (110, 24), (150, 30), (175, 20)])
def test_clean_signal(bpm, fps):
    result = pulse_metric(pulse(bpm, fps))
    assert result.status == "ESTIMATED", result.reason
    assert abs(result.value - bpm) <= 1


@pytest.mark.parametrize("case", ["flat", "noise", "motion", "short", "gap", "duplicates", "coverage", "clipped", "dark", "unstable", "harmonic", "too_slow", "out_of_range"])
def test_unreliable_signal_rejected(case):
    data = pulse(200 if case == "out_of_range" else 72, 10 if case == "too_slow" else 30)
    rng = random.Random(7)
    if case == "short": data.samples = data.samples[:300]
    if case == "gap": data.samples = data.samples[:400] + data.samples[430:]
    if case == "duplicates": data.samples[400].t = data.samples[399].t
    for i, s in enumerate(data.samples):
        if case == "flat": s.g = 95
        if case == "noise": s.g = 95 + rng.gauss(0, 2)
        if case == "motion": s.g += 15 * math.sin(s.t * .4)
        if case == "coverage" and i > 350: s.coverage = .2
        if case == "clipped": s.clipped = .5
        if case == "dark": s.g = 3
        if case == "unstable" and s.t > 15: s.g = 95 + 1.5 * math.sin(2 * math.pi * 110 / 60 * s.t)
        if case == "harmonic": s.g += 1.5 * math.sin(2 * math.pi * 144 / 60 * s.t)
    assert pulse_metric(data).value is None


def test_jittered_sampling():
    data = pulse()
    for i, s in enumerate(data.samples):
        s.t += .002 * math.sin(i)
        s.g = 95 + 1.5 * math.sin(2 * math.pi * 72 / 60 * s.t)
    assert pulse_metric(data).value == 72


def test_face_reflection_and_known_offset():
    data = face()
    assert face_metric(data).value == 0
    data.landmarks[291].x += .04
    # One of six pairs shifted by 10% of the outer-eye span.
    assert face_metric(data).value == 1.7


@pytest.mark.parametrize("case", ["yaw", "pitch", "expression", "dark", "blur", "cropped", "small", "bad_matrix"])
def test_face_quality(case):
    data = face()
    if case == "yaw": data.matrix[8], data.matrix[10] = .5, .866
    if case == "pitch": data.matrix[9], data.matrix[10] = .5, .866
    if case == "expression": data.expression = .8
    if case == "dark": data.brightness = 20
    if case == "blur": data.sharpness = 1
    if case == "cropped": data.landmarks[10].y = .01
    if case == "small":
        for p in data.landmarks: p.x = .5 + (p.x - .5) * .2; p.y = .5 + (p.y - .5) * .2
    if case == "bad_matrix": data.matrix = [0] * 16
    assert face_metric(data).value is None


def test_schema_rejects_nonfinite_and_oversized():
    data = pulse().model_dump()
    data["samples"][0]["g"] = float("nan")
    with pytest.raises(ValidationError): PulseCapture.model_validate(data)
    data = pulse().model_dump(); data["samples"] *= 2
    with pytest.raises(ValidationError): PulseCapture.model_validate(data)
    data = face().model_dump(); data["landmarks"] = []
    with pytest.raises(ValidationError): FaceCapture.model_validate(data)


def test_report_updates_are_owned_persistent_and_fail_closed(client):
    headers = auth_headers(register(client, "optical@example.com"))
    other = auth_headers(register(client, "other-optical@example.com"))
    image = io.BytesIO(); Image.new("RGB", (600, 1000), "#8295aa").save(image, "JPEG")
    response = client.post("/api/v1/analyses", headers=headers, files={"image": ("photo.jpg", image.getvalue(), "image/jpeg")})
    assert response.status_code == 201, response.text
    analysis_id = response.json()["data"]["id"]
    url = f"/api/v1/analyses/{analysis_id}/camera-measurements"
    assert client.post(url, headers=other, json=face().model_dump()).status_code == 404
    assert client.post(url, json=face().model_dump()).status_code == 401
    assert client.post(url, headers=headers, json={"kind": "pulse", "version": 1, "samples": []}).status_code == 422
    response = client.post(url, headers=headers, json=face().model_dump())
    assert response.status_code == 200, response.text
    response = client.post(url, headers=headers, json=pulse().model_dump())
    assert response.status_code == 200, response.text
    result = response.json()["data"]["result"]["optical_metrics"]
    assert result["pulse"]["value"] == 72 and result["face"]["value"] == 0
    assert "samples" not in result["pulse"]
    data = pulse(); data.samples = data.samples[:5]
    response = client.post(url, headers=headers, json=data.model_dump())
    assert response.json()["data"]["result"]["optical_metrics"]["pulse"]["value"] is None
    saved = client.get(f"/api/v1/analyses/{analysis_id}", headers=headers).json()["data"]["result"]["optical_metrics"]
    assert saved["face"]["value"] == 0 and saved["pulse"]["status"] == "UNAVAILABLE"
