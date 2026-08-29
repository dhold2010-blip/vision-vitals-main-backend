# Vision Vitals — Part 1 Main Production Backend

FastAPI backend foundation for secure accounts, sessions, private health data,
image analysis orchestration, audit events, local sensitive-image storage, and
AI provider abstraction.

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

## Security boundary

Passwords are Argon2id-hashed. Access tokens are short-lived JWTs, refresh
tokens are rotated and stored only as SHA-256 hashes, and all protected
resources derive ownership from the authenticated user. Images are stored
under generated keys and are served only through an authenticated route.
Sensitive image contents and medical details are not written to logs.

AI responses are strictly validated and explicitly distinguish observations,
estimations, warnings, limitations, and unavailable results. The backend does
not turn AI output into a diagnosis and does not fabricate measurements.

The Raspberry Pi/device integration remains intentionally outside Part 1 and
is reserved for Part 2.