"""Deterministic image-plane geometry, never LLM-generated or clinical measurements.

Landmarks are supplied by the browser's MediaPipe model and are not attested.
The server validates their shape and image dimensions and computes every value.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Landmark(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    # Detected landmarks may legitimately extend beyond the crop; reject them per metric.
    x: float = Field(ge=-5, le=5)
    y: float = Field(ge=-5, le=5)
    visibility: float = Field(ge=0, le=1)
    presence: float | None = Field(default=None, ge=0, le=1)


class PoseCapture(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    version: Literal[1]
    model: Literal["mediapipe-pose-landmarker-lite-float16-v1"]
    status: Literal["DETECTED", "NO_POSE", "MULTIPLE_POSES", "MODEL_UNAVAILABLE"]
    width: int = Field(strict=True, ge=1, le=8192)
    height: int = Field(strict=True, ge=1, le=8192)
    landmarks: list[Landmark] = Field(default_factory=list, max_length=33)

    @model_validator(mode="after")
    def consistent_landmarks(self):
        if len(self.landmarks) != (33 if self.status == "DETECTED" else 0):
            raise ValueError("Exactly 33 landmarks are required only for DETECTED captures")
        return self


class CameraMetric(BaseModel):
    key: str
    label: str
    value: float | None
    unit: Literal["degrees"] = "degrees"
    status: Literal["ESTIMATED", "UNAVAILABLE"]
    reason: str


class CameraMetrics(BaseModel):
    version: Literal[1] = 1
    method: str = "MediaPipe Pose Lite v1 · 2D image geometry"
    source: str = "BROWSER_LANDMARKS"
    status: Literal["AVAILABLE", "PARTIAL", "UNAVAILABLE"]
    metrics: list[CameraMetric]
    limitations: list[str]


SPECS = [
    ("shoulder_tilt", "Shoulder line tilt", (11, 12), "line"),
    ("hip_tilt", "Hip line tilt", (23, 24), "line"),
    ("torso_lean", "Torso lean in image", (11, 12, 23, 24), "torso"),
    ("left_elbow", "Left elbow angle", (11, 13, 15), "joint"),
    ("right_elbow", "Right elbow angle", (12, 14, 16), "joint"),
    ("left_knee", "Left knee angle", (23, 25, 27), "joint"),
    ("right_knee", "Right knee angle", (24, 26, 28), "joint"),
]
LIMITATIONS = [
    "Estimates from detected landmarks in one 2D image; not clinical joint measurements or a posture score.",
    "Camera tilt, perspective, clothing and occlusion change the estimates. Face the camera with the lens level, limbs visible and only one person in frame.",
    "Left/right refer to the person's anatomical sides. A straight joint is approximately 180 degrees; line tilt is relative to the image horizontal and torso lean to the image vertical.",
    "Landmarks are detected on the device and submitted by the browser; the server computes angles but does not independently verify the landmarks against the photo.",
]
REASONS = {
    "NO_POSE": "No body landmarks detected. Retake with your full body clearly visible.",
    "MULTIPLE_POSES": "More than one person detected. Retake with only one person in frame.",
    "MODEL_UNAVAILABLE": "The landmark detector could not run. Retry on a supported browser with a stable connection.",
}


def camera_metrics(capture: PoseCapture | None, width: int, height: int) -> CameraMetrics:
    reason = "Landmarks were not recorded for this report. Take a new camera scan."
    if capture:
        reason = REASONS.get(capture.status, "")
        if (capture.width, capture.height) != (width, height):
            reason = "Landmark and image dimensions do not match. Retake the scan."
    metrics = []
    for key, label, indices, kind in SPECS:
        issue = reason
        value = None
        if not issue and capture:
            points = [capture.landmarks[i] for i in indices]
            if any(
                p.visibility < 0.7
                or (p.presence is not None and p.presence < 0.7)
                or not (0.02 <= p.x <= 0.98 and 0.02 <= p.y <= 0.98)
                for p in points
            ):
                issue = "Required joints are obscured, cropped or uncertain. Retake with these joints clearly visible."
            else:
                xy = [(p.x * width, p.y * height) for p in points]
                # Reject near-collapsed segments that make an angle unstable.
                minimum = max(8, min(width, height) * 0.025)
                if kind == "line":
                    a, b = xy
                    if math.dist(a, b) < minimum:
                        issue = (
                            "Landmarks are too close together for a stable angle. Face the camera."
                        )
                    else:
                        value = math.degrees(math.atan2(abs(a[1] - b[1]), abs(a[0] - b[0])))
                elif kind == "torso":
                    top = tuple((xy[0][i] + xy[1][i]) / 2 for i in (0, 1))
                    bottom = tuple((xy[2][i] + xy[3][i]) / 2 for i in (0, 1))
                    if math.dist(top, bottom) < minimum:
                        issue = "Torso landmarks are too close together for a stable angle."
                    else:
                        value = math.degrees(
                            math.atan2(abs(top[0] - bottom[0]), abs(top[1] - bottom[1]))
                        )
                else:
                    a, b, c = xy
                    if min(math.dist(a, b), math.dist(b, c)) < minimum:
                        issue = "Joint landmarks are too close together for a stable angle."
                    else:
                        u, v = (a[0] - b[0], a[1] - b[1]), (c[0] - b[0], c[1] - b[1])
                        cosine = (u[0] * v[0] + u[1] * v[1]) / (math.hypot(*u) * math.hypot(*v))
                        value = math.degrees(math.acos(max(-1, min(1, cosine))))
        metrics.append(
            CameraMetric(
                key=key,
                label=label,
                value=round(value, 1) if value is not None else None,
                status="ESTIMATED" if value is not None else "UNAVAILABLE",
                reason=issue
                or "2D estimate from visible body landmarks; no clinical interpretation.",
            )
        )
    count = sum(m.value is not None for m in metrics)
    return CameraMetrics(
        status="AVAILABLE" if count == len(metrics) else "PARTIAL" if count else "UNAVAILABLE",
        metrics=metrics,
        limitations=LIMITATIONS,
    )
