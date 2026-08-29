# Security notes

- Set long, different `JWT_SECRET` and `JWT_REFRESH_SECRET` values in
  production. The application fails fast in production when they are absent.
- Keep `GEMINI_API_KEY` server-side only. It is never returned by the API or
  written to application logs.
- Put the API behind TLS and a trusted reverse proxy in deployment.
- Use a managed secret store and a managed PostgreSQL instance for production.
- The project does not claim HIPAA, GDPR, DPDP Act, medical-device, or other
  regulatory compliance. Those claims require independent verification.