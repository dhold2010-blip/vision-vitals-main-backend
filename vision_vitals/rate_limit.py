from __future__ import annotations

import threading
import time
from collections import defaultdict, deque

from .errors import AppError


class RateLimiter:
    """Small in-process limiter; the interface can later be backed by Redis."""

    def __init__(self, window_seconds: int = 60):
        self.window_seconds = window_seconds
        self._events: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def check(self, key: str, limit: int) -> None:
        now = time.monotonic()
        with self._lock:
            events = self._events[key]
            cutoff = now - self.window_seconds
            while events and events[0] <= cutoff:
                events.popleft()
            if len(events) >= limit:
                raise AppError(
                    "RATE_LIMITED",
                    "Too many device requests; try again later",
                    429,
                    {"retry_after_seconds": self.window_seconds},
                )
            events.append(now)


device_rate_limiter = RateLimiter()