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
