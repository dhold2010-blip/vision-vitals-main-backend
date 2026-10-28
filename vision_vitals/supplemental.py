"""User readings and explicit scale-reference geometry; no appearance-based health inference."""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .pose import PoseCapture, camera_metrics


class Point(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)
    x: float = Field(ge=0, le=1)
    y: float = Field(ge=0, le=1)


class HeightReference(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)
    reference_cm: float = Field(ge=20, le=250)
    # Top of reference, bottom of reference, top of head, soles of feet.
    points: list[Point] = Field(min_length=4, max_length=4)
    same_plane_confirmed: Literal[True]
    width: int = Field(strict=True, ge=1, le=8192)
    height: int = Field(strict=True, ge=1, le=8192)


class MeasurementInputs(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, strict=True)
    version: Literal[1] = 1
    age_years: int | None = Field(default=None, strict=True, ge=0, le=120)
    height_cm: float | None = Field(default=None, ge=40, le=260)
    weight_kg: float | None = Field(default=None, ge=2, le=500)
    pulse_bpm: int | None = Field(default=None, strict=True, ge=20, le=250)
    height_reference: HeightReference | None = None


class SupplementalMetric(BaseModel):
    key: Literal["age", "height", "height-estimate", "weight", "bmi", "heart-rate"]
    label: str
    value: float | None
    unit: Literal["years", "cm", "kg", "kg/m²", "bpm"]
    source: Literal["USER_PROVIDED", "CALCULATED", "CAMERA_REFERENCE"]
    status: Literal["RECORDED", "CALCULATED", "ESTIMATED", "UNAVAILABLE"]
    reason: str


class SupplementalMetrics(BaseModel):
    version: Literal[1] = 1
    metrics: list[SupplementalMetric]


def estimate_height(reference: HeightReference, pose: PoseCapture | None, width: int, height: int):
    if (reference.width, reference.height) != (width, height):
        return None, "Reference marks do not match the captured image. Retake and mark again."
    if not pose or pose.status != "DETECTED" or (pose.width, pose.height) != (width, height):
        return None, "A single full standing body must be detected for the reference estimate."
    if any(
        pose.landmarks[i].visibility < 0.8
        or (pose.landmarks[i].presence is not None and pose.landmarks[i].presence < 0.8)
        or not (0.02 <= pose.landmarks[i].x <= 0.98 and 0.02 <= pose.landmarks[i].y <= 0.98)
        for i in (0, 11, 12, 23, 24, 27, 28, 31, 32)
    ):
        return None, "Head, torso and both feet must be clearly visible. Retake from farther back."
    angles = {m.key: m.value for m in camera_metrics(pose, width, height).metrics}
    if (
        any(angles[k] is None or angles[k] < 165 for k in ("left_knee", "right_knee"))
        or angles["torso_lean"] is None
        or angles["torso_lean"] > 10
    ):
        return None, "Stand upright with straight legs for a height estimate."
    a, b, c, d = reference.points
    if b.y <= a.y or d.y <= c.y:
        return None, "Mark each top before its bottom. Reset the reference marks."
    ref_dy, body_dy = (b.y - a.y) * height, (d.y - c.y) * height
    if ref_dy < max(60, height * 0.15) or body_dy < height * 0.4:
        return (
            None,
            "The reference or body occupies too few pixels. Use a larger reference and retake.",
        )
    if abs(a.x - b.x) * width > ref_dy * 0.1 or abs(c.x - d.x) * width > body_dy * 0.15:
        return None, "The reference and body must be upright with the camera level."
    nose = pose.landmarks[0]
    feet_y = max(pose.landmarks[i].y for i in (27, 28, 31, 32))
    if (
        not (nose.y - 0.15 <= c.y <= nose.y)
        or abs(c.x - nose.x) > 0.15
        or not (feet_y - 0.025 <= d.y <= feet_y + 0.1)
    ):
        return None, "Head or foot marks do not align with the detected person. Reset the marks."
    value = reference.reference_cm * body_dy / ref_dy
    if not math.isfinite(value) or not 80 <= value <= 250:
        return (
            None,
            "Estimate is outside the supported 80–250 cm range. Check the reference length and marks.",
        )
    return (
        round(value),
        f"Using a {reference.reference_cm:g} cm reference. Rough estimate from your reference length and marked head/feet. Assumes equal distance from a level camera; perspective, hair, shoes and marking error affect it. Accuracy is not validated. Not used for BMI.",
    )


def supplemental_metrics(
    inputs: MeasurementInputs | None, pose: PoseCapture | None, width: int, height: int
):
    values = inputs or MeasurementInputs()
    rows = []
    for key, label, value, unit, reason in [
        (
            "age",
            "Age",
            values.age_years,
            "years",
            "Enter age in completed years; age is not inferred from appearance.",
        ),
        (
            "height",
            "Measured height",
            values.height_cm,
            "cm",
            "Enter height measured with a tape or stadiometer.",
        ),
        (
            "weight",
            "Weight",
            values.weight_kg,
            "kg",
            "Enter a recent reading from weighing scales.",
        ),
        (
            "heart-rate",
            "Heart rate",
            values.pulse_bpm,
            "bpm",
            "Enter a pulse reading from a device. This photo flow does not measure pulse.",
        ),
    ]:
        rows.append(
            SupplementalMetric(
                key=key,
                label=label,
                value=value,
                unit=unit,
                source="USER_PROVIDED",
                status="RECORDED" if value is not None else "UNAVAILABLE",
                reason="Entered by you for this scan; not measured or verified by the camera."
                if value is not None
                else reason,
            )
        )
    bmi = None
    bmi_reason = "Enter measured height, weight and age (20 or older) to calculate adult BMI."
    if values.age_years is not None and values.age_years < 20:
        bmi_reason = "This adult BMI feature is for ages 20 and older. Child and teen assessment needs an age-specific method."
    elif values.age_years is not None and values.height_cm and values.weight_kg:
        bmi = round(values.weight_kg / (values.height_cm / 100) ** 2, 1)
        bmi_reason = "Calculated as entered weight (kg) ÷ entered height (m)². BMI is a screening index, not body fat or a health score."
    rows.append(
        SupplementalMetric(
            key="bmi",
            label="Adult body mass index",
            value=bmi,
            unit="kg/m²",
            source="CALCULATED",
            status="CALCULATED" if bmi is not None else "UNAVAILABLE",
            reason=bmi_reason,
        )
    )
    estimate, reason = (
        None,
        "Enable guided height before scanning and place a known-length vertical reference beside you at the same distance from the camera.",
    )
    if values.height_reference:
        estimate, reason = estimate_height(values.height_reference, pose, width, height)
    rows.append(
        SupplementalMetric(
            key="height-estimate",
            label="Reference-based height estimate",
            value=estimate,
            unit="cm",
            source="CAMERA_REFERENCE",
            status="ESTIMATED" if estimate is not None else "UNAVAILABLE",
            reason=reason,
        )
    )
    return SupplementalMetrics(metrics=rows)
