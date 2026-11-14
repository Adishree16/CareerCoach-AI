# Architecture

## Frontend
The frontend is a collection of semantic HTML pages styled with shared CSS tokens and foundations. ES modules provide a small shared shell and centralized API client. Static page navigation keeps routing explicit and avoids a client framework.

## Backend
FastAPI exposes REST routes under `/api`. Route handlers should delegate feature behavior to service modules; Pydantic schemas belong in `app/schemas`. Configuration is environment-based.

## API communication
`frontend/js/services/api.js` owns Fetch calls, JSON handling, and transport/HTTP errors. The base URL is centralized in `frontend/js/config.js`.

## Database
SQLAlchemy engine and declarative base are configured in `app/database/session.py`. PostgreSQL is the configured database. No feature tables or migrations are defined until requirements exist.

## Data flow
Browser page → API client → FastAPI router → service → (future schema/model and PostgreSQL). The health route currently responds without database access.

## Status
The shared foundation and health endpoint are implemented. Profile, recommendations, assessment, roadmap, accountability, authentication, and GitHub intelligence are planned only.
