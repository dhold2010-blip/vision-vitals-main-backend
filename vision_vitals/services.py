from __future__ import annotations

from sqlalchemy.orm import Session

from .ai import AIProvider
from .errors import AIOutputInvalidError, AppError
from .models import Analysis, AnalysisImage, AnalysisResult, AuditEvent
from .schemas import AIAnalysisRequest, AIAnalysisResponse
from .storage import StoredImage, StorageProvider


class VisionAnalysisService:
    def __init__(self, db: Session, storage: StorageProvider, provider: AIProvider | None = None):
        self.db = db
        self.storage = storage
        self.provider = provider

    def create(
        self, user_id: str, request_id: str, image: bytes, filename: str, mime_type: str
    ) -> Analysis:
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
            )
            ai_request = AIAnalysisRequest(
                request_id=request_id, mime_type=mime_type, image_sha256=stored.sha256
            )
            if self.provider is None:
                raise AppError("AI_PROVIDER_ERROR", "No AI provider is configured", 503)
            result: AIAnalysisResponse = self.provider.analyze(ai_request, image)
            if result.request_id != request_id:
                raise AIOutputInvalidError("The AI response request_id did not match the request")
            analysis.result = AnalysisResult(
                analysis_id=analysis.id,
                provider=result.provider,
                model=result.model,
                observation=result.observation,
                result_status=result.result_status,
                confidence=result.confidence,
                warnings=result.warnings,
                limitations=result.limitations,
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