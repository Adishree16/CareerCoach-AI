# API

## Health
`GET /api/health` returns `{"status":"ok","service":"careercoach-api"}` when the application process is running. It does not currently check PostgreSQL connectivity.

## Conventions
Place endpoints in `backend/app/api/routes/`, mount them under `/api`, validate request/response payloads with Pydantic schemas, and keep feature logic in services. Use JSON and meaningful HTTP status codes. Feature endpoints are planned, not implemented.
