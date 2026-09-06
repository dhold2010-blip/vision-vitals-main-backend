from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Any

from PIL import Image

from .client import DeviceClient
from .config import DeviceConfig


class MockHardwareDevice:
    """Integration-test device that talks to the real HTTP device endpoints."""

    def __init__(self, client: DeviceClient):
        self.client = client
        self.device_secret: str | None = None
        self.last_capture: dict[str, Any] | None = None

    @classmethod
    def for_backend(cls, backend_url: str) -> "MockHardwareDevice":
        config = DeviceConfig(
            backend_url=backend_url,
            device_identifier="mock-device-001",
            device_name="Mock Vision Vitals Camera",
        )
        return cls(DeviceClient(config))

    def register(self, owner_access_token: str) -> dict[str, Any]:
        data = self.client.register(owner_access_token)
        self.device_secret = data["device_secret"]
        return data

    def authenticate(self) -> dict[str, Any]:
        if not self.device_secret:
            raise RuntimeError("Register the mock device before authenticating")
        return self.client.authenticate(self.device_secret)

    def heartbeat(self) -> dict[str, Any]:
        return self.client.heartbeat()

    def capture(self, *, duplicate: bool = False, idempotency_key: str = "mock-capture-001") -> dict[str, Any]:
        with TemporaryDirectory() as directory:
            image_path = Path(directory) / "mock-capture.png"
            image = Image.new("RGB", (64, 64), (96, 120, 140))
            image.save(image_path, format="PNG")
            result = self.client.capture(image_path, idempotency_key=idempotency_key)
            self.last_capture = result
            if duplicate:
                result = self.client.capture(image_path, idempotency_key=idempotency_key)
            return result

    def sensor_reading(self, distance_mm: float = 350.0) -> dict[str, Any]:
        return self.client.sensor_reading(distance_mm, datetime.now(timezone.utc))

    def close(self) -> None:
        self.client.close()