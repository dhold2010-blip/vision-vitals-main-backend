from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class DeviceConfig:
    backend_url: str
    device_identifier: str
    device_name: str
    device_type: str = "VISION_VITALS_CAMERA"
    firmware_version: str | None = None
    software_version: str | None = None
    resolution_width: int = 1280
    resolution_height: int = 720
    image_format: str = "jpeg"
    image_quality: int = 90
    autofocus_mode: str = "continuous"
    capture_timeout_seconds: float = 10.0
    max_image_bytes: int = 10 * 1024 * 1024
    max_retries: int = 3

    @classmethod
    def from_env(cls) -> "DeviceConfig":
        return cls(
            backend_url=os.getenv("VISION_VITALS_BACKEND_URL", ""),
            device_identifier=os.getenv("VISION_VITALS_DEVICE_IDENTIFIER", ""),
            device_name=os.getenv("VISION_VITALS_DEVICE_NAME", "Vision Vitals Camera"),
            device_type=os.getenv("VISION_VITALS_DEVICE_TYPE", "VISION_VITALS_CAMERA"),
            firmware_version=os.getenv("VISION_VITALS_FIRMWARE_VERSION") or None,
            software_version=os.getenv("VISION_VITALS_SOFTWARE_VERSION") or None,
            resolution_width=int(os.getenv("VISION_VITALS_CAMERA_WIDTH", "1280")),
            resolution_height=int(os.getenv("VISION_VITALS_CAMERA_HEIGHT", "720")),
            image_format=os.getenv("VISION_VITALS_IMAGE_FORMAT", "jpeg").lower(),
            image_quality=int(os.getenv("VISION_VITALS_IMAGE_QUALITY", "90")),
            autofocus_mode=os.getenv("VISION_VITALS_AUTOFOCUS_MODE", "continuous").lower(),
            capture_timeout_seconds=float(os.getenv("VISION_VITALS_CAPTURE_TIMEOUT", "10")),
            max_image_bytes=int(os.getenv("VISION_VITALS_MAX_IMAGE_BYTES", str(10 * 1024 * 1024))),
            max_retries=int(os.getenv("VISION_VITALS_MAX_RETRIES", "3")),
        )

    def validate(self) -> None:
        if not self.backend_url.startswith("https://"):
            local_test_hosts = ("http://localhost", "http://127.0.0.1", "http://testserver")
            if not self.backend_url.startswith(local_test_hosts):
                raise ValueError("VISION_VITALS_BACKEND_URL must use HTTPS")
        if not self.device_identifier:
            raise ValueError("VISION_VITALS_DEVICE_IDENTIFIER is required")
        if self.image_format not in {"jpeg", "jpg", "png"}:
            raise ValueError("VISION_VITALS_IMAGE_FORMAT must be jpeg or png")
        if not 1 <= self.image_quality <= 100:
            raise ValueError("VISION_VITALS_IMAGE_QUALITY must be between 1 and 100")
        if self.resolution_width < 8 or self.resolution_height < 8:
            raise ValueError("Camera resolution is too small")
        if self.max_retries < 0 or self.max_retries > 5:
            raise ValueError("VISION_VITALS_MAX_RETRIES must be between 0 and 5")