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

### Raspberry Pi setup

The client is a Python HTTP client and does not install backend, database, or
Gemini dependencies on the Pi. On Raspberry Pi OS Bookworm:

1. Install Raspberry Pi OS, enable the camera in the supported `rpicam`
   stack, and update the system:

   ```bash
   sudo apt update && sudo apt full-upgrade -y
   sudo apt install -y python3-venv python3-picamera2 python3-pil python3-httpx
   rpicam-hello --timeout 5000
   ```

2. Copy the `device/` package to the Pi, create a virtual environment if the
   distribution packages are not being used, and configure only runtime values:

   ```bash
   python3 -m venv .venv
   . .venv/bin/activate
   pip install httpx Pillow
   export VISION_VITALS_BACKEND_URL=https://api.example.com
   export VISION_VITALS_DEVICE_IDENTIFIER=pi-unique-identifier
   export VISION_VITALS_DEVICE_NAME="Vision Vitals Camera"
   ```

3. Register the device once using an owner access token, store the returned
   device secret in a root-readable local environment file, then authenticate
   the device. Do not put that secret in source control.

4. Use `RaspberryPiCameraProvider` with a `CameraConfig`, call
   `initialize()`, `capture()`, and `close()`. The client validates the image
   locally; the backend validates it again.

5. For the optional VL53L0X, install the CircuitPython dependencies supported
   by the Pi image (`board`, `busio`, and `adafruit_vl53l0x`), wire the sensor to
   I2C, call `initialize()`, and submit `read_distance_mm()`. This value is
   positioning metadata only.

6. Test the backend connection with the device client's `heartbeat()` and
   `capture()` methods. For autostart, run the client from a systemd service
   that reads an `EnvironmentFile` with mode `0600`; the repository does not
   ship a service file because deployment paths and the device secret are
   installation-specific.

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

See [API.md](API.md), [ARCHITECTURE.md](ARCHITECTURE.md), [SECURITY.md](SECURITY.md),
[DEPLOYMENT.md](DEPLOYMENT.md), and [CONTRIBUTING.md](CONTRIBUTING.md) for the
complete operational contract.