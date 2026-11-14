# CareerCoach AI

CareerCoach AI — Your Personal AI Career Strategist — is planned as a career guidance platform for profile intelligence, career discovery, skill gap analysis, learning roadmaps, and accountability. The current repository contains the application foundation only; feature behavior and final page designs are planned, not implemented.

## Technology
Frontend: HTML5, CSS3, vanilla JavaScript ES modules. Backend: Python 3.11+, FastAPI, Pydantic Settings, SQLAlchemy, Alembic. Database configuration targets PostgreSQL.

## Structure
- `frontend/`: static pages, shared CSS tokens, assets, ES modules, and Fetch client.
- `backend/app/`: FastAPI app, routes, config, database setup, and feature service placeholders.
- `docs/`: architecture, API, and design system notes.

## Prerequisites
Python 3.11+ and pip. PostgreSQL is needed when database-backed features are implemented; the health endpoint works without a database server.

## Frontend
From the project root: `python -m http.server 5500 --directory frontend` then open http://localhost:5500. The API base URL defaults to `http://localhost:8000`; configure through the `api-base-url` meta tag when needed.

## Backend
From the project root: `python -m venv backend/.venv`, activate it, then `pip install -r backend/requirements.txt`. Copy `backend/.env.example` to `backend/.env` and configure `DATABASE_URL` and `FRONTEND_ORIGINS` for your environment. Run `uvicorn app.main:app --reload --app-dir backend` from the project root. Health: http://localhost:8000/api/health.

## Environment
The backend reads `backend/.env`; do not commit secrets. `DATABASE_URL` uses SQLAlchemy's PostgreSQL psycopg dialect. CORS origins are comma-separated.
