# Security notes

- Set long, different `JWT_SECRET` and `JWT_REFRESH_SECRET` values in
  production. The application fails fast in production when they are absent.
- Keep `GEMINI_API_KEY` server-side only. It is never returned by the API or
  written to application logs.
- Put the API behind TLS and a trusted reverse proxy in deployment.
- Device clients must use HTTPS in production; the Raspberry Pi never receives
  Gemini, database, JWT-signing, or administrative credentials.
- Device secrets and session tokens are stored only as SHA-256 hashes on the
  backend. Device sessions expire, can be revoked immediately, and captures
  require both an active device session and an idempotency key.
- Device ownership is always derived from the authenticated user that
  registered the device. Path IDs and request bodies cannot change ownership.
- Image uploads are decoded and validated server-side, stored under generated
  private keys, and never logged or exposed as public URLs.
- The in-process device rate limiter is intentionally abstracted so a shared
  Redis implementation can replace it for multi-instance deployments.
- Use a managed secret store and a managed PostgreSQL instance for production.
- The project does not claim HIPAA, GDPR, DPDP Act, medical-device, or other
  regulatory compliance. Those claims require independent verification.