# Deployment

## Required configuration

Production must provide distinct, randomly generated values for
`JWT_SECRET` and `JWT_REFRESH_SECRET`, a private `DATABASE_URL`, and a
private `STORAGE_PATH` or a separately implemented `StorageProvider`. Use
`AI_PROVIDER=mock` until a server-side Gemini key is intentionally configured.

Important settings include:

* `TRUSTED_HOSTS`: exact hostnames accepted by the application.
* `CORS_ORIGINS`: exact browser origins; do not use a wildcard with credentials.
* `MAX_UPLOAD_SIZE_MB`, `MAX_IMAGE_WIDTH`, `MAX_IMAGE_HEIGHT`, and
  `MAX_IMAGE_PIXELS`: upload safety limits.
* `DEVICE_HEARTBEAT_TIMEOUT_SECONDS`: online/offline threshold.
* `DEVICE_MIN_SHARPNESS`: optional calibrated blur threshold for hardware
  captures; zero disables the sharpness heuristic.
* authentication, password, and device rate limits.

Do not put secrets in the image, repository, OpenAPI examples, device
configuration committed to source control, or logs.

## Docker Compose

Create a private `.env` file from `.env.example`, replace every placeholder,
and start:

```bash
docker compose up --build
curl http://127.0.0.1:8000/api/v1/health/live
```

Compose requires `POSTGRES_PASSWORD`, `JWT_SECRET`, and
`JWT_REFRESH_SECRET`; it has no insecure fallback for those values. The API
container runs `alembic upgrade head` before Uvicorn, uses a non-root user, and
has a liveness health check. PostgreSQL data and private image storage are
named volumes.

## Non-container deployment

1. Build a Python 3.11+ environment and install `requirements.txt`.
2. Provision PostgreSQL and a private storage directory.
3. Set production environment variables through the platform secret manager.
4. Run `alembic upgrade head`.
5. Run Uvicorn behind a TLS-terminating reverse proxy:

   ```bash
   uvicorn vision_vitals.main:app --host 0.0.0.0 --port 8000
   ```

6. Route `/api/v1/health/live` to the platform liveness probe and
   `/api/v1/health/ready` to the readiness probe.
7. Back up PostgreSQL and private image storage according to the approved
   retention policy. Deleting an account or analysis removes its local image
   before the database row is committed.

The in-process rate limiter is safe for a single instance only. A multi-instance
deployment needs a shared implementation behind the same limiter interface
before relying on rate limits across replicas.

## Hardware

ESP32 firmware connects only to the HTTPS API with certificate verification
enabled. It never receives Gemini, database, JWT-signing, or administrator
credentials. Provision the one-time device secret into protected device
storage, rotate it when a device is transferred, and revoke the device when it
is lost. Hardware captures are JPEG-only and follow the device API contract in
`API.md`; the optional VL53L0X range is positioning metadata, not a medical
measurement.

See `firmware/esp32_controller/README.md` for the ESP32 DevKit, ESP32-CAM
(OV2640), capture button, LED, and optional sensor setup. Before deployment,
set a trusted backend root CA in the untracked firmware secrets header. The
firmware intentionally has no plain-HTTP production fallback and never disables
TLS verification.