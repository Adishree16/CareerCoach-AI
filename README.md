# CareerCoach AI

CareerCoach AI is a career-exploration application that turns a user's saved skills, projects, and structured experience into explainable career matches, a role-specific analysis, and practical roadmap guidance. Its core recommendations are deterministic: curated role data and explicit scoring rules select and order career matches. Optional generative AI can add explanations and learning guidance only after the user gives separate, current consent.

The project is intended for people exploring early-career or changing-career options, including students, fresh graduates, and experienced professionals. It helps users understand which roles align with the evidence in their profile and what they could explore next. It is a guidance tool; its scores are not hiring probabilities and its roadmap does not guarantee employment or career progression.

## Contents

- [What is implemented](#what-is-implemented)
- [User workflow](#user-workflow)
- [Technology stack](#technology-stack)
- [Architecture and data flow](#architecture-and-data-flow)
- [Recommendation engine](#recommendation-engine)
- [Optional AI enrichment and consent](#optional-ai-enrichment-and-consent)
- [Data model](#data-model)
- [Repository layout](#repository-layout)
- [Prerequisites](#prerequisites)
- [Local setup](#local-setup)
- [Configuration reference](#configuration-reference)
- [API overview](#api-overview)
- [Tests](#tests)
- [Deployment](#deployment)
- [Security and privacy](#security-and-privacy)
- [Roadmap and future work](#roadmap-and-future-work)
- [Contributing](#contributing)
- [License](#license)

## What is implemented

The live frontend is built from static HTML pages, shared CSS, and browser-native JavaScript ES modules. The backend is a FastAPI service with authenticated profile storage, a curated 22-role knowledge base, deterministic scoring, optional consent-aware AI enrichment, and PostgreSQL persistence.

In the user-facing flow, the implemented core steps are:

1. **Landing page and navigation.** The homepage describes the product and links into Career Intelligence and the roadmap experience. Additional HTML pages remain in the repository, but a page file alone does not mean that a complete corresponding workflow is implemented.
2. **Account creation and sign in.** The frontend calls the backend authentication API, which delegates authentication to Supabase Auth. The backend manages the application's session and browser cookies.
3. **Profile setup and editing.** The authenticated user can save education and career status, technical skills, projects, internship or professional experience, certifications, and additional achievements. The API scopes profile access to the verified authenticated user.
4. **Career preferences and AI privacy choice.** Career Intelligence collects a career interest and preferred work activity, plus career goal and career priority. Users can separately enable, decline, or withdraw optional AI personalization in the privacy settings. Goals and priorities are contextual; they do not add recommendation points.
5. **Career recommendations.** The backend evaluates the 22 canonical roles and returns them in knowledge-base order, along with the IDs of the top three it selected. The frontend uses that supplied order and IDs; it does not independently rank roles. AI enrichment, if consent and evidence checks allow it, can explain only those top three and cannot change their scores or order.
6. **Detailed career analysis.** The user selects a role by canonical role ID. The backend combines the saved profile, current preference selections, deterministic scoring evidence, and curated role information. Optional AI content may explain fit, strengths, areas to explore, and catalogue-backed practice ideas.
7. **Personalized career roadmap.** The roadmap page reads a canonical `role_id` from its URL and requests the authenticated role-specific roadmap. It displays deterministic role, skill, evidence, project, and career-growth information. Current-consent AI enrichment may personalize learning guidance, practice approaches, and career-growth text. If enrichment is unavailable or rejected, deterministic roadmap content remains available.

These steps do not create a day-by-day or week-by-week schedule and do not track completion. The roadmap is guidance, not a promise of employment.

## User workflow

```mermaid
flowchart TD
    A[Visit homepage] --> B[Create account or sign in]
    B --> C[Save profile]
    C --> D[Choose career interest and work activity]
    D --> E{Optional AI consent}
    E -->|Decline, withdraw, or no current grant| F[Deterministic features remain available]
    E -->|Explicit current grant| G[Allowlisted evidence may be sent for optional enrichment]
    F --> H[Evaluate 22 roles deterministically]
    G --> H
    H --> I[Show backend-selected top three and all evaluated roles]
    I --> J[Select a role]
    J --> K[Detailed Career Analysis]
    K --> L[Open role-specific roadmap]
    L --> M[Deterministic roadmap with optional validated AI guidance]
```

## Technology stack

| Area | Technology | Role in this repository |
|---|---|---|
| Browser frontend | HTML5, CSS3, Vanilla JavaScript, ES modules | Static pages, responsive presentation, client-side navigation and authenticated API requests. No frontend framework is needed to serve these pages. |
| API | Python, FastAPI, Uvicorn | HTTP API, dependency injection, request validation, middleware, lifecycle, and local ASGI serving. |
| Data validation | Pydantic and `pydantic-settings` | Request/response schemas, settings validation, and structured AI response validation. |
| Persistence | SQLAlchemy 2, PostgreSQL, Psycopg 3 | ORM models, database sessions, and PostgreSQL connectivity. |
| Schema changes | Alembic | Versioned database migrations. |
| Authentication | Supabase Auth over HTTPX | User authentication is delegated to Supabase; the backend validates and manages application sessions. |
| Session/rate-limit state | Upstash Redis REST and Upstash rate limiting | Shared, expiring session and rate-limit state for the backend. |
| Key protection | Python `cryptography` | Auth/session recovery data encryption and HMAC-based identifiers in the rate-limit/session service. |
| Career matching | Deterministic Python scoring and NetworkX | Explicit weighted evidence scoring; NetworkX models curated relationships among role, domain, skill, and skill groups. |
| Optional text enrichment | Groq and Google Gen AI SDKs | Groq is primary and Gemini fallback for consent-gated, validated explanations and guidance. Neither provider calculates or reorders recommendations. |
| Hosting configuration | Netlify, Render, Supabase, Upstash | Current production architecture referenced by the app and deployment setup. Hosting is configured outside the application source. |

The root `frontend/package.json`, `frontend/next.config.ts`, `frontend/tsconfig.json`, and `frontend/src/` are explicitly identified by `.gitignore` as an **unused Next.js frontend scaffold**. The deployed application described here is the static HTML/CSS/Vanilla JavaScript app; do not use the scaffold's `npm run` commands to serve the current product.

### Why these choices fit the current design

- **Static HTML/CSS/JavaScript** keeps the user interface framework-free and can be served by a static host. The shared API module centralizes credentialed requests and CSRF handling.
- **FastAPI and Pydantic** provide typed API boundaries and validation around Python services without moving scoring into the browser.
- **PostgreSQL, SQLAlchemy, and Alembic** provide relational persistence, explicit ownership relationships, and a migration history suitable for hosted PostgreSQL.
- **Supabase Auth** handles account authentication; the application backend controls its own protected API and session-cookie behavior.
- **Upstash Redis** supplies shared state needed by session, refresh, and rate-limit flows across backend instances rather than relying on process-local memory.
- **Deterministic scoring** makes recommendation points and ordering reproducible and auditable. LLM output is kept as optional text enrichment so generated content cannot silently alter scoring decisions.
- **NetworkX** represents curated career relationships and requirement groups; it is not a machine-learning ranking model.

## Architecture and data flow

```mermaid
flowchart LR
    Browser[Static browser app<br/>HTML / CSS / ES modules]
    API[FastAPI routes]
    Auth[Supabase Auth]
    Services[Application services]
    Scoring[Deterministic scorers<br/>22-role knowledge base]
    Graph[NetworkX career graph]
    DB[(PostgreSQL<br/>SQLAlchemy models)]
    Redis[(Upstash Redis<br/>session and rate-limit state)]
    Consent[AI consent service]
    Enrichment[Career enrichment service]
    Groq[Groq primary<br/>GPT-OSS 120B]
    Gemini[Gemini fallback<br/>Gemini 3.8 Flash]

    Browser -->|credentialed API calls + CSRF on writes| API
    API --> Auth
    API --> Services
    Services --> DB
    Services --> Scoring
    Scoring --> Graph
    API --> Redis
    Services --> Consent
    Consent --> DB
    Services --> Enrichment
    Enrichment -->|current explicit consent + allowlisted task data| Groq
    Enrichment -->|fallback after primary failure| Gemini
    Enrichment -->|validated optional content| API
    API --> Browser
```

The frontend calls the backend through `frontend/js/services/api.js`; `frontend/js/config.js` reads an `api-base-url` meta tag and otherwise uses the configured Render API origin. The API router is mounted under `/api`. Route handlers authenticate protected requests and call service-layer functions. Recommendation, analysis, and roadmap services use saved profile data and the role catalogue; AI enrichment is a separate optional step after deterministic results are built.

## Recommendation engine

The recommendation service evaluates all **22** roles in canonical knowledge-base order. The approved global allocation is:

| Scoring component | Global maximum |
|---|---:|
| Skill Alignment | 43.75 |
| Projects & Experience | 31.25 |
| Interest Alignment | 25.00 |
| **Total** | **100.00** |

For each role, component availability and the applicable maximum are tracked separately. The returned final score sums available component points; it is not a probability and should not be interpreted as a hiring likelihood. A role can have less than 100 applicable points when a component has no applicable/evaluable capacity. Unavailable evidence is not treated as proof that the user lacks a skill.

- **Skill Alignment** compares saved technical skills with applicable curated role requirements. Requirement importance has explicit points (core, important, specialised; optional requirements do not earn weighted points). Alternative groups, complementary requirements, compatible stacks, and conditional requirements are represented by the knowledge data and scorer.
- **Projects & Experience** evaluates structured projects, internships, professional experience, and eligible certification evidence against role-specific categories. It reports applicable/evaluable capacity and category evidence rather than inferring technologies from free-text descriptions.
- **Interest Alignment** uses the selected career interest and preferred work activity where the catalogue maps them to a role. Missing or unavailable mappings are represented as unavailable, not as a negative user trait.
- **Career goal and career priority** are returned as contextual preferences. They are not scoring components and do not determine top-three ties.

The backend selects up to three scoreable roles by final score descending, then applicable maximum descending, then canonical catalogue order. The API returns the selected IDs separately as `top_recommendation_role_ids` while retaining all 22 evaluated roles in canonical order. The frontend must not calculate or reorder the top three.

NetworkX's graph connects curated role, domain, skill, and skill-group nodes. Services use it to validate and query knowledge-base relationships; it complements the explicit scorers and does not itself infer a ranking.

## Optional AI enrichment and consent

The configured provider order is:

1. **Groq** — model `openai/gpt-oss-120b` (primary).
2. **Google Gemini** — model `gemini-3.8-flash` (fallback).

AI is used only for optional text enrichment: concise explanations for the deterministic top three recommendations, selected-role analysis text, and roadmap learning/practice/career-growth guidance. The enrichment service builds task-specific, allowlisted payloads from the already-computed role and relevant structured evidence. It does not receive the entire ORM profile, and it does not set scores, eligibility, role order, or recommendation ranking.

Consent is separate from login and general terms. It is stored against the authenticated user's UUID with a state, notice version, scope version, and decision timestamps. Only a current explicit grant permits an external provider call. Missing, declined, withdrawn, outdated, or unreadable consent fails closed. Users can use deterministic recommendations, analysis, and roadmap functionality without AI. Provider or validation failure also returns deterministic content. AI output passes Pydantic and application-specific grounding/safety validation before being attached as an optional `ai_enrichment` response field.

## Data model

```mermaid
erDiagram
    PROFILES ||--o{ SKILLS : has
    PROFILES ||--o{ EXPERIENCES : has
    PROFILES ||--o{ PROJECTS : has
    PROFILES ||--o{ CERTIFICATIONS : has
    PROFILES {
        uuid id PK
        uuid supabase_user_id UK
        string first_name
        string last_name
        string education_level
        string degree
        string field_of_study
        string career_status
        text achievements
    }
    SKILLS {
        uuid id PK
        uuid profile_id FK
        string skill_name
    }
    EXPERIENCES {
        uuid id PK
        uuid profile_id FK
        string experience_type
        string role
        text technologies_used
    }
    PROJECTS {
        uuid id PK
        uuid profile_id FK
        string project_name
        text technologies_used
        text project_description
    }
    CERTIFICATIONS {
        uuid id PK
        uuid profile_id FK
        string certification_name
        string issuing_organization
    }
    AI_PERSONALIZATION_CONSENTS {
        uuid user_id PK
        string consent_state
        string notice_version
        string scope_version
        datetime decision_at
        datetime granted_at
        datetime withdrawn_at
    }
```

`profiles` has a unique Supabase user UUID. Skills, experiences, projects, and certifications belong to a profile and use cascading deletion. `ai_personalization_consents` is deliberately separate and keyed directly by the verified external user UUID; it does not have a profile foreign key. The migration chain creates the profile/evidence tables, removes the obsolete `portfolio_website` column, and adds consent storage. Apply migrations to the intended database before starting a new deployment; do not point local setup commands at production unintentionally.

## Repository layout

```text
.
├── backend/
│   ├── app/
│   │   ├── api/routes/       # Health, auth, profile, recommendation, analysis, roadmap, consent routes
│   │   ├── core/             # Settings, middleware-related services, rate limiting
│   │   ├── data/             # Curated career and preference catalogues (JSON)
│   │   ├── database/         # SQLAlchemy engine, session, Base
│   │   ├── models/           # Profile/evidence and AI-consent ORM models
│   │   ├── schemas/          # Pydantic request and response models
│   │   ├── services/         # Scoring, profile, auth, analysis, roadmap, enrichment
│   │   └── main.py           # FastAPI application, middleware, lifespan, API router
│   ├── alembic/versions/     # Versioned PostgreSQL migrations
│   ├── tests/                # Python unittest modules
│   ├── requirements.txt
│   └── .env.example          # Safe local configuration template
├── docs/                     # API, architecture, and design-system documentation
├── frontend/
│   ├── assets/               # Static assets such as SVGs
│   ├── css/                  # Shared styles and page styles
│   ├── js/                   # ES modules, shared shell/API client, page behavior
│   ├── pages/                # Career Intelligence, roadmap, profile, auth, and other HTML pages
│   ├── tests/                # Node built-in frontend tests
│   └── index.html            # Static homepage
└── README.md
```

The Next.js-related files under `frontend/` are unused scaffold files, not the current static application's build system.

## Prerequisites

- Python **3.10 or newer** (the source uses Python 3.10 union type syntax).
- PostgreSQL for local persistence, or an explicitly configured compatible PostgreSQL database.
- A Supabase project for account authentication.
- Upstash Redis REST credentials for the shared rate-limit and session state used by authentication flows.
- Optional Groq and Google Gemini API credentials if AI personalization is to be enabled.
- A modern browser and a static HTTP server. Node.js is optional and used for the frontend's Node test, not to serve the HTML app.

## Local setup

### 1. Clone and enter the repository

```bash
git clone https://github.com/Adishree16/CareerCoach-AI.git
cd CareerCoach-AI
```

### 2. Create the backend environment and install dependencies

From the repository root:

```bash
python -m venv backend/.venv
```

Activate it:

```powershell
# Windows PowerShell
backend\.venv\Scripts\Activate.ps1
```

```bash
# Linux or macOS
source backend/.venv/bin/activate
```

Install the declared backend packages:

```bash
python -m pip install -r backend/requirements.txt
```

### 3. Configure the backend

```powershell
# Windows PowerShell
Copy-Item backend/.env.example backend/.env
```

```bash
# Linux or macOS
cp backend/.env.example backend/.env
```

Edit `backend/.env` locally with your own development credentials. Never commit it. At minimum, configure a reachable PostgreSQL `DATABASE_URL`, the Supabase URL and publishable key, explicit local `FRONTEND_ORIGINS`, and the Upstash/HMAC/encryption settings required for authenticated session and rate-limit operation. AI API keys are optional; without them deterministic career features remain available.

`Settings` loads `backend/.env` and standard environment variables. Process environment variables take precedence over `.env` values. Alembic imports the same application settings and uses `settings.database_url`.

### 4. Apply local database migrations

Run from the backend directory so `alembic.ini` is found:

```bash
cd backend
alembic upgrade head
```

The current migration head is `a81d6f2c9b40`. Confirm the selected `DATABASE_URL` is the local development database before running this command.

### 5. Start the API

From `backend/`:

```bash
uvicorn app.main:app --reload
```

The API defaults to `http://127.0.0.1:8000`. Interactive API docs are at `http://127.0.0.1:8000/docs`; ReDoc is at `/redoc`; the health endpoint is `/api/health`.

### 6. Serve the static frontend

Open a second terminal at the repository root:

```bash
python -m http.server 5500 --directory frontend
```

Visit `http://localhost:5500/index.html`. This avoids browser restrictions that can occur when opening modules through `file://`.

The API client reads a root API origin from `<meta name="api-base-url">`; without that tag it defaults to the current Render API origin. For local full-stack use, configure the API origin as `http://localhost:8000` in the HTML page's head (the API client appends `/api/...` itself). The current HTML pages do not include this meta tag by default, so a local browser may otherwise send requests to the configured Render origin.

## Configuration reference

Never commit actual values. The names below are the settings read by the backend's `Settings` class; some are optional in development but required for the corresponding hosted capability.

| Variable | Requirement | Purpose |
|---|---|---|
| `APP_ENV` | Set appropriately in production | Environment mode, including production cookie behavior. |
| `DATABASE_URL` | Required for persistence | SQLAlchemy PostgreSQL connection URL, using the Psycopg driver. |
| `FRONTEND_ORIGINS` | Required for browser deployment | Comma-separated explicit origins allowed by credentialed CORS. Wildcards are rejected. |
| `SUPABASE_URL` | Required for authentication | Supabase project Auth API origin. |
| `SUPABASE_PUBLISHABLE_KEY` | Required for authentication | Supabase public/publishable key used by the backend Auth client; do not substitute a service-role key. |
| `UPSTASH_REDIS_REST_URL` | Required for shared auth/session state | Upstash Redis REST endpoint. |
| `UPSTASH_REDIS_REST_TOKEN` | Required for shared auth/session state | Upstash Redis REST credential. |
| `RATE_LIMIT_HMAC_SECRET` | Required for rate-limit/session privacy | Secret used for HMAC-based identifiers. |
| `REFRESH_RECOVERY_ENCRYPTION_KEY_ID` | Required with recovery encryption | Identifier for the active refresh-recovery encryption key. |
| `REFRESH_RECOVERY_ENCRYPTION_KEY` | Required for refresh recovery | Active encryption key used by the auth/session recovery flow. |
| `REFRESH_RECOVERY_PREVIOUS_ENCRYPTION_KEY_ID` | Optional during key rotation | Identifier for a previous recovery key. |
| `REFRESH_RECOVERY_PREVIOUS_ENCRYPTION_KEY` | Optional during key rotation | Previous key accepted during configured rotation. |
| `GROQ_API_KEY` | Optional | Groq primary provider credential. |
| `GEMINI_API_KEY` | Optional | Gemini fallback provider credential. |
| `GROQ_MODEL_ID` | Optional; defaults to approved model | Groq model selector; default `openai/gpt-oss-120b`. |
| `GEMINI_MODEL_ID` | Optional; defaults to approved model | Gemini model selector; default `gemini-3.8-flash`. |
| `LLM_PROVIDER_TIMEOUT_SECONDS` | Optional; bounded setting | Provider request timeout (default 30 seconds, configured range is greater than 0 through 120). |
| `LLM_ROADMAP_MAX_OUTPUT_TOKENS` | Optional; bounded setting | Roadmap output cap (default 4096, configured range 256–8192). |

Safe template shape (placeholders only):

```dotenv
APP_ENV=development
DATABASE_URL=postgresql+psycopg://careercoach:replace-me@localhost:5432/careercoach
FRONTEND_ORIGINS=http://localhost:5500,http://127.0.0.1:5500
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_PUBLISHABLE_KEY=replace-me
UPSTASH_REDIS_REST_URL=https://your-upstash-endpoint
UPSTASH_REDIS_REST_TOKEN=replace-me
RATE_LIMIT_HMAC_SECRET=replace-with-a-long-random-secret
REFRESH_RECOVERY_ENCRYPTION_KEY_ID=primary
REFRESH_RECOVERY_ENCRYPTION_KEY=replace-with-a-valid-key
GROQ_API_KEY=
GEMINI_API_KEY=
GROQ_MODEL_ID=openai/gpt-oss-120b
GEMINI_MODEL_ID=gemini-3.8-flash
LLM_PROVIDER_TIMEOUT_SECONDS=30
LLM_ROADMAP_MAX_OUTPUT_TOKENS=4096
```

The backend also provides safe defaults for several settings, but defaults such as localhost database/CORS origins are not production configuration. Keep secrets in the hosting provider's secret/environment settings, not in source control.

## API overview

All routes are mounted under `/api`.

| Method | Path | Description |
|---|---|---|
| `GET` | `/api/health` | Basic service health response. |
| `POST` | `/api/auth/register` | Register through Supabase Auth and establish the application session. |
| `POST` | `/api/auth/login` | Authenticate and establish the application session. |
| `GET` | `/api/auth/me` | Read the current authenticated user. |
| `GET` | `/api/auth/csrf` | Obtain a CSRF token for state-changing requests. |
| `POST` | `/api/auth/refresh` | Refresh/rotate the current session. |
| `POST` | `/api/auth/logout` | End the current application session. |
| `GET` | `/api/profiles/me` | Read the authenticated user's profile. |
| `PUT` | `/api/profiles/me` | Create or update the authenticated user's profile. |
| `POST` | `/api/recommendations` | Evaluate the 22 canonical careers with the saved profile and supplied preferences. |
| `POST` | `/api/career-analysis` | Analyze one canonical role using the saved profile and preference selections. |
| `GET` | `/api/career-roadmaps/{role_id}` | Build the authenticated user's roadmap for a canonical role ID. |
| `GET` | `/api/ai-consent` | Read the authenticated user's AI personalization state. |
| `POST` | `/api/ai-consent` | Record an explicit grant or decline for the current notice/scope. |
| `POST` | `/api/ai-consent/withdraw` | Withdraw a previously granted AI consent. |

Protected routes derive user identity from verified authentication. The browser does not submit an authoritative user ID or profile ID. State-changing requests use the frontend API client's CSRF convention. See [`docs/API.md`](docs/API.md) for request/response details. Local Swagger UI is available at `/docs` after starting FastAPI.

## Tests

Backend tests use Python's standard `unittest` framework and are in `backend/tests/`. From the backend directory, run the suite with:

```bash
python -m unittest discover -s tests
```

Individual modules can be run with, for example:

```bash
python -m unittest tests.test_career_recommendations
python -m unittest tests.test_career_enrichment
python -m unittest tests.test_career_roadmaps
```

Coverage includes scoring and role ordering, profile and preference behavior, consent, authentication/rate-limit services, provider adapters, enrichment validation/fallback, career analysis, and roadmaps. Frontend's Node built-in test is run from `frontend/`:

```bash
node --test tests/api_credentials.test.cjs
```

No test results are implied by these instructions; run the commands in the environment where you plan to develop or release.

## Deployment

The configured hosting architecture is:

- **Frontend:** Netlify serves the static files in `frontend/`.
- **Backend:** Render runs the FastAPI application.
- **Database:** Supabase-hosted PostgreSQL.
- **Authentication:** Supabase Auth.
- **Shared session and rate-limit store:** Upstash Redis.
- **Optional AI providers:** Groq primary, Gemini fallback.

The frontend API origin can be set with the `api-base-url` meta tag; otherwise the current source defaults to `https://careercoach-ai-t9pe.onrender.com`. The browser sends credentialed requests, so set `FRONTEND_ORIGINS` to the exact deployed Netlify origin(s), without a wildcard. Production auth cookies require HTTPS and the production cookie configuration. Set all required backend environment variables in Render and keep provider credentials server-side. Netlify must serve the static `frontend/` directory; no frontend build is required for the Vanilla app.

Recommended release sequence:

1. Provision Supabase PostgreSQL/Auth and Upstash Redis; store credentials securely.
2. Configure Render's environment variables, including the production `DATABASE_URL`, explicit Netlify origin, Supabase Auth settings, Upstash settings, HMAC and refresh-encryption keys. Add AI provider keys only if AI personalization will be offered.
3. Deploy the backend with the ASGI command `uvicorn app.main:app --host 0.0.0.0 --port $PORT` (Render supplies `PORT`; adapt shell syntax to the platform's command field if necessary). The application requires its dependencies from `backend/requirements.txt`; use `backend` as the service root so `app.main:app` and `alembic.ini` resolve.
4. Review the migration chain and apply `alembic upgrade head` to the intended production database as a deliberate release operation. Do not run against an unverified URL.
5. Deploy the `frontend/` directory as a Netlify static site and set the API base origin to the Render service origin.
6. Verify health, login/logout, cookie delivery, profile read/write, explicit consent/withdrawal, recommendation, selected-role analysis, and roadmap fallback in the deployed environment. Confirm CORS allows the exact frontend origin and no secrets appear in logs.

No Dockerfile or checked-in Render/Netlify deployment manifest was found during this README review; hosting settings are managed in the provider dashboards unless a manifest is added later.

## Security and privacy

- Authentication is delegated to Supabase Auth; protected API identity comes from the verified session rather than a client-provided user ID.
- Session/auth cookies are configured for production cross-site use with secure attributes. Authentication tokens remain HttpOnly; CSRF protection is applied to state-changing requests. The browser API helper uses `credentials: "include"`.
- Credentialed CORS requires explicit origins; wildcard origins are rejected by settings validation.
- The backend uses Upstash-backed shared state and rate limiting with HMAC-derived identifiers and encrypted refresh-recovery data. Missing required auth/session infrastructure fails closed for those operations.
- AI processing is optional and requires a current explicit consent grant. Provider payloads are task-specific and minimize profile information. Deterministic functionality remains available without consent or when enrichment fails.
- `.gitignore` excludes `.env` files while allowing `.env.example`. Keep real keys in local ignored files or hosting secret managers only.
- Application validation and AI diagnostics are designed to avoid reflecting user-submitted data and provider output into safe diagnostic events. Production logging should still be reviewed when changing exception handling.

## Roadmap and future work

The word “Roadmap” refers to the implemented personalized guidance page and API. The repository does not establish a separate authoritative future product roadmap. Additional planned capabilities should be labeled as planned in their own product specification and must not be presented as currently implemented.

## Contributing

Contributions should preserve the deterministic scoring/ordering contract, explicit AI consent, privacy allowlists, and existing API behavior. A practical contribution flow is:

1. Open an issue describing the problem or proposed change.
2. Create a focused branch and keep unrelated changes out of the pull request.
3. Add or update tests for behavior changes and run the relevant backend/frontend checks above.
4. Document API, configuration, or migration effects in the relevant docs.
5. Submit a pull request with a concise summary and verification results.

No `CONTRIBUTING.md` policy was found in the repository; these are project-oriented suggestions, not an existing formal contribution policy.

## License

No license file or license declaration was found in the repository during this review. The project should not be assumed to be open-source licensed until its owner adds an explicit license.
