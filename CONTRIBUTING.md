# Contributing

## Development

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
cp .env.example .env
alembic upgrade head
```

The default provider is `mock`, so local development does not require Gemini,
cloud storage, Redis, or a queue.

## Verification

Before opening a change, run:

```bash
ruff check .
python -m pytest -q
alembic upgrade head
```

Changes to models or persistent behavior require a new Alembic migration.
Never rewrite an applied migration. Add regression tests for ownership,
authentication, upload validation, idempotency, privacy, and device behavior
when those areas change.

## Security rules

* Never commit `.env`, credentials, API keys, database passwords, device
  secrets, or private keys.
* Never log raw image bytes, access tokens, refresh tokens, device secrets, or
  Gemini keys.
* Keep ownership checks server-derived; clients cannot submit `user_id`,
  `owner_user_id`, or role changes.
* Treat all AI output as untrusted and preserve safe failure behavior.
* Do not claim unsupported medical measurements or regulatory compliance.

Pull requests should separate implemented behavior, tested behavior, and any
requirement that still depends on external credentials or physical hardware.