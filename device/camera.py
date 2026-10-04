from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class CameraConfig:
    width: int = 1280
    height: int = 720
    image_format: str = "jpeg"
    capture_timeout_seconds: float = 10.0


class CameraProvider(ABC):
    """Hardware-neutral capture interface; concrete camera firmware owns sensor I/O."""

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