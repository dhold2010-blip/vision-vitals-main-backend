# Vision Vitals — Main Production Backend

FastAPI backend foundation for secure accounts, sessions, private health data,
image analysis orchestration, audit events, local sensitive-image storage, AI
provider abstraction, and secure external-camera/device integration.

## Run locally

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
uvicorn vision_vitals.main:app --reload
```

The default `AI_PROVIDER=mock` mode runs without Gemini credentials, cloud
storage, Redis, or a queue. The API is available under `/api/v1`; interactive
OpenAPI documentation is at `/docs`.

## Database and Docker

```bash
alembic upgrade head
docker compose up --build
```

PostgreSQL is the Docker database. SQLite is supported for development and
tests. Secrets must be supplied through environment configuration; no real
secret is committed here.

## Device and camera integration

Users register a device through `POST /api/v1/devices/register`. The response
contains a device secret exactly once; store it on the Raspberry Pi through its
local device configuration, never in source control. The Pi authenticates with
that secret, receives a short-lived device session token, and sends captures
with `X-Device-Session` and an `Idempotency-Key` header.

Supported device endpoints:

- `POST /api/v1/devices/register`
- `GET /api/v1/devices` and `GET /api/v1/devices/{device_id}`
- `DELETE /api/v1/devices/{device_id}` (revokes the device and sessions)
- `POST /api/v1/devices/{device_id}/authenticate`
- `POST /api/v1/devices/{device_id}/heartbeat`
- `GET /api/v1/devices/{device_id}/status`
- `POST /api/v1/devices/{device_id}/capture`
- `POST /api/v1/devices/{device_id}/sensor-readings`

The `device/` directory is a separate Raspberry Pi client. It uses Picamera2
for Camera Module 3, performs local image checks, requires HTTPS outside local
test hosts, retries only transient failures with bounded exponential backoff,
and does not contain backend or Gemini credentials. The backend repeats all
upload validation and sends hardware images through the same
`VisionAnalysisService` used by app uploads.

Device status is derived from the last heartbeat: `REGISTERED` before the first
heartbeat, `ONLINE` while the heartbeat is within
`DEVICE_HEARTBEAT_TIMEOUT_SECONDS` (five minutes by default), `OFFLINE` after
that timeout, and `REVOKED` after owner revocation. The optional VL53L0X value
is stored only as a positioning distance in millimetres; it is not a medical
measurement.

## Security boundary

Passwords are Argon2id-hashed. Access tokens are short-lived JWTs, refresh
tokens are rotated and stored only as SHA-256 hashes, and all protected
resources derive ownership from the authenticated user. Images are stored
under generated keys and are served only through an authenticated route.
Sensitive image contents and medical details are not written to logs.

AI responses are strictly validated and explicitly distinguish observations,
estimations, warnings, limitations, and unavailable results. The backend does
not turn AI output into a diagnosis and does not fabricate measurements.

Raspberry Pi captures never call Gemini directly. Gemini credentials remain
server-side, while the device receives only the analysis result returned by
the backend.