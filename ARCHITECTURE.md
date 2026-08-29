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