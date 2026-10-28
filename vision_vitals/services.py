from __future__ import annotations

from sqlalchemy.orm import Session

from .ai import AIProvider
from .errors import AIOutputInvalidError, AppError
from .models import Analysis, AnalysisImage, AnalysisResult, AuditEvent
from .pose import PoseCapture, camera_metrics
from .supplemental import MeasurementInputs, supplemental_metrics
from .quality import ImageQualityService
from .schemas import AIAnalysisRequest, AIAnalysisResponse
from .storage import StoredImage, StorageProvider


class VisionAnalysisService:
    def __init__(self, db: Session, storage: StorageProvider, provider: AIProvider | None = None):
        self.db = db
        self.storage = storage
        self.provider = provider
        self.quality = ImageQualityService()

    def create(
        self,
        user_id: str,
        request_id: str,
        image: bytes,
        filename: str,
        mime_type: str,
        image_source: str = "UPLOAD",
        pose_capture: PoseCapture | None = None,
        measurement_inputs: MeasurementInputs | None = None,
    ) -> Analysis:
        quality = self.quality.validate_or_raise(image)
        geometry = camera_metrics(pose_capture, quality.width, quality.height)
        supplemental = supplemental_metrics(measurement_inputs, pose_capture, quality.width, quality.height)
        analysis = Analysis(user_id=user_id, request_id=request_id, status="PROCESSING")
        self.db.add(analysis)
        self.db.flush()
        stored: StoredImage | None = None
        try:
            stored = self.storage.save_image(image, filename, mime_type)
            analysis.image = AnalysisImage(
                analysis_id=analysis.id,
                storage_key=stored.storage_key,
                original_filename=stored.original_filename,
                mime_type=stored.mime_type,
                size_bytes=stored.size_bytes,
                sha256=stored.sha256,
                source=image_source,
            )
            ai_request = AIAnalysisRequest(
                request_id=request_id, mime_type=mime_type, image_sha256=stored.sha256
            )
            try:
                if self.provider is None:
                    raise AppError("AI_PROVIDER_ERROR", "No AI provider is configured", 503)
                result: AIAnalysisResponse = self.provider.analyze(ai_request, image)
                if result.request_id != request_id:
                    raise AIOutputInvalidError("The AI response request_id did not match the request")
            except AppError:
                if geometry.status == "UNAVAILABLE" and not any(m.value is not None for m in supplemental.metrics):
                    raise
                # Numeric geometry is independent of the optional generative description.
                result = AIAnalysisResponse(
                    provider="camera_geometry", model="mediapipe-pose-landmarker-lite-v1",
                    observation="Recorded measurements are available below. The photo description service is temporarily unavailable.",
                    result_status="ESTIMATION", confidence=None,
                    warnings=["PHOTO_DESCRIPTION_UNAVAILABLE"],
                    limitations=["No clinical measurements or diagnosis were produced."],
                    request_id=request_id,
                )
            analysis.result = AnalysisResult(
                analysis_id=analysis.id,
                provider=result.provider,
                model=result.model,
                observation=result.observation,
                result_status=result.result_status,
                confidence=result.confidence,
                warnings=result.warnings,
                limitations=result.limitations,
                visual_assessment=(
                    result.visual_assessment.model_dump() if result.visual_assessment else None
                ),
                camera_metrics=geometry.model_dump(),
                supplemental_metrics=supplemental.model_dump(),
                request_id=result.request_id,
            )
            analysis.status = "COMPLETED"
            self.db.add(
                AuditEvent(
                    user_id=user_id,
                    action="ANALYSIS_CREATED",
                    resource_type="analysis",
                    resource_id=analysis.id,
                    request_id=request_id,
                    metadata_json={"provider": result.provider},
                )
            )
            self.db.commit()
            self.db.refresh(analysis)
            return analysis
        except Exception as exc:
            if stored:
                self.storage.delete(stored.storage_key)
            self.db.rollback()
            if isinstance(exc, AppError):
                raise
            raise AppError("AI_PROVIDER_ERROR", "Analysis processing failed", 502) from exc

    def delete(self, analysis: Analysis, user_id: str, request_id: str) -> None:
        if analysis.user_id != user_id:
            raise AppError("RESOURCE_FORBIDDEN", "You cannot access this analysis", 403)
        if analysis.image:
            self.storage.delete(analysis.image.storage_key)
        self.db.add(
            AuditEvent(
                user_id=user_id,
                action="ANALYSIS_DELETED",
                resource_type="analysis",
                resource_id=analysis.id,
                request_id=request_id,
                metadata_json={},
            )
        )
        self.db.delete(analysis)
        self.db.commit()
