from __future__ import annotations

import io
from dataclasses import dataclass

from PIL import Image, ImageFilter, ImageStat, UnidentifiedImageError

from .config import settings
from .errors import AppError


@dataclass(frozen=True)
class ImageQualityResult:
    valid: bool
    reasons: tuple[str, ...] = ()
    width: int | None = None
    height: int | None = None


class ImageQualityService:
    """Conservative, explainable checks before an image enters analysis."""

    def validate(
        self,
        content: bytes,
        *,
        enforce_exposure: bool = False,
        expected_format: str | None = None,
    ) -> ImageQualityResult:
        try:
            with Image.open(io.BytesIO(content)) as image:
                width, height = image.size
                if expected_format and image.format != expected_format:
                    raise AppError("UPLOAD_INVALID", "The image format is not supported", 422)
                if (
                    width > settings.max_image_width
                    or height > settings.max_image_height
                    or width * height > settings.max_image_pixels
                ):
                    return ImageQualityResult(
                        False, ("RESOLUTION_TOO_LARGE",), width=width, height=height
                    )
                image.load()
                if width < settings.device_min_image_width or height < settings.device_min_image_height:
                    return ImageQualityResult(
                        False, ("LOW_RESOLUTION",), width=width, height=height
                    )
                if enforce_exposure:
                    aspect_ratio = width / height
                    if aspect_ratio < 0.5 or aspect_ratio > 2.0:
                        return ImageQualityResult(
                            False, ("UNUSUAL_FRAMING",), width=width, height=height
                        )
                    grayscale = image.convert("L")
                    mean = ImageStat.Stat(grayscale).mean[0]
                    if mean < 8:
                        return ImageQualityResult(
                            False, ("TOO_DARK",), width=width, height=height
                        )
                    if mean > 250:
                        return ImageQualityResult(
                            False, ("TOO_BRIGHT",), width=width, height=height
                        )
                    if settings.device_min_sharpness > 0:
                        laplacian = grayscale.filter(
                            ImageFilter.Kernel(
                                (3, 3),
                                [0, 1, 0, 1, -4, 1, 0, 1, 0],
                                scale=1,
                                offset=128,
                            )
                        )
                        sharpness = ImageStat.Stat(laplacian).var[0]
                        if sharpness < settings.device_min_sharpness:
                            return ImageQualityResult(
                                False, ("BLUR_DETECTED",), width=width, height=height
                            )
                return ImageQualityResult(True, width=width, height=height)
        except (
            Image.DecompressionBombError,
            UnidentifiedImageError,
            OSError,
            ValueError,
        ) as exc:
            raise AppError("UPLOAD_INVALID", "The image is corrupted or unsupported", 422) from exc

    def validate_or_raise(
        self,
        content: bytes,
        *,
        enforce_exposure: bool = False,
        expected_format: str | None = None,
    ) -> ImageQualityResult:
        result = self.validate(
            content,
            enforce_exposure=enforce_exposure,
            expected_format=expected_format,
        )
        if not result.valid:
            raise AppError(
                "IMAGE_RECAPTURE_REQUIRED",
                "The image quality is insufficient; recapture is required",
                422,
                {"reasons": list(result.reasons)},
            )
        return result