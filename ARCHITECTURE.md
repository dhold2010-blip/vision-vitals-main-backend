# Architecture

`vision_vitals/api.py` contains thin HTTP adapters. Authentication and
authorization are FastAPI dependencies; database access uses SQLAlchemy
models/session boundaries; `VisionAnalysisService` owns the analysis pipeline;
`AIProvider` and `StorageProvider` keep external systems behind interfaces.

The image pipeline is:

`request → authentication → authorization → validation → image validation →
provider → strict output validation → safety metadata → persistence → response`

Every analysis, image, metric, and result is scoped to the authenticated user.
The current local storage implementation can be replaced by an object-storage
provider without changing the API or domain orchestration.

Part 2 adds a device boundary in front of the same pipeline:

`device session → device ownership → upload validation → image quality →
VisionAnalysisService → AIProvider`

`Device`, `DeviceSession`, `DeviceCapture`, and `SensorReading` are backend
models. The Raspberry Pi in `device/` is an HTTPS input client only; it does
not access the database or AI provider. `DeviceCapture` owns idempotency and
server-controlled lifecycle status, while `AnalysisImage.source` distinguishes
`APP_CAMERA`, `HARDWARE_CAMERA`, and `UPLOAD`.