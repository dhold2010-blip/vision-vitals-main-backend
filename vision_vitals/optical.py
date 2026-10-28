"""Experimental optical estimates. No AI-generated or clinically validated vitals.

Client camera samples are untrusted/unattested; only bounded deterministic results
are persisted. Thresholds are engineering rejection rules, not accuracy claims.
"""
from __future__ import annotations

from datetime import datetime, timezone
from itertools import pairwise
from math import acos, cos, hypot, pi, sin, sqrt
from statistics import mean, median, pstdev
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Number = Annotated[float, Field(strict=True, allow_inf_nan=False)]


class Input(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Sample(Input):
    t: Annotated[Number, Field(ge=0, le=45)]
    r: Annotated[Number, Field(ge=0, le=255)]
    g: Annotated[Number, Field(ge=0, le=255)]
    b: Annotated[Number, Field(ge=0, le=255)]
    coverage: Annotated[Number, Field(ge=0, le=1)]
    clipped: Annotated[Number, Field(ge=0, le=1)]


class PulseCapture(Input):
    kind: Literal["pulse"]
    version: Literal[1]
    samples: list[Sample] = Field(min_length=1, max_length=1400)


class Point(Input):
    x: Annotated[Number, Field(ge=-1, le=2)]
    y: Annotated[Number, Field(ge=-1, le=2)]
    z: Annotated[Number, Field(ge=-2, le=2)]


class FaceCapture(Input):
    kind: Literal["face"]
    version: Literal[1]
    width: int = Field(ge=240, le=1920)
    height: int = Field(ge=240, le=1920)
    landmarks: list[Point] = Field(min_length=478, max_length=478)
    matrix: list[Number] = Field(min_length=16, max_length=16)
    expression: Annotated[Number, Field(ge=0, le=1)]
    brightness: Annotated[Number, Field(ge=0, le=255)]
    sharpness: Annotated[Number, Field(ge=0, le=100000)]


OpticalCapture = Annotated[PulseCapture | FaceCapture, Field(discriminator="kind")]


class OpticalMetric(BaseModel):
    value: float | None
    unit: Literal["bpm", "% eye span"]
    status: Literal["ESTIMATED", "UNAVAILABLE"]
    reason: str
    method: str
    captured_at: str
    source: Literal["CAMERA_PPG", "CAMERA_FACE_LANDMARKS"]


class OpticalMetrics(BaseModel):
    pulse: OpticalMetric | None = None
    face: OpticalMetric | None = None


def result(kind, value, reason):
    return OpticalMetric(
        value=value, unit="bpm" if kind == "pulse" else "% eye span",
        status="UNAVAILABLE" if value is None else "ESTIMATED", reason=reason,
        method=("Experimental fingertip PPG v1: timestamp resampling, detrending, spectral peak, "
                "autocorrelation and two-window agreement. Working range 45–180 bpm. Not clinically validated."
                if kind == "pulse" else
                "2D facial landmark reflection v1: mean distance between six mirrored landmark pairs, "
                "normalized to outer-eye span. Smaller values mean less difference in this image, "
                "not better health or attractiveness. Perspective, expression and model error affect it."),
        captured_at=datetime.now(timezone.utc).isoformat(),
        source="CAMERA_PPG" if kind == "pulse" else "CAMERA_FACE_LANDMARKS",
    )


def _spectrum(values: list[float], fps=20):
    n = len(values)
    center = (n - 1) / 2
    avg = mean(values)
    slope = sum((i - center) * (v - avg) for i, v in enumerate(values)) / sum((i - center) ** 2 for i in range(n))
    detrended = [v - avg - slope * (i - center) for i, v in enumerate(values)]
    spread = pstdev(detrended)
    # Reject no signal, major movement/exposure drift, and implausibly large oscillations.
    if spread < 0.08 or spread > avg * 0.08 or abs(slope * n) > avg * 0.12:
        return None
    window = [v * (0.5 - 0.5 * cos(2 * pi * i / (n - 1))) for i, v in enumerate(detrended)]
    powers = []
    # Include out-of-range rates so an aliased harmonic is not silently accepted.
    for bpm in range(35, 221):
        omega = 2 * pi * bpm / (60 * fps)
        real = sum(v * cos(omega * i) for i, v in enumerate(window))
        imag = sum(v * sin(omega * i) for i, v in enumerate(window))
        powers.append(real * real + imag * imag)
    peak = max(range(len(powers)), key=powers.__getitem__)
    bpm = peak + 35
    if not 45 <= bpm <= 180:
        return None
    noise = max(p for i, p in enumerate(powers) if abs(i - peak) > 12)
    if powers[peak] < 5 * noise:
        return None
    lag = round(60 * fps / bpm)
    a, b = detrended[:-lag], detrended[lag:]
    denom = sqrt(sum(v * v for v in a) * sum(v * v for v in b))
    if not denom or sum(x * y for x, y in zip(a, b)) / denom < 0.75:
        return None
    return bpm


def pulse_metric(capture: PulseCapture) -> OpticalMetric:
    fail = lambda message: result("pulse", None, message)
    samples = capture.samples
    gaps = [b.t - a.t for a, b in pairwise(samples)]
    duration = samples[-1].t - samples[0].t
    if len(samples) < 450 or duration < 29 or duration > 35 or not gaps or min(gaps) <= 0 or max(gaps) > 0.2 or median(gaps) > 1 / 14:
        return fail("The recording was too short, slow or interrupted. Keep this page open and record again for 30 seconds.")
    # Ignore initial three seconds of finger placement/exposure settling.
    samples = [s for s in samples if s.t >= samples[0].t + 3]
    good = [s.coverage >= 0.9 and s.clipped < 0.1 and 10 < s.g < 245 for s in samples]
    if sum(good) / len(good) < 0.95:
        return fail("The lens was not evenly covered or lighting was unsuitable. Cover the rear lens gently; adjust light and retry.")
    if any(not any(good[i:i + 5]) for i in range(len(good) - 4)):
        return fail("Finger coverage was interrupted. Hold still with the lens covered throughout the recording.")
    # Linear interpolation at 20 Hz uses real frame timestamps, never assumed frame rate.
    values = []
    index = 0
    for tick in range(int((samples[-1].t - samples[0].t) * 20)):
        t = samples[0].t + tick / 20
        while index + 1 < len(samples) - 1 and samples[index + 1].t < t:
            index += 1
        a, b = samples[index:index + 2]
        values.append(a.g + (b.g - a.g) * (t - a.t) / (b.t - a.t))
    n = len(values)
    estimates = [_spectrum(values), _spectrum(values[:n // 2]), _spectrum(values[n // 2:])]
    if any(v is None for v in estimates) or max(estimates) - min(estimates) > 5:
        return fail("No stable pulse signal was found. Rest your hand, avoid movement and changing light, then retry.")
    return result("pulse", estimates[0], "Approximate camera pulse rate. Experimental wellness use only; verify with a validated device. Motion or lighting can still produce a wrong estimate.")


# MediaPipe canonical mesh pairs: outer/inner eye corners, mouth corners,
# cheeks, temples and jaw. The midline runs from forehead (10) to chin (152).
PAIRS = [(33, 263), (133, 362), (61, 291), (234, 454), (127, 356), (172, 397)]


def face_metric(capture: FaceCapture) -> OpticalMetric:
    fail = lambda message: result("face", None, message)
    if capture.expression > 0.25 or not 40 <= capture.brightness <= 220 or capture.sharpness < 15:
        return fail("Use even lighting, keep a neutral expression with eyes open, and hold the camera steady.")
    # MediaPipe's column-major face transform: third column is the face normal.
    normal = capture.matrix[8:11]
    length = sqrt(sum(v * v for v in normal))
    if not 0.8 <= length <= 1.2 or acos(min(1, abs(normal[2]) / length)) * 180 / pi > 15:
        return fail("Face the camera straight on at eye level. Head rotation changes the symmetry estimate.")
    points = [(p.x * capture.width, p.y * capture.height) for p in capture.landmarks]
    needed = {10, 152, 168, *(i for pair in PAIRS for i in pair)}
    if any(not (0.03 < capture.landmarks[i].x < 0.97 and 0.03 < capture.landmarks[i].y < 0.97) for i in needed):
        return fail("Keep your entire face inside the frame, including your forehead and chin.")
    top, chin = points[10], points[152]
    length = hypot(chin[0] - top[0], chin[1] - top[1])
    eye_span = hypot(points[33][0] - points[263][0], points[33][1] - points[263][1])
    if length < capture.height * 0.3 or eye_span < 70 or length < eye_span:
        return fail("Move closer until your face fills the guide, while keeping forehead and chin visible.")
    ux, uy = (chin[0] - top[0]) / length, (chin[1] - top[1]) / length
    def reflect(p):
        projection = (p[0] - top[0]) * ux + (p[1] - top[1]) * uy
        return (2 * (top[0] + projection * ux) - p[0], 2 * (top[1] + projection * uy) - p[1])
    errors = [hypot(reflect(points[a])[0] - points[b][0], reflect(points[a])[1] - points[b][1]) for a, b in PAIRS]
    value = round(mean(errors) / eye_span * 100, 1)
    if value > 30:
        return fail("Landmarks were inconsistent. Remove obstructions, face forward and retake the capture.")
    return result("face", value, "Approximate left–right landmark difference as a percentage of eye span. This describes this capture only, not a diagnosis or an attractiveness score.")


def estimate(capture: OpticalCapture) -> OpticalMetric:
    return pulse_metric(capture) if isinstance(capture, PulseCapture) else face_metric(capture)
