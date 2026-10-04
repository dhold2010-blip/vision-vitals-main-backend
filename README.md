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
alembic upgrade head
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
tests. The application does not create or mutate database tables at startup;
run `alembic upgrade head` before starting it. Secrets must be supplied through
environment configuration; no real secret is committed here.

## Device and camera integration

Users register a device through `POST /api/v1/devices/register`. The response
contains a device secret exactly once. Provision it into the ESP32 controller's
protected local storage; never put it in source control or the ESP32-CAM image.
The controller authenticates with that secret, receives a short-lived device
session token, and sends captures with `X-Device-Session` and an
`Idempotency-Key` header.

Supported device endpoints:

- `POST /api/v1/devices/register`
- `GET /api/v1/devices` and `GET /api/v1/devices/{device_id}`
- `DELETE /api/v1/devices/{device_id}` (revokes the device and sessions)
- `POST /api/v1/devices/authenticate`
- `POST /api/v1/devices/{device_id}/authenticate`
- `POST /api/v1/devices/{device_id}/heartbeat`
- `GET /api/v1/devices/{device_id}/status`
- `POST /api/v1/devices/{device_id}/capture`
- `POST /api/v1/devices/{device_id}/sensor-readings`

The hardware path is an input/control device, not an AI computer:

```text
ESP32-CAM (OV2640) → ESP32 DevKit / ESP32-WROOM-32
  → Wi-Fi + verified HTTPS → device API → image quality checks
  → VisionAnalysisService → selected AIProvider
```

The ESP32-CAM supplies JPEG frames; the main controller handles Wi-Fi,
heartbeat, the physical capture button, optional VL53L0X positioning readings,
and controlled illumination/status LEDs. `firmware/esp32_controller/` contains
the controller reference firmware and UART camera-frame contract. The
`device/` Python package remains a hardware-neutral API client and mock
integration path for local testing; the Arduino reference firmware is under
`firmware/esp32_controller/`.

Hardware captures accept JPEG only, are size-bounded and decoded server-side,
and store safe camera metadata plus each capture lifecycle transition in the
audit log. Basic framing validation checks a plausible aspect ratio; exposure
is checked, and a calibrated blur threshold can be enabled with
`DEVICE_MIN_SHARPNESS` (zero disables the heuristic). This is not face or
subject detection. The backend does not trust image dimensions or ownership values
supplied by the client. It derives dimensions from the decoded JPEG and
ownership from the authenticated device session, then sends the capture through
the same `VisionAnalysisService` used by app camera and upload requests.

Device status is derived from the last heartbeat: `REGISTERED` before the first
heartbeat, `ONLINE` while the heartbeat is within
`DEVICE_HEARTBEAT_TIMEOUT_SECONDS` (five minutes by default), `OFFLINE` after
that timeout, `ERROR` as a reserved operational state, and `REVOKED` after
owner revocation. The optional VL53L0X value is stored only as a positioning
distance in millimetres; it is not a medical measurement.

### ESP32 setup

See [`firmware/esp32_controller/README.md`](firmware/esp32_controller/README.md)
for the board wiring, Arduino dependencies, firmware provisioning, and camera
UART frame format. Production controller-to-backend traffic must use HTTPS with
certificate validation enabled.

For a backend-only smoke test, use `device.MockHardwareDevice` against a local
FastAPI server with `AI_PROVIDER=mock`; this exercises registration,
authentication, heartbeat, JPEG capture, the shared analysis pipeline, and
sensor readings without physical boards or Gemini credentials.

## Security boundary

Passwords are Argon2id-hashed. Access tokens are short-lived JWTs, refresh
tokens are rotated and stored only as SHA-256 hashes, and all protected
resources derive ownership from the authenticated user. Images are stored
under generated keys and are served only through an authenticated route.
Sensitive image contents and medical details are not written to logs.

AI responses are strictly validated and explicitly distinguish observations,
estimations, warnings, limitations, and unavailable results. The backend does
not turn AI output into a diagnosis and does not fabricate measurements.

ESP32 firmware never calls Gemini directly. Gemini credentials, database
credentials, and JWT signing secrets remain server-side; the device receives
only a short-lived session and the analysis result returned by the backend.

See [API.md](API.md), [ARCHITECTURE.md](ARCHITECTURE.md), [SECURITY.md](SECURITY.md),
[DEPLOYMENT.md](DEPLOYMENT.md), and [CONTRIBUTING.md](CONTRIBUTING.md) for the
complete operational contract.