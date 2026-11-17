from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from app.api.router import api_router
from app.core.config import settings
from app.core.rate_limit import rate_limit_service
from app.services.llm_providers import llm_provider_service

MAX_API_REQUEST_BODY_BYTES = 1_048_576


def _configure_career_enrichment_logging() -> None:
    """Send safe enrichment diagnostics to Uvicorn's console exactly once."""
    logger = logging.getLogger("app.services.career_enrichment")
    if logger.handlers:
        logger.setLevel(logging.INFO)
        logger.propagate = False
        return

    console_handler = next(
        (
            handler
            for name in ("uvicorn.error", "uvicorn")
            for handler in logging.getLogger(name).handlers
            if isinstance(handler, logging.StreamHandler)
        ),
        None,
    )
    if console_handler is None:
        return
    logger.setLevel(logging.INFO)
    logger.addHandler(console_handler)
    logger.propagate = False


class RequestBodySizeLimitMiddleware:
    """Bound API request bodies while replaying accepted bytes to FastAPI."""

    def __init__(self, app, max_body_size: int) -> None:
        self.app = app
        self.max_body_size = max_body_size

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http" or not (
            scope.get("path") == "/api" or scope.get("path", "").startswith("/api/")
        ):
            await self.app(scope, receive, send)
            return

        content_lengths = [
            value.decode("latin-1").strip()
            for key, value in scope.get("headers", [])
            if key.lower() == b"content-length"
        ]
        if content_lengths:
            if len(set(content_lengths)) != 1:
                await self._reject(scope, receive, send, 400, "Invalid request size header.")
                return
            try:
                declared_length = int(content_lengths[0])
            except ValueError:
                await self._reject(scope, receive, send, 400, "Invalid request size header.")
                return
            if declared_length < 0:
                await self._reject(scope, receive, send, 400, "Invalid request size header.")
                return
            if declared_length > self.max_body_size:
                await self._reject(scope, receive, send, 413, "Request body exceeds the 1 MB limit.")
                return

        messages = []
        received_size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                messages.append(message)
                break
            if message["type"] != "http.request":
                messages.append(message)
                continue

            received_size += len(message.get("body", b""))
            if received_size > self.max_body_size:
                await self._reject(scope, receive, send, 413, "Request body exceeds the 1 MB limit.")
                return
            messages.append(message)
            if not message.get("more_body", False):
                break

        replay_index = 0

        async def replay_receive():
            nonlocal replay_index
            if replay_index < len(messages):
                message = messages[replay_index]
                replay_index += 1
                return message
            return await receive()

        await self.app(scope, replay_receive, send)

    @staticmethod
    async def _reject(scope, receive, send, status_code: int, detail: str) -> None:
        response = JSONResponse(status_code=status_code, content={"detail": detail})
        await response(scope, receive, send)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _configure_career_enrichment_logging()
    await rate_limit_service.startup()
    try:
        await llm_provider_service.startup()
        yield
    finally:
        await llm_provider_service.shutdown()
        await rate_limit_service.shutdown()


app = FastAPI(title="CareerCoach AI API", version="0.1.0", lifespan=lifespan)
# Register the size limiter first so CORS remains the outer middleware and adds
# its normal headers to 400/413 responses as well as application responses.
app.add_middleware(
    RequestBodySizeLimitMiddleware,
    max_body_size=MAX_API_REQUEST_BODY_BYTES,
)
app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_credentials=True, allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"], allow_headers=["*"])
app.include_router(api_router, prefix="/api")


_SAFE_VALIDATION_FIELDS = {
    "email",
    "password",
    "basicInformation",
    "firstName",
    "lastName",
    "educationLevel",
    "educationLevelOther",
    "degree",
    "degreeOther",
    "fieldOfStudy",
    "fieldOfStudyOther",
    "careerSkills",
    "careerStatus",
    "technicalSkills",
    "internshipExperience",
    "professionalExperience",
    "company",
    "role",
    "duration",
    "responsibilities",
    "technologies",
    "additionalInformation",
    "projects",
    "name",
    "description",
    "certifications",
    "organization",
    "achievements",
}


def _safe_validation_message(error_type: str) -> tuple[str, str]:
    if error_type == "missing":
        return "required", "This field is required."
    if error_type in {"string_too_short", "string_too_long", "too_short", "too_long"}:
        return "length", "The value does not meet the required length."
    if error_type in {"literal_error", "enum"}:
        return "invalid_choice", "Choose one of the available options."
    if error_type == "extra_forbidden":
        return "unsupported_field", "This field is not supported."
    if error_type.startswith("url_"):
        return "invalid_url", "Enter a valid URL."
    if error_type == "value_error":
        return "invalid_value", "Review this value and correct it."
    return "invalid_format", "Enter a value in the required format."


@app.exception_handler(RequestValidationError)
async def request_validation_exception_handler(
    request: Request, exc: RequestValidationError
) -> JSONResponse:
    """Return actionable validation errors without reflecting submitted values."""
    safe_errors = []
    for error in exc.errors()[:50]:
        location = error.get("loc", ())
        safe_parts = []
        for part in location:
            if isinstance(part, int):
                if safe_parts:
                    safe_parts[-1] = f"{safe_parts[-1]}[{part}]"
            elif isinstance(part, str) and part in _SAFE_VALIDATION_FIELDS:
                safe_parts.append(part)

        code, message = _safe_validation_message(str(error.get("type", "")))
        safe_errors.append(
            {
                "field": ".".join(safe_parts) if safe_parts else "request",
                "code": code,
                "message": message,
            }
        )

    return JSONResponse(
        status_code=422,
        content={
            "detail": "Some submitted information needs attention.",
            "errors": safe_errors,
        },
    )
