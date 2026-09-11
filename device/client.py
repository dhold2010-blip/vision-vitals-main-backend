from __future__ import annotations

import time
import uuid
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from PIL import Image, UnidentifiedImageError

from .config import DeviceConfig


@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int = 3
    base_delay_seconds: float = 0.5


class DeviceClient:
    """HTTPS-only client. Device credentials are accepted at runtime, never embedded."""

    def __init__(
        self,
        config: DeviceConfig,
        *,
        http_client: httpx.Client | None = None,
        retry_policy: RetryPolicy | None = None,
    ):
        config.validate()
        self.config = config
        self.client = http_client or httpx.Client(base_url=config.backend_url, timeout=30, verify=True)
        self.retry_policy = retry_policy or RetryPolicy(config.max_retries)
        self.device_id: str | None = None
        self.session_token: str | None = None

    def close(self) -> None:
        self.client.close()

    def register(self, owner_access_token: str) -> dict[str, Any]:
        response = self.client.post(
            "/api/v1/devices/register",
            json={
                "device_identifier": self.config.device_identifier,
                "device_name": self.config.device_name,
                "device_type": self.config.device_type,
                "firmware_version": self.config.firmware_version,
                "software_version": self.config.software_version,
            },
            headers={"Authorization": f"Bearer {owner_access_token}"},
        )
        payload = self._expect(response)
        data = payload["data"]
        self.device_id = data["id"]
        return data

    def authenticate(self, device_secret: str) -> dict[str, Any]:
        if not self.device_id:
            raise RuntimeError("Register the device before authenticating")
        response = self.client.post(
            f"/api/v1/devices/{self.device_id}/authenticate",
            json={"device_secret": device_secret},
        )
        payload = self._expect(response)
        data = payload["data"]
        self.session_token = data["device_session_token"]
        return data

    def heartbeat(self) -> dict[str, Any]:
        return self._device_request(
            "POST",
            f"/api/v1/devices/{self._require_device_id()}/heartbeat",
            json={
                "software_version": self.config.software_version,
                "firmware_version": self.config.firmware_version,
            },
        )["data"]

    def capture(self, image_path: Path, idempotency_key: str | None = None) -> dict[str, Any]:
        self._validate_local_image(image_path)
        key = idempotency_key or str(uuid.uuid4())
        content = image_path.read_bytes()
        if len(content) > self.config.max_image_bytes:
            raise ValueError("Image exceeds the configured device limit")
        mime_type = "image/png" if image_path.suffix.lower() == ".png" else "image/jpeg"
        return self._device_request(
            "POST",
            f"/api/v1/devices/{self._require_device_id()}/capture",
            files={"image": (image_path.name, content, mime_type)},
            data={"capture_type": "camera"},
            headers={"Idempotency-Key": key},
        )["data"]

    def sensor_reading(
        self, distance_mm: float, timestamp: datetime, capture_id: str | None = None
    ) -> dict[str, Any]:
        payload = {
            "sensor_type": "distance",
            "value": distance_mm,
            "unit": "mm",
            "quality": "VALID",
            "timestamp": timestamp.isoformat(),
        }
        if capture_id:
            payload["capture_id"] = capture_id
        return self._device_request(
            "POST",
            f"/api/v1/devices/{self._require_device_id()}/sensor-readings",
            retryable=False,
            json=payload,
        )["data"]

    def _device_request(self, method: str, path: str, **kwargs: Any) -> dict[str, Any]:
        token = self.session_token
        if not token:
            raise RuntimeError("Authenticate the device before making device requests")
        headers = dict(kwargs.pop("headers", {}))
        retryable = kwargs.pop("retryable", True)
        headers["X-Device-Session"] = token
        transient = {408, 425, 429, 500, 502, 503, 504}
        for attempt in range(self.retry_policy.max_retries + 1):
            try:
                response = self.client.request(method, path, headers=headers, **kwargs)
            except httpx.TransportError:
                if not retryable or attempt >= self.retry_policy.max_retries:
                    raise
                time.sleep(self.retry_policy.base_delay_seconds * (2**attempt))
                continue
            if retryable and response.status_code in transient and attempt < self.retry_policy.max_retries:
                time.sleep(self.retry_policy.base_delay_seconds * (2**attempt))
                continue
            return self._expect(response)
        raise RuntimeError("Request retry loop exhausted")

    def _expect(self, response: httpx.Response) -> dict[str, Any]:
        if response.is_error:
            if response.status_code in {401, 403}:
                raise RuntimeError("Device authentication is required or invalid")
            if response.status_code == 422:
                raise ValueError("Device request failed validation")
            response.raise_for_status()
        return response.json()

    def _require_device_id(self) -> str:
        if not self.device_id:
            raise RuntimeError("The device is not registered")
        return self.device_id

    def _validate_local_image(self, image_path: Path) -> None:
        if not image_path.is_file() or image_path.stat().st_size == 0:
            raise ValueError("Image does not exist or is empty")
        try:
            with Image.open(image_path) as image:
                image.verify()
        except (UnidentifiedImageError, OSError) as exc:
            raise ValueError("Image is corrupted or unsupported") from exc