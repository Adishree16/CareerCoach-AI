from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.config import settings


class SupabaseAuthError(RuntimeError):
    """Sanitized error raised for Supabase Auth request failures."""

    def __init__(self, message: str, *, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class SupabaseAuthService:
    """Reusable asynchronous HTTP client for the Supabase Auth REST API."""

    def __init__(self) -> None:
        project_url = settings.supabase_url
        publishable_key = settings.supabase_publishable_key
        if not project_url or not publishable_key:
            raise SupabaseAuthError("Supabase Auth is not configured.")

        parsed_url = urlsplit(project_url)
        if parsed_url.scheme != "https" or not parsed_url.netloc:
            raise SupabaseAuthError("Supabase Auth requires a valid HTTPS project URL.")

        self.auth_api_url = f"{project_url.rstrip('/')}/auth/v1/"
        self._client = httpx.AsyncClient(
            base_url=self.auth_api_url,
            headers={
                "apikey": publishable_key,
                "Accept": "application/json",
                "Content-Type": "application/json",
            },
            timeout=httpx.Timeout(10.0),
        )

    async def request(
        self,
        method: str,
        path: str,
        *,
        payload: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
    ) -> Any:
        """Send a request to a relative path under `/auth/v1/`."""
        relative_path = path.lstrip("/")
        if not relative_path or "://" in relative_path or relative_path.startswith(".."):
            raise ValueError("Auth API path must be relative to the Auth API base URL.")

        try:
            response = await self._client.request(
                method,
                relative_path,
                json=payload,
                headers=headers,
            )
        except httpx.TimeoutException:
            raise SupabaseAuthError("Supabase Auth request timed out.") from None
        except httpx.RequestError:
            raise SupabaseAuthError("Supabase Auth request failed.") from None

        if not 200 <= response.status_code < 300:
            raise SupabaseAuthError(
                "Supabase Auth returned an unsuccessful response.",
                status_code=response.status_code,
            )

        if not response.content:
            return None

        try:
            data = response.json()
        except ValueError:
            raise SupabaseAuthError(
                "Supabase Auth returned an invalid response.",
                status_code=response.status_code,
            ) from None

        if isinstance(data, dict) and ("error" in data or "error_description" in data):
            raise SupabaseAuthError(
                "Supabase Auth returned an API error.",
                status_code=response.status_code,
            )

        return data

    async def aclose(self) -> None:
        """Close the underlying asynchronous HTTP client."""
        await self._client.aclose()

    async def __aenter__(self) -> "SupabaseAuthService":
        return self

    async def __aexit__(self, exc_type: Any, exc: Any, traceback: Any) -> None:
        await self.aclose()
