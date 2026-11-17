"""Centralized async clients for the approved external LLM providers.

This module deliberately contains no feature prompts, profile handling, provider
fallback orchestration, or consent policy. Callers must enforce consent before
passing any user information here.
"""

from __future__ import annotations

import asyncio
import inspect
import json
from typing import Any, Callable, Literal, TypeVar

from pydantic import BaseModel, ValidationError

from app.core.config import settings


ProviderName = Literal["gemini", "groq"]
ResponseModel = TypeVar("ResponseModel", bound=BaseModel)
# Per-client best-effort limit; shutdown must not wait indefinitely on a close.
CLEANUP_TIMEOUT_SECONDS = 2.0
_GEMINI_UNSUPPORTED_JSON_SCHEMA_KEYWORDS = frozenset({"minLength", "maxLength"})
_ROADMAP_VALIDATION_FIELD_PATHS = frozenset({
    "root",
    "learning_guidance",
    "learning_guidance[].text",
    "learning_guidance[].skill_ids",
    "learning_guidance[].evidence_categories",
    "learning_guidance[].project_indexes",
    "practice_approaches",
    "practice_approaches[].text",
    "practice_approaches[].skill_ids",
    "practice_approaches[].evidence_categories",
    "practice_approaches[].project_indexes",
    "career_growth",
    "career_growth[].text",
    "career_growth[].related_role_ids",
    "unclassified",
})
_PYDANTIC_VALIDATOR_IDS = {
    "missing": "required_field_missing",
    "extra_forbidden": "unexpected_field",
    "literal_error": "invalid_enum",
    "enum": "invalid_enum",
    "string_type": "wrong_type",
    "list_type": "wrong_type",
    "dict_type": "wrong_type",
    "int_type": "wrong_type",
    "bool_type": "wrong_type",
    "string_too_short": "string_length_constraint",
    "string_too_long": "string_length_constraint",
    "too_short": "collection_length_constraint",
    "too_long": "collection_length_constraint",
    "value_error": "custom_validator",
}
_SAFE_VALIDATOR_IDS = frozenset({
    *_PYDANTIC_VALIDATOR_IDS.values(),
    "role_guidance_text_must_be_safe_and_concise",
    "unclassified",
})


def _safe_roadmap_validation_details(error: ValidationError) -> tuple[str, str]:
    """Return only allowlisted Pydantic location and error-type categories."""
    try:
        details = error.errors(include_input=False)
        if not details:
            return "unclassified", "unclassified"
        detail = details[0]
        location = detail.get("loc", ())
        path = "root"
        if location and isinstance(location[0], str):
            root = location[0]
            if root in {"learning_guidance", "practice_approaches", "career_growth"}:
                if len(location) == 1:
                    path = root
                elif (
                    len(location) == 3
                    and isinstance(location[1], int)
                    and location[2] in {"text", "skill_ids", "evidence_categories", "project_indexes", "related_role_ids"}
                ):
                    path = f"{root}[].{location[2]}"
                else:
                    path = "unclassified"
            else:
                path = "unclassified"
        validator_id = _PYDANTIC_VALIDATOR_IDS.get(detail.get("type"), "unclassified")
        if path not in _ROADMAP_VALIDATION_FIELD_PATHS:
            path = "unclassified"
        if validator_id not in _SAFE_VALIDATOR_IDS:
            validator_id = "unclassified"
        return path, validator_id
    except Exception:
        return "unclassified", "unclassified"


def _gemini_compatible_json_schema(schema: Any) -> Any:
    """Copy JSON Schema while removing Gemini-unsupported string bounds.

    The original Pydantic model remains authoritative for local validation.
    """
    if isinstance(schema, dict):
        return {
            key: _gemini_compatible_json_schema(value)
            for key, value in schema.items()
            if key not in _GEMINI_UNSUPPORTED_JSON_SCHEMA_KEYWORDS
        }
    if isinstance(schema, list):
        return [_gemini_compatible_json_schema(value) for value in schema]
    return schema


def _consume_cleanup_task(task: asyncio.Task[Any]) -> None:
    """Retrieve a detached cleanup task's eventual exception safely."""
    try:
        task.result()
    except BaseException:
        pass


async def _bounded_cleanup(awaitable: Any) -> None:
    """Wait briefly for cleanup without letting cancellation delay shutdown."""
    try:
        task = asyncio.create_task(awaitable)
        done, _pending = await asyncio.wait({task}, timeout=CLEANUP_TIMEOUT_SECONDS)
        if task in done:
            _consume_cleanup_task(task)
            return
        task.cancel()
        task.add_done_callback(_consume_cleanup_task)
    except BaseException:
        # Cleanup is best effort and must not replace a result or block shutdown.
        close = getattr(awaitable, "close", None)
        if callable(close):
            close()


class LLMProviderError(RuntimeError):
    """Base class for normalized provider errors; messages contain no payloads."""

    diagnostic_category = "provider_failure"

    def __init__(
        self,
        message: str,
        *,
        http_status: int | None = None,
        sdk_exception_category: str = "other_exception",
    ) -> None:
        super().__init__(message)
        self.http_status = http_status if isinstance(http_status, int) and 400 <= http_status <= 599 else None
        self.sdk_exception_category = (
            sdk_exception_category
            if sdk_exception_category in _SAFE_SDK_EXCEPTION_CATEGORIES
            else "other_exception"
        )


class LLMConfigurationError(LLMProviderError):
    """Provider key or SDK configuration is unavailable or invalid."""

    diagnostic_category = "configuration/authentication"


class LLMRateLimitError(LLMProviderError):
    """Provider rejected the call because of a rate or quota limit."""

    diagnostic_category = "rate_limit"


class LLMTimeoutError(LLMProviderError):
    """Provider call exceeded its bounded timeout."""

    diagnostic_category = "timeout"


class LLMNetworkError(LLMProviderError):
    """Provider could not be reached over the network."""

    diagnostic_category = "network"


class LLMProviderFailureError(LLMProviderError):
    """Provider returned an unsuccessful or otherwise failed response."""

    diagnostic_category = "provider_failure"


class LLMInvalidResponseError(LLMProviderError):
    """Provider output was empty, malformed, or failed model validation."""

    diagnostic_category = "provider_failure"

    def __init__(
        self,
        message: str,
        *,
        validation_boundary: str | None = None,
        field_path: str | None = None,
        validator_id: str | None = None,
        http_status: int | None = None,
        sdk_exception_category: str = "invalid_response",
    ) -> None:
        super().__init__(
            message,
            http_status=http_status,
            sdk_exception_category=sdk_exception_category,
        )
        self.validation_boundary = validation_boundary
        self.field_path = field_path
        self.validator_id = validator_id


_SAFE_PROVIDER_FAILURE_CATEGORIES = {
    "configuration/authentication",
    "rate_limit",
    "timeout",
    "network",
    "provider_failure",
}
_SAFE_SDK_EXCEPTION_CATEGORIES = frozenset({
    "rate_limit_exception",
    "timeout_exception",
    "authentication_exception",
    "network_exception",
    "http_client_exception",
    "http_server_exception",
    "provider_sdk_exception",
    "invalid_response",
    "other_exception",
})


def safe_provider_failure_category(error: BaseException) -> str:
    """Return only a fixed provider failure category; never inspect its message."""
    category = getattr(type(error), "diagnostic_category", None)
    if isinstance(category, str) and category in _SAFE_PROVIDER_FAILURE_CATEGORIES:
        return category
    return "provider_failure"


def _secret_value(value: Any) -> str | None:
    if value is None:
        return None
    secret = value.get_secret_value() if hasattr(value, "get_secret_value") else str(value)
    return secret.strip() or None


def _status_code(error: Exception) -> int | None:
    for attribute in ("status_code", "code"):
        value = getattr(error, attribute, None)
        if isinstance(value, int) and not isinstance(value, bool) and 100 <= value <= 599:
            return value
    return None


def _safe_sdk_exception_category(error: Exception, status: int | None) -> str:
    """Map exception metadata to fixed categories without exposing class names."""
    class_name = type(error).__name__.casefold()
    module_name = type(error).__module__.casefold()
    if isinstance(error, LLMInvalidResponseError) or "invalidresponse" in class_name:
        return "invalid_response"
    if status == 429 or "ratelimit" in class_name or "rate_limit" in class_name:
        return "rate_limit_exception"
    if status == 408 or isinstance(error, (TimeoutError, asyncio.TimeoutError)) or "timeout" in class_name:
        return "timeout_exception"
    if status in {401, 403} or any(word in class_name for word in ("authentication", "permission")):
        return "authentication_exception"
    if isinstance(error, (ConnectionError, OSError)) or any(
        word in class_name for word in ("connection", "network")
    ) or "httpx" in module_name and "error" in class_name:
        return "network_exception"
    if status is not None and 400 <= status < 500:
        return "http_client_exception"
    if status is not None and 500 <= status <= 599:
        return "http_server_exception"
    if module_name.startswith(("groq.", "google.genai")):
        return "provider_sdk_exception"
    return "other_exception"


def safe_provider_failure_diagnostics(error: BaseException) -> dict[str, Any]:
    """Expose only normalized category, bounded HTTP status, and fixed exception class."""
    status = getattr(error, "http_status", None)
    if not (isinstance(status, int) and not isinstance(status, bool) and 400 <= status <= 599):
        status = None
    sdk_category = getattr(error, "sdk_exception_category", None)
    if not isinstance(sdk_category, str) or sdk_category not in _SAFE_SDK_EXCEPTION_CATEGORIES:
        if isinstance(error, Exception):
            sdk_category = _safe_sdk_exception_category(error, status)
        else:
            sdk_category = "other_exception"
    return {
        "failure_category": safe_provider_failure_category(error),
        "http_status": status,
        "sdk_exception_category": sdk_category,
    }


def _normalize_provider_error(error: Exception) -> LLMProviderError:
    """Map SDK/transport exceptions without including their potentially sensitive text."""
    status = _status_code(error)
    sdk_category = _safe_sdk_exception_category(error, status)
    class_name = type(error).__name__.casefold()
    module_name = type(error).__module__.casefold()

    if status == 408 or isinstance(error, (TimeoutError, asyncio.TimeoutError)) or "timeout" in class_name:
        return LLMTimeoutError("The AI provider request timed out.", http_status=status, sdk_exception_category=sdk_category)
    if status == 429 or "ratelimit" in class_name or "rate_limit" in class_name:
        return LLMRateLimitError("The AI provider is temporarily rate limited.", http_status=status, sdk_exception_category=sdk_category)
    if status in {401, 403} or any(word in class_name for word in ("authentication", "permission")):
        return LLMConfigurationError("The AI provider credentials or permissions are invalid.", http_status=status, sdk_exception_category=sdk_category)
    if isinstance(error, (ConnectionError, OSError)) or any(
        word in class_name for word in ("connection", "network")
    ) or "httpx" in module_name and "error" in class_name:
        return LLMNetworkError("The AI provider could not be reached.", http_status=status, sdk_exception_category=sdk_category)
    return LLMProviderFailureError("The AI provider request failed.", http_status=status, sdk_exception_category=sdk_category)


def _build_gemini_client(api_key: str, timeout_seconds: float) -> Any:
    from google import genai
    from google.genai import types

    timeout_ms = max(1, int(timeout_seconds * 1000))
    http_options = types.HttpOptions(
        timeout=timeout_ms,
        retry_options=types.HttpRetryOptions(attempts=1),
    )
    return genai.Client(api_key=api_key, http_options=http_options)


def _build_groq_client(api_key: str, timeout_seconds: float) -> Any:
    from groq import AsyncGroq

    return AsyncGroq(api_key=api_key, timeout=timeout_seconds, max_retries=0)


class LLMProviderService:
    """Initialize and use Gemini/Groq clients without making startup requests."""

    def __init__(
        self,
        settings_obj: Any = settings,
        *,
        gemini_client_factory: Callable[[str, float], Any] | None = None,
        groq_client_factory: Callable[[str, float], Any] | None = None,
    ) -> None:
        self._settings = settings_obj
        self._gemini_client_factory = gemini_client_factory or _build_gemini_client
        self._groq_client_factory = groq_client_factory or _build_groq_client
        self._gemini_root_client: Any | None = None
        self._groq_client: Any | None = None
        self._gemini_initialization_failed = False
        self._groq_initialization_failed = False

    @property
    def timeout_seconds(self) -> float:
        return float(self._settings.llm_provider_timeout_seconds)

    @property
    def configured_providers(self) -> tuple[ProviderName, ...]:
        providers: list[ProviderName] = []
        if _secret_value(self._settings.gemini_api_key):
            providers.append("gemini")
        if _secret_value(self._settings.groq_api_key):
            providers.append("groq")
        return tuple(providers)

    async def startup(self) -> None:
        """Create configured clients only; this method performs no network calls."""
        if _secret_value(self._settings.gemini_api_key) and self._gemini_root_client is None:
            try:
                self._gemini_root_client = self._gemini_client_factory(
                    _secret_value(self._settings.gemini_api_key), self.timeout_seconds
                )
                self._gemini_initialization_failed = False
            except Exception:
                self._gemini_initialization_failed = True

        if _secret_value(self._settings.groq_api_key) and self._groq_client is None:
            try:
                self._groq_client = self._groq_client_factory(
                    _secret_value(self._settings.groq_api_key), self.timeout_seconds
                )
                self._groq_initialization_failed = False
            except Exception:
                self._groq_initialization_failed = True

    async def shutdown(self) -> None:
        gemini_client, self._gemini_root_client = self._gemini_root_client, None
        groq_client, self._groq_client = self._groq_client, None
        if gemini_client is not None:
            async_client = getattr(gemini_client, "aio", None)
            close_async = getattr(async_client, "aclose", None)
            if callable(close_async):
                try:
                    awaitable = close_async()
                except BaseException:
                    awaitable = None
                if awaitable is not None:
                    await _bounded_cleanup(awaitable)
            close_sync = getattr(gemini_client, "close", None)
            if callable(close_sync):
                await _bounded_cleanup(asyncio.to_thread(close_sync))
        if groq_client is not None:
            close = getattr(groq_client, "close", None)
            if callable(close):
                try:
                    if inspect.iscoroutinefunction(close):
                        await _bounded_cleanup(close())
                    else:
                        await _bounded_cleanup(asyncio.to_thread(close))
                except Exception:
                    pass

    def _client_for(self, provider: ProviderName) -> tuple[Any, str]:
        if provider == "gemini":
            if self._gemini_root_client is None:
                message = "Gemini client initialization failed." if self._gemini_initialization_failed else "Gemini is not configured."
                raise LLMConfigurationError(message)
            return self._gemini_root_client, self._settings.gemini_model_id
        if provider == "groq":
            if self._groq_client is None:
                message = "Groq client initialization failed." if self._groq_initialization_failed else "Groq is not configured."
                raise LLMConfigurationError(message)
            return self._groq_client, self._settings.groq_model_id
        raise LLMConfigurationError("The requested AI provider is unsupported.")

    async def generate_json(
        self,
        provider: ProviderName,
        *,
        prompt: str,
        response_model: type[ResponseModel],
        max_output_tokens: int | None = None,
    ) -> ResponseModel:
        """Request structured JSON and validate it as a Pydantic model.

        Provider selection is explicit here. Fallback sequencing is intentionally
        left to a future enrichment service.
        """
        if not isinstance(prompt, str) or not prompt.strip():
            raise LLMInvalidResponseError("A non-empty prompt is required.")
        if not isinstance(response_model, type) or not issubclass(response_model, BaseModel):
            raise LLMInvalidResponseError("A Pydantic response model is required.")
        if max_output_tokens is not None and (
            not isinstance(max_output_tokens, int)
            or isinstance(max_output_tokens, bool)
            or max_output_tokens < 1
        ):
            raise LLMConfigurationError("The output token limit is invalid.")

        client, model_id = self._client_for(provider)
        try:
            schema = response_model.model_json_schema()
        except Exception:
            raise LLMConfigurationError("The requested response schema could not be prepared.") from None
        try:
            if provider == "gemini":
                from google.genai import types

                gemini_schema = _gemini_compatible_json_schema(schema)
                request = client.aio.models.generate_content(
                    model=model_id,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json",
                        response_json_schema=gemini_schema,
                        **({"max_output_tokens": max_output_tokens} if max_output_tokens is not None else {}),
                    ),
                )
                response = await asyncio.wait_for(request, timeout=self.timeout_seconds)
            else:
                request = client.chat.completions.create(
                    model=model_id,
                    messages=[{"role": "user", "content": prompt}],
                    response_format={
                        "type": "json_schema",
                        "json_schema": {
                            "name": response_model.__name__.casefold(),
                            "strict": False,
                            "schema": schema,
                        },
                    },
                    **({"max_completion_tokens": max_output_tokens} if max_output_tokens is not None else {}),
                )
                response = await asyncio.wait_for(request, timeout=self.timeout_seconds)
        except Exception as error:
            normalized = _normalize_provider_error(error)
            raise normalized from None

        try:
            output_text = (
                getattr(response, "text", None)
                if provider == "gemini"
                else response.choices[0].message.content
            )
        except Exception:
            raise LLMInvalidResponseError("The AI provider returned an invalid response structure.") from None

        if not isinstance(output_text, str) or not output_text.strip():
            raise LLMInvalidResponseError("The AI provider returned no structured content.")
        try:
            payload = json.loads(output_text)
        except (json.JSONDecodeError, TypeError, ValueError):
            raise LLMInvalidResponseError("The AI provider returned invalid structured content.") from None
        if not isinstance(payload, dict):
            raise LLMInvalidResponseError("The AI provider returned invalid structured content.") from None
        try:
            return response_model.model_validate(payload)
        except ValidationError as error:
            field_path, validator_id = _safe_roadmap_validation_details(error)
            raise LLMInvalidResponseError(
                "The AI provider returned invalid structured content.",
                validation_boundary="provider_response_model",
                field_path=field_path,
                validator_id=validator_id,
            ) from None
        except (TypeError, ValueError):
            raise LLMInvalidResponseError("The AI provider returned invalid structured content.") from None


llm_provider_service = LLMProviderService()
