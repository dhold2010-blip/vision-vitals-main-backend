from __future__ import annotations

import base64
import json

import httpx

from .config import settings
from .errors import AIOutputInvalidError, AppError
from .schemas import AIAnalysisRequest, AIAnalysisResponse


class AIProvider:
    name = "unknown"
    model = "unknown"

    def analyze(self, request: AIAnalysisRequest, image: bytes) -> AIAnalysisResponse:
        raise NotImplementedError


class MockAIProvider(AIProvider):
    name = "mock"
    model = "mock-safe-boundary"

    def analyze(self, request: AIAnalysisRequest, image: bytes) -> AIAnalysisResponse:
        return AIAnalysisResponse(
            provider=self.name,
            model=self.model,
            observation="Image received. No clinical measurement or diagnosis was produced in mock mode.",
            result_status="UNAVAILABLE",
            confidence=None,
            warnings=["Mock mode does not infer health measurements."],
            limitations=["This result is a development placeholder, not medical advice."],
            request_id=request.request_id,
        )


class GeminiProvider(AIProvider):
    name = "gemini"

    def __init__(self, api_key: str, model: str):
        if not api_key:
            raise AppError("AI_PROVIDER_ERROR", "Gemini is not configured on the backend", 503)
        self.api_key = api_key
        self.model = model

    def analyze(self, request: AIAnalysisRequest, image: bytes) -> AIAnalysisResponse:
        prompt = (
            "Return JSON only with keys provider, model, observation, result_status, confidence, "
            "warnings, limitations, request_id. This is a health decision-support system: never "
            "diagnose, never invent measurements, and use UNAVAILABLE when the image cannot support "
            "a reliable observation. result_status must be OBSERVATION, ESTIMATION, UNAVAILABLE, "
            "or WARNING. request_id must equal the supplied request id. "
            f"Supplied request id: {request.request_id}"
        )
        url = (
            f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
            f"?key={self.api_key}"
        )
        body = {
            "contents": [
                {
                    "parts": [
                        {"text": prompt},
                        {
                            "inline_data": {
                                "mime_type": request.mime_type,
                                "data": base64.b64encode(image).decode("ascii"),
                            }
                        },
                    ]
                }
            ],
            "generationConfig": {"responseMimeType": "application/json", "temperature": 0},
        }
        try:
            response = httpx.post(url, json=body, timeout=30)
            response.raise_for_status()
            payload = response.json()
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
            parsed = json.loads(text)
            result = AIAnalysisResponse.model_validate(parsed)
        except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
            raise AppError("AI_PROVIDER_ERROR", "The AI provider could not process the image", 502) from exc
        if result.request_id != request.request_id:
            raise AIOutputInvalidError("The AI response request_id did not match the request")
        return result


def get_ai_provider() -> AIProvider:
    if settings.ai_provider == "gemini":
        return GeminiProvider(settings.gemini_api_key, settings.gemini_model)
    return MockAIProvider()