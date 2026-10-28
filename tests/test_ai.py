import json

import httpx
import pytest

from vision_vitals.ai import GeminiProvider
from vision_vitals.errors import AIOutputInvalidError, AppError
from vision_vitals.schemas import AIAnalysisRequest


def test_gemini_uses_configured_identity_not_generated_metadata(monkeypatch):
    calls = []

    def generate(url, **kwargs):
        calls.append(url)
        payload = {
            "provider": "incorrect-provider",
            "model": "gemini-1.5-flash",
            "observation": "A geometric test image is visible.",
            "result_status": "UNAVAILABLE",
            "confidence": None,
            "warnings": [],
            "limitations": ["No person in test image."],
            "request_id": "test-request",
            "visual_assessment": {"subject_visible": False, "framing": "No person visible.",
                                  "lighting": "Even lighting.", "posture": "Not assessable.",
                                  "capture_tips": ["Include a person in frame."]},
        }
        return httpx.Response(
            200, request=httpx.Request("POST", url),
            json={"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]},
        )

    monkeypatch.setattr("vision_vitals.ai.httpx.post", generate)
    provider = GeminiProvider("test-key", "gemini-3.5-flash-lite")
    request = AIAnalysisRequest(
        request_id="test-request", mime_type="image/jpeg", image_sha256="test-hash"
    )
    result = provider.analyze(request, b"synthetic-image")
    assert calls == [
        (
            "https://generativelanguage.googleapis.com/v1beta/models/"
            "gemini-3.5-flash-lite:generateContent"
        )
    ]
    assert result.provider == "gemini"
    assert result.model == "gemini-3.5-flash-lite"
    assert result.result_status == "UNAVAILABLE"
    assert result.request_id == request.request_id


def test_gemini_still_rejects_an_unrelated_response(monkeypatch):
    def generate(url, **kwargs):
        return httpx.Response(
            200, request=httpx.Request("POST", url),
            json={"candidates": [{"content": {"parts": [{"text": json.dumps({
                "provider": "gemini", "model": "any-model", "observation": "Test",
                "result_status": "UNAVAILABLE", "request_id": "wrong-request",
            })}]}}]},
        )

    monkeypatch.setattr("vision_vitals.ai.httpx.post", generate)
    request = AIAnalysisRequest(
        request_id="test-request", mime_type="image/jpeg", image_sha256="test-hash"
    )
    with pytest.raises(AIOutputInvalidError):
        GeminiProvider("test-key", "gemini-3.5-flash-lite").analyze(request, b"test")


def test_gemini_requires_a_backend_key():
    with pytest.raises(AppError) as error:
        GeminiProvider("", "gemini-3.5-flash-lite")
    assert error.value.status_code == 503


@pytest.mark.parametrize("subject_visible", [True, False])
def test_visual_report_uses_photo_findings_without_claiming_measurement_confidence(
    monkeypatch, subject_visible
):
    def generate(url, **kwargs):
        prompt = kwargs["json"]["contents"][0]["parts"][0]["text"]
        assert "non-medical visual report" in prompt
        assert "not a generic disclaimer" in prompt
        payload = {
            "provider": "gemini", "model": "generated-model", "request_id": "visual-test",
            "observation": "A standing person is visible." if subject_visible else "Empty frame.",
            "result_status": "OBSERVATION", "confidence": 0.99,
            "visual_assessment": {
                "subject_visible": subject_visible, "framing": "Full frame visible.",
                "lighting": "Even lighting.", "posture": "Standing." if subject_visible else "No person.",
                "capture_tips": ["Keep the camera steady."],
            },
        }
        return httpx.Response(
            200, request=httpx.Request("POST", url),
            json={"candidates": [{"content": {"parts": [{"text": json.dumps(payload)}]}}]},
        )

    monkeypatch.setattr("vision_vitals.ai.httpx.post", generate)
    result = GeminiProvider("test", "configured-model").analyze(
        AIAnalysisRequest(request_id="visual-test", mime_type="image/jpeg", image_sha256="hash"),
        b"test-image",
    )
    assert result.confidence is None
    assert result.result_status == ("OBSERVATION" if subject_visible else "UNAVAILABLE")
    assert result.visual_assessment.lighting == "Even lighting."


def test_unavailable_is_not_zero_confidence_and_legacy_results_remain_readable():
    from vision_vitals.schemas import AIAnalysisResponse

    result = AIAnalysisResponse(
        provider="gemini", model="old", observation="No useful observation.",
        result_status="UNAVAILABLE", confidence=0, request_id="legacy",
    )
    assert result.confidence is None
    assert result.visual_assessment is None
