import math
import secrets
import time
import asyncio
import httpx
from urllib.parse import urlsplit

from fastapi import APIRouter, Cookie, Depends, Header, HTTPException, Request, Response
from fastapi.responses import JSONResponse

from app.core.config import settings
from app.core.rate_limit import (
    REFRESH_REUSE_INTERVAL_SECONDS,
    REFRESH_UPSTREAM_TIMEOUT_SECONDS,
    RefreshLockBusy,
    RefreshRecoveryExpired,
    RefreshRecovery,
    rate_limit_service,
)
from app.schemas.auth import (
    AuthMessageResponse,
    AuthenticatedUser,
    LoginRequest,
    LoginResponse,
    RegisterRequest,
    RegisterResponse,
)
from app.services.supabase_auth import SupabaseAuthError, SupabaseAuthService

router = APIRouter(prefix="/auth", tags=["auth"])

_PRE_SEND_HTTPX_ERRORS = (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout)

ACCESS_COOKIE = "careercoach_access_token"
REFRESH_COOKIE = "careercoach_refresh_token"
SESSION_COOKIE = "careercoach_session_id"
CSRF_COOKIE = "careercoach_csrf_token"
CSRF_HEADER = "X-CSRF-Token"


def _cookie_options() -> dict[str, str | bool]:
    return {
        "httponly": True,
        "secure": settings.app_env.casefold() not in {"development", "dev", "local", "test"},
        "samesite": "lax",
        "path": "/api",
    }


def _clear_auth_cookies(response: Response) -> None:
    options = _cookie_options()
    response.delete_cookie(key=ACCESS_COOKIE, **options)
    response.delete_cookie(key=REFRESH_COOKIE, **options)
    response.delete_cookie(key=SESSION_COOKIE, **options)


def _origin_key(value: str, *, allow_path: bool = False) -> tuple[str, str, int] | None:
    try:
        parsed = urlsplit(value)
        scheme = parsed.scheme.casefold()
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if (
        scheme not in {"http", "https"}
        or not hostname
        or parsed.username is not None
        or parsed.password is not None
        or (
            not allow_path
            and (parsed.path not in {"", "/"} or parsed.query or parsed.fragment)
        )
    ):
        return None
    effective_port = port or (443 if scheme == "https" else 80)
    return scheme, hostname.casefold(), effective_port


TRUSTED_ORIGIN_KEYS = {
    origin_key
    for configured_origin in settings.cors_origins
    if (origin_key := _origin_key(configured_origin)) is not None
}


async def require_trusted_origin(request: Request) -> None:
    """Reject unsafe requests that do not originate at a configured frontend."""
    origin = request.headers.get("origin")
    if origin is not None:
        request_origin = _origin_key(origin)
    else:
        referer = request.headers.get("referer")
        request_origin = _origin_key(referer, allow_path=True) if referer else None

    if request_origin is None or request_origin not in TRUSTED_ORIGIN_KEYS:
        raise HTTPException(status_code=403, detail="Request origin is not allowed.")


async def require_csrf_protection(
    request: Request,
    csrf_cookie: str | None = Cookie(default=None, alias=CSRF_COOKIE),
    csrf_header: str | None = Header(default=None, alias=CSRF_HEADER),
) -> None:
    """Require trusted origin plus a matching double-submit token for mutations."""
    await require_trusted_origin(request)
    if (
        not csrf_cookie
        or not csrf_header
        or not secrets.compare_digest(csrf_cookie, csrf_header)
    ):
        raise HTTPException(status_code=403, detail="CSRF validation failed.")


def _issue_csrf_cookie(response: Response) -> str:
    token = secrets.token_urlsafe(32)
    _set_csrf_cookie(response, token)
    return token


def _set_csrf_cookie(response: Response, token: str) -> None:
    options = _cookie_options()
    options["httponly"] = False
    response.set_cookie(key=CSRF_COOKIE, value=token, **options)


def _set_refresh_response_cookies(
    response: Response, payload: dict, session_id: str, session_expires_at: float
) -> None:
    session_max_age = math.ceil(session_expires_at - time.time())
    if session_max_age < 1:
        raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
    access_expires_at = payload.get("access_expires_at")
    if (
        not isinstance(access_expires_at, (int, float))
        or isinstance(access_expires_at, bool)
        or not math.isfinite(access_expires_at)
    ):
        raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
    access_max_age = math.ceil(access_expires_at - time.time())
    if access_max_age < 1:
        raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
    options = _cookie_options()
    response.set_cookie(
        key=ACCESS_COOKIE,
        value=payload["access_token"],
        max_age=access_max_age,
        **options,
    )
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session_id,
        max_age=session_max_age,
        **options,
    )
    response.delete_cookie(key=REFRESH_COOKIE, **options)
    _set_csrf_cookie(response, payload["csrf_token"])


async def _set_authenticated_cookies(
    response: Response, access_token: str, access_max_age: int, session_id: str
) -> None:
    _refresh_token, session_expires_at, _session_max_age = await rate_limit_service.get_session_refresh_token(session_id)
    session_max_age = math.ceil(session_expires_at - time.time())
    if session_max_age < 1:
        raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
    options = _cookie_options()
    response.set_cookie(
        key=ACCESS_COOKIE, value=access_token, max_age=access_max_age, **options
    )
    response.set_cookie(
        key=SESSION_COOKIE, value=session_id, max_age=session_max_age, **options
    )
    response.delete_cookie(key=REFRESH_COOKIE, **options)
    _issue_csrf_cookie(response)


def _clear_csrf_cookie(response: Response) -> None:
    options = _cookie_options()
    options["httponly"] = False
    response.delete_cookie(key=CSRF_COOKIE, **options)


def _access_cookie_max_age(session: dict) -> int | None:
    expires_in = session.get("expires_in")
    expires_at = session.get("expires_at")
    if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool):
        return math.ceil(expires_in) if math.isfinite(expires_in) and expires_in > 0 else None
    if isinstance(expires_at, (int, float)) and not isinstance(expires_at, bool):
        remaining = expires_at - time.time()
        return math.ceil(remaining) if math.isfinite(remaining) and remaining > 0 else None
    return None


def _valid_session(session: object) -> tuple[str, str, int] | None:
    if not isinstance(session, dict):
        return None
    access_token = session.get("access_token")
    refresh_token = session.get("refresh_token")
    max_age = _access_cookie_max_age(session)
    if (
        isinstance(access_token, str)
        and access_token
        and isinstance(refresh_token, str)
        and refresh_token
        and max_age is not None
    ):
        return access_token, refresh_token, max_age
    return None


async def _fetch_authenticated_user(
    supabase_auth: SupabaseAuthService, access_token: str
) -> AuthenticatedUser:
    try:
        user_data = await supabase_auth.request(
            "GET",
            "user",
            headers={"Authorization": f"Bearer {access_token}"},
        )
    except SupabaseAuthError as error:
        if error.status_code is not None and 400 <= error.status_code < 500:
            raise HTTPException(status_code=401, detail="Authentication required.") from None
        raise HTTPException(status_code=503, detail="Authentication service unavailable.") from None

    if not isinstance(user_data, dict):
        raise HTTPException(status_code=401, detail="Authentication required.")
    user_id = user_data.get("id")
    email = user_data.get("email")
    if not isinstance(user_id, str) or not user_id or not isinstance(email, str) or not email:
        raise HTTPException(status_code=401, detail="Authentication required.")
    return AuthenticatedUser(id=user_id, email=email)


async def get_current_user(
    response: Response,
    access_token: str | None = Cookie(default=None, alias=ACCESS_COOKIE),
    session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE),
    legacy_refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
) -> AuthenticatedUser:
    """Validate both the access token and its authoritative opaque session."""
    if not access_token or (not session_id and not legacy_refresh_token):
        raise HTTPException(status_code=401, detail="Authentication required.")
    try:
        if session_id:
            await rate_limit_service.get_session_refresh_token(session_id)
        async with SupabaseAuthService() as supabase_auth:
            user = await _fetch_authenticated_user(supabase_auth, access_token)
        if not session_id and legacy_refresh_token:
            # Verify the access identity before migrating the old fixed-name
            # refresh cookie; migration itself uses the existing server SID.
            session_id = await rate_limit_service.establish_session(
                legacy_refresh_token, user.id
            )
            _current_refresh, _expires_at, max_age = await rate_limit_service.get_session_refresh_token(session_id)
            options = _cookie_options()
            response.set_cookie(
                key=SESSION_COOKIE, value=session_id, max_age=max_age, **options
            )
            response.delete_cookie(key=REFRESH_COOKIE, **options)
        await rate_limit_service.bind_session_user(session_id, user.id)
        return user
    except HTTPException:
        raise
    except SupabaseAuthError:
        raise HTTPException(status_code=503, detail="Authentication service unavailable.") from None


@router.get("/me", response_model=AuthenticatedUser)
async def get_me(
    current_user: AuthenticatedUser = Depends(get_current_user),
) -> AuthenticatedUser:
    return current_user


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=201,
    dependencies=[Depends(require_trusted_origin)],
)
async def register(
    request: RegisterRequest, response: Response, http_request: Request
) -> RegisterResponse:
    await rate_limit_service.check_credentials(http_request, request.email, registration=True)
    try:
        async with SupabaseAuthService() as supabase_auth:
            session = await supabase_auth.request(
                "POST",
                "signup",
                payload={
                    "email": request.email,
                    "password": request.password,
                },
            )
    except SupabaseAuthError:
        return JSONResponse(
            status_code=400,
            content=RegisterResponse(
                registered=False,
                authenticated=False,
                confirmation_required=False,
                message="Unable to create an account with those details. Please try again or use another email address.",
            ).model_dump(),
        )

    session_data = session if isinstance(session, dict) else {}
    access_token = session_data.get("access_token")
    refresh_token = session_data.get("refresh_token")
    expires_in = session_data.get("expires_in")
    expires_at = session_data.get("expires_at")

    if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool):
        access_max_age = (
            math.ceil(expires_in)
            if math.isfinite(expires_in) and expires_in > 0
            else None
        )
    elif isinstance(expires_at, (int, float)) and not isinstance(expires_at, bool):
        remaining = expires_at - time.time()
        access_max_age = (
            math.ceil(remaining) if math.isfinite(remaining) and remaining > 0 else None
        )
    else:
        access_max_age = None

    has_session_tokens = (
        isinstance(access_token, str)
        and bool(access_token)
        and isinstance(refresh_token, str)
        and bool(refresh_token)
    )
    if has_session_tokens and access_max_age is not None:
        try:
            user = session_data.get("user")
            user_id = user.get("id") if isinstance(user, dict) else None
            session_id = await rate_limit_service.establish_session(refresh_token, user_id)
            await _set_authenticated_cookies(response, access_token, access_max_age, session_id)
        except HTTPException:
            return JSONResponse(
                status_code=503,
                content={"detail": "Authentication service temporarily unavailable."},
            )
        return RegisterResponse(
            registered=True,
            authenticated=True,
            confirmation_required=False,
            message="Your account was created and you are signed in.",
        )

    if not access_token and not refresh_token:
        return RegisterResponse(
            registered=True,
            authenticated=False,
            confirmation_required=True,
            message="Your account was created. Additional verification is required before you can sign in.",
        )

    return JSONResponse(
        status_code=502,
        content=RegisterResponse(
            registered=False,
            authenticated=False,
            confirmation_required=False,
            message="Could not establish a secure session. Please try again.",
        ).model_dump(),
    )


@router.post(
    "/login",
    response_model=LoginResponse,
    dependencies=[Depends(require_trusted_origin)],
)
async def login(
    request: LoginRequest, response: Response, http_request: Request
) -> LoginResponse:
    await rate_limit_service.check_credentials(http_request, request.email, registration=False)
    try:
        async with SupabaseAuthService() as supabase_auth:
            session = await supabase_auth.request(
                "POST",
                "token?grant_type=password",
                payload={"email": request.email, "password": request.password.get_secret_value()},
            )
    except SupabaseAuthError as error:
        if error.status_code in {400, 401, 422}:
            status_code = 401
            message = "Invalid email or password."
        else:
            status_code = 502
            message = "Unable to sign in right now. Please try again."
        return JSONResponse(
            status_code=status_code,
            content=LoginResponse(authenticated=False, message=message).model_dump(),
        )

    session_data = session if isinstance(session, dict) else {}
    access_token = session_data.get("access_token")
    refresh_token = session_data.get("refresh_token")
    expires_in = session_data.get("expires_in")
    expires_at = session_data.get("expires_at")

    if isinstance(expires_in, (int, float)) and not isinstance(expires_in, bool):
        access_max_age = (
            math.ceil(expires_in)
            if math.isfinite(expires_in) and expires_in > 0
            else None
        )
    elif isinstance(expires_at, (int, float)) and not isinstance(expires_at, bool):
        remaining = expires_at - time.time()
        access_max_age = (
            math.ceil(remaining) if math.isfinite(remaining) and remaining > 0 else None
        )
    else:
        access_max_age = None

    has_session_tokens = (
        isinstance(access_token, str)
        and bool(access_token)
        and isinstance(refresh_token, str)
        and bool(refresh_token)
    )
    if not has_session_tokens or access_max_age is None:
        return JSONResponse(
            status_code=502,
            content=LoginResponse(
                authenticated=False,
                message="Unable to establish a secure session. Please try again.",
            ).model_dump(),
        )

    try:
        user = session_data.get("user")
        user_id = user.get("id") if isinstance(user, dict) else None
        session_id = await rate_limit_service.establish_session(refresh_token, user_id)
        await _set_authenticated_cookies(response, access_token, access_max_age, session_id)
    except HTTPException:
        return JSONResponse(
            status_code=503,
            content={"detail": "Authentication service temporarily unavailable."},
        )

    return LoginResponse(authenticated=True, message="Login successful.")


@router.get("/csrf")
async def get_csrf_token(
    response: Response,
    csrf_cookie: str | None = Cookie(default=None, alias=CSRF_COOKIE),
) -> dict[str, str]:
    response.headers["Cache-Control"] = "no-store"
    token_is_valid = (
        isinstance(csrf_cookie, str)
        and 32 <= len(csrf_cookie) <= 128
        and all(character.isalnum() or character in "-_" for character in csrf_cookie)
    )
    token = csrf_cookie if token_is_valid else _issue_csrf_cookie(response)
    return {"csrf_token": token}


@router.post(
    "/refresh",
    response_model=AuthMessageResponse,
    dependencies=[Depends(require_csrf_protection)],
)
async def refresh_session(
    request: Request,
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
    session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> AuthMessageResponse:
    if not session_id and not refresh_token:
        _clear_auth_cookies(response)
        _clear_csrf_cookie(response)
        response.status_code = 401
        return AuthMessageResponse(authenticated=False, message="Session expired. Please sign in again.")

    await rate_limit_service.check_refresh_ip(request)
    rate_limit_service.ensure_refresh_recovery_ready()
    legacy_refresh_token = refresh_token if not session_id else None
    if not session_id:
        # One-time compatibility path: bind the existing fixed-name refresh
        # credential to its already-known opaque limiter SID, encrypt it, then
        # stop using the browser-held refresh credential after success.
        try:
            session_id = await rate_limit_service.establish_session(refresh_token)
        except HTTPException:
            return JSONResponse(status_code=503, content={"detail": "Authentication service temporarily unavailable."})
    try:
        current_refresh_token, session_expires_at, _session_max_age = await rate_limit_service.get_session_refresh_token(session_id)
        # On the one-time migration path, keep the original browser credential
        # as the recovery lookup key. Stable-cookie requests always use Redis's
        # current encrypted token and never inspect the legacy cookie.
        refresh_token = legacy_refresh_token or current_refresh_token
    except HTTPException as error:
        if error.status_code == 401:
            _clear_auth_cookies(response)
            _clear_csrf_cookie(response)
            response.status_code = 401
            return AuthMessageResponse(authenticated=False, message="Session expired. Please sign in again.")
        return JSONResponse(status_code=503, content={"detail": "Authentication service temporarily unavailable."})
    try:
        prepared = await rate_limit_service.prepare_refresh(refresh_token)
    except RefreshLockBusy:
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Please try again later.",
            headers={"Retry-After": "1"},
        ) from None
    except RefreshRecoveryExpired:
        _clear_auth_cookies(response)
        _clear_csrf_cookie(response)
        response.status_code = 401
        return AuthMessageResponse(
            authenticated=False,
            message="Session expired. Please sign in again.",
        )

    if isinstance(prepared, RefreshRecovery):
        try:
            if prepared.session_id != session_id:
                raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
            _set_refresh_response_cookies(response, prepared.payload, session_id, session_expires_at)
        except HTTPException:
            return JSONResponse(
                status_code=503,
                content={"detail": "Authentication service temporarily unavailable."},
            )
        return AuthMessageResponse(authenticated=True, message="Session refreshed.")

    attempt = prepared

    request_may_have_reached_supabase = False
    refresh_response_received = False
    authenticated_user: AuthenticatedUser | None = None
    try:
        # Construct the client before marking the attempt ambiguous. A local
        # configuration/client-construction failure cannot have rotated a token.
        supabase_client = SupabaseAuthService()
    except Exception:
        await rate_limit_service.release_refresh(attempt, clear_attempt=True)
        return JSONResponse(
            status_code=503,
            content={"detail": "Authentication service temporarily unavailable."},
        )

    try:
        async with supabase_client as supabase_auth:
            # This atomic marker is written immediately before the upstream call.
            # Once marked, all transport failures are treated as ambiguous because
            # the existing HTTP service does not prove whether bytes reached Supabase.
            try:
                await rate_limit_service.mark_refresh_ambiguous(attempt)
            except Exception:
                await rate_limit_service.release_refresh(attempt, clear_attempt=True)
                return JSONResponse(
                    status_code=503,
                    content={"detail": "Authentication service temporarily unavailable."},
                )

            request_may_have_reached_supabase = True
            session = await asyncio.wait_for(
                supabase_auth.request(
                    "POST",
                    "token?grant_type=refresh_token",
                    payload={"refresh_token": refresh_token},
                ),
                timeout=REFRESH_UPSTREAM_TIMEOUT_SECONDS,
            )
            refresh_response_received = True
            valid_session = _valid_session(session)
            if valid_session is None:
                raise SupabaseAuthError("Supabase Auth returned an incomplete session.")
            access_token, rotated_refresh_token, access_max_age = valid_session
            access_expires_at = time.time() + access_max_age
            authenticated_user = await asyncio.wait_for(
                _fetch_authenticated_user(supabase_auth, access_token),
                timeout=REFRESH_UPSTREAM_TIMEOUT_SECONDS,
            )
        if authenticated_user is None:
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
        await rate_limit_service.bind_session_user(attempt.session_id, authenticated_user.id)
    except SupabaseAuthError as error:
        confirmed_pre_send = isinstance(error.__context__, _PRE_SEND_HTTPX_ERRORS)
        if not request_may_have_reached_supabase or confirmed_pre_send:
            # HTTPX connection/pool establishment failures happen before an HTTP
            # request can be processed. The service wraps errors with `from None`,
            # which suppresses display but retains __context__ for this check.
            await rate_limit_service.release_refresh(attempt, clear_attempt=True)
        if (
            request_may_have_reached_supabase
            and not refresh_response_received
            and error.status_code is not None
            and 400 <= error.status_code < 500
        ):
            # A completed 4xx from the refresh endpoint confirms rejection, so
            # this request did not rotate the token. Remove only this owner's
            # attempt marker and lock, allowing a still-valid token to retry.
            await rate_limit_service.release_refresh(attempt, clear_attempt=True)
            _clear_auth_cookies(response)
            _clear_csrf_cookie(response)
            response.status_code = 401
            return AuthMessageResponse(authenticated=False, message="Session expired. Please sign in again.")
        # Remaining transport errors, malformed successful responses, and user
        # validation failures may follow a rotation. Preserve old cookies and the
        # ambiguous marker; never issue cookies or make an uncertain retry.
        return JSONResponse(
            status_code=503,
            content={"detail": "Authentication service temporarily unavailable."},
        )
    except HTTPException:
        if not request_may_have_reached_supabase:
            await rate_limit_service.release_refresh(attempt, clear_attempt=True)
        return JSONResponse(
            status_code=503,
            content={"detail": "Authentication service temporarily unavailable."},
        )
    except Exception:
        if not request_may_have_reached_supabase:
            await rate_limit_service.release_refresh(attempt, clear_attempt=True)
        # Unknown post-refresh failures are treated as ambiguous rotations.
        return JSONResponse(
            status_code=503,
            content={"detail": "Authentication service temporarily unavailable."},
        )

    csrf_token = secrets.token_urlsafe(32)
    recovery_payload = {
        "access_token": access_token,
        "refresh_token": rotated_refresh_token,
        "access_expires_at": access_expires_at,
        "csrf_token": csrf_token,
    }
    try:
        recovery_envelope = rate_limit_service.encrypt_refresh_response(
            attempt, recovery_payload
        )
    except HTTPException:
        return JSONResponse(
            status_code=503,
            content={"detail": "Authentication service temporarily unavailable."},
        )

    try:
        await rate_limit_service.rotate_session(
            attempt, rotated_refresh_token, recovery_envelope,
            user_id=authenticated_user.id if authenticated_user else None,
        )
    except HTTPException:
        # Supabase may already have rotated the token. Keep old cookies and the
        # attempt marker. A retry can replay only an encrypted response committed
        # atomically with its mapping; never issue untracked cookies.
        return JSONResponse(
            status_code=503,
            content={"detail": "Authentication service temporarily unavailable."},
        )
    except Exception:
        return JSONResponse(
            status_code=503,
            content={"detail": "Authentication service temporarily unavailable."},
        )

    try:
        _set_refresh_response_cookies(
            response,
            {
                **recovery_payload,
                "recovery_deadline": attempt.started_at + REFRESH_REUSE_INTERVAL_SECONDS,
            },
            session_id,
            session_expires_at,
        )
    except HTTPException:
        return JSONResponse(
            status_code=503,
            content={"detail": "Authentication service temporarily unavailable."},
        )
    return AuthMessageResponse(authenticated=True, message="Session refreshed.")


@router.post(
    "/logout",
    response_model=AuthMessageResponse,
    dependencies=[Depends(require_csrf_protection)],
)
async def logout(
    response: Response,
    access_token: str | None = Cookie(default=None, alias=ACCESS_COOKIE),
    refresh_token: str | None = Cookie(default=None, alias=REFRESH_COOKIE),
    session_id: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> AuthMessageResponse:
    refresh_to_revoke = refresh_token
    try:
        if session_id:
            # Close locally first. The atomic status transition prevents any
            # in-flight refresh commit from reopening this session.
            refresh_to_revoke = await rate_limit_service.close_session(session_id) or refresh_token
        elif refresh_token:
            await rate_limit_service.clear_session(refresh_token)
    except HTTPException:
        _clear_auth_cookies(response)
        _clear_csrf_cookie(response)
        response.status_code = 503
        return AuthMessageResponse(
            authenticated=False,
            message="Signed out locally; server-side session revocation could not be confirmed.",
        )

    revocation_confirmed = False
    if access_token or refresh_to_revoke:
        try:
            async with SupabaseAuthService() as supabase_auth:
                token_to_revoke = access_token
                if token_to_revoke:
                    try:
                        await supabase_auth.request(
                            "POST",
                            "logout?scope=global",
                            headers={"Authorization": f"Bearer {token_to_revoke}"},
                        )
                        revocation_confirmed = True
                    except SupabaseAuthError:
                        token_to_revoke = None

                if not token_to_revoke and refresh_to_revoke:
                    try:
                        refreshed_session = await supabase_auth.request(
                            "POST",
                            "token?grant_type=refresh_token",
                            payload={"refresh_token": refresh_to_revoke},
                        )
                        candidate = _valid_session(refreshed_session)
                        if candidate is not None:
                            token_to_revoke = candidate[0]
                            await supabase_auth.request(
                                "POST",
                                "logout?scope=global",
                                headers={"Authorization": f"Bearer {token_to_revoke}"},
                            )
                            revocation_confirmed = True
                    except SupabaseAuthError:
                        pass
        except SupabaseAuthError:
            # Local session closure has already completed.
            pass

    _clear_auth_cookies(response)
    _clear_csrf_cookie(response)
    if (session_id or access_token or refresh_to_revoke) and not revocation_confirmed:
        return AuthMessageResponse(
            authenticated=False,
            message="Signed out locally; server-side session revocation could not be confirmed.",
        )
    return AuthMessageResponse(authenticated=False, message="Logged out.")
