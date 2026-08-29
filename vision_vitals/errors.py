from __future__ import annotations

from typing import Any


class AppError(Exception):
    def __init__(self, code: str, message: str, status_code: int = 400, details: Any = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details


class AIOutputInvalidError(AppError):
    def __init__(self, message: str = "The AI provider returned unsupported output"):
        super().__init__("AI_OUTPUT_INVALID", message, 502)