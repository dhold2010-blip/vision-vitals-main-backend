# Architecture

`vision_vitals/api.py` contains thin HTTP adapters. Authentication and
authorization are FastAPI dependencies; database access uses SQLAlchemy
models/session boundaries; `VisionAnalysisService` owns the analysis pipeline;
`AIProvider` and `StorageProvider` keep external systems behind interfaces.
Database schema changes are applied by Alembic; application startup does not
silently create or rewrite tables.

The image pipeline is:

`request → authentication → authorization → validation → image validation →
provider → strict output validation → safety metadata → persistence → response`

Every analysis, image, metric, and result is scoped to the authenticated user.
The current local storage implementation can be replaced by an object-storage
provider without changing the API or domain orchestration.

The user session boundary uses short-lived access JWTs plus rotated refresh
sessions. Device registration returns a high-entropy secret once; the
backend stores only its hash and exchanges it for a short-lived device session.
Owner checks are performed from database relationships, never from client
supplied ownership fields.

Part 2 adds a device boundary in front of the same pipeline:

`device session → device ownership → upload validation → image quality →
VisionAnalysisService → AIProvider`

`Device`, `DeviceSession`, `DeviceCapture`, and `SensorReading` are backend
models. The Raspberry Pi in `device/` is an HTTPS input client only; it does
not access the database or AI provider. `DeviceCapture` owns idempotency and
server-controlled lifecycle status, while `AnalysisImage.source` distinguishes
`APP_CAMERA`, `HARDWARE_CAMERA`, and `UPLOAD`.

Both app uploads and hardware captures call `ImageQualityService`, then
`VisionAnalysisService`, then the selected `AIProvider`, then strict
`AIAnalysisResponse` validation before persistence. Mock mode returns an
explicit unavailable result and does not invent medical measurements.