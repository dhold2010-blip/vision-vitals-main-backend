from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from time import monotonic

from PIL import Image, UnidentifiedImageError


@dataclass(frozen=True)
class CameraConfig:
    width: int = 1280
    height: int = 720
    image_format: str = "jpeg"
    quality: int = 90
    autofocus_mode: str = "continuous"
    capture_timeout_seconds: float = 10.0


class CameraProvider(ABC):
    @abstractmethod
    def initialize(self) -> None:
        raise NotImplementedError

    @abstractmethod
    def capture(self, output_path: Path) -> Path:
        raise NotImplementedError

    @abstractmethod
    def validate(self, image_path: Path) -> None:
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        raise NotImplementedError


class RaspberryPiCameraProvider(CameraProvider):
    """Picamera2 adapter for Camera Module 3; the import stays Pi-only."""

    def __init__(self, config: CameraConfig):
        self.config = config
        self._camera = None

    def initialize(self) -> None:
        try:
            from picamera2 import Picamera2
        except ImportError as exc:
            raise RuntimeError("picamera2 is required on the Raspberry Pi") from exc
        self._camera = Picamera2()
        try:
            autofocus_mode = {"manual": 0, "auto": 1, "continuous": 2}[self.config.autofocus_mode]
        except KeyError as exc:
            raise ValueError("autofocus_mode must be manual, auto, or continuous") from exc
        controls = {"AfMode": autofocus_mode}
        configuration = self._camera.create_still_configuration(
            main={"size": (self.config.width, self.config.height)},
            controls=controls,
        )
        self._camera.configure(configuration)
        self._camera.start()

    def capture(self, output_path: Path) -> Path:
        if self._camera is None:
            raise RuntimeError("Camera has not been initialized")
        output_path.parent.mkdir(parents=True, exist_ok=True)
        suffix = ".png" if self.config.image_format == "png" else ".jpg"
        target = output_path.with_suffix(suffix)
        options = {"quality": self.config.quality} if suffix == ".jpg" else {}
        started = monotonic()
        self._camera.capture_file(str(target), **options)
        if monotonic() - started > self.config.capture_timeout_seconds:
            raise TimeoutError("Camera capture exceeded the configured timeout")
        self.validate(target)
        return target

    def validate(self, image_path: Path) -> None:
        if not image_path.is_file() or image_path.stat().st_size == 0:
            raise ValueError("Camera capture did not produce an image")
        try:
            with Image.open(image_path) as image:
                image.verify()
        except (UnidentifiedImageError, OSError) as exc:
            raise ValueError("Camera capture is corrupted") from exc

    def close(self) -> None:
        if self._camera is not None:
            self._camera.stop()
            self._camera.close()
            self._camera = None