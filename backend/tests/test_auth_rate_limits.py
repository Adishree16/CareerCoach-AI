"""Mocked route tests for rate-limit integration and auth-cookie preservation."""

import asyncio
import json
import time
import unittest
from unittest.mock import patch

from fastapi import HTTPException, Response
import httpx
from starlette.requests import Request

from app.api.routes import auth
from app.core.config import Settings
from app.core.rate_limit import RateLimitService, RefreshAttempt, REFRESH_REUSE_INTERVAL_SECONDS
from app.schemas.auth import LoginRequest, RegisterRequest
from app.main import app as fastapi_app
from tests.test_rate_limit import FakeRatelimit, FakeRedis, FakeSlidingWindow

if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class FakeRateService:
    def __init__(self, *, fail_mapping=False):
        self.calls = []
        self.fail_mapping = fail_mapping
        self.rotated = False
        self.fail_mark = False
        self.last_established_token = None
        self.attempt = RefreshAttempt("opaque-session", "digest-old", "digest-old", "owner", 1000.0)

    async def check_credentials(self, request, email, *, registration):
        self.calls.append(("credentials", email, registration))

    async def check_refresh_ip(self, request):
        self.calls.append(("refresh-ip",))

    def ensure_refresh_recovery_ready(self):
        self.calls.append(("recovery-ready",))

    async def establish_session(self, token, user_id=None):
        self.calls.append(("establish", token))
        self.last_established_token = token
        if self.fail_mapping:
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
        return "opaque-session"

    async def get_session_refresh_token(self, session_id):
        self.calls.append(("get-session-token", session_id))
        return self.last_established_token or "old-refresh-cookie", time.time() + 86400, 86400

    async def bind_session_user(self, session_id, user_id):
        self.calls.append(("bind-user", session_id, user_id))

    async def close_session(self, session_id):
        self.calls.append(("close-session", session_id))
        return self.last_established_token

    async def prepare_refresh(self, token):
        self.calls.append(("prepare", token))
        return self.attempt

    async def check_refresh_session(self, session_id):
        self.calls.append(("session-limit", session_id))

    def encrypt_refresh_response(self, attempt, payload):
        self.calls.append(("encrypt-recovery", attempt.session_id))
        return "mock-encrypted-refresh-response"

    async def rotate_session(self, attempt, token, recovery_envelope, user_id=None):
        self.calls.append(("rotate", token, recovery_envelope, user_id))
        if self.fail_mapping:
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
        self.rotated = True

    async def release_refresh(self, attempt, *, clear_attempt=False):
        self.calls.append(("release", clear_attempt))

    async def mark_refresh_ambiguous(self, attempt):
        self.calls.append(("mark-ambiguous",))
        if self.fail_mark:
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")

    async def clear_session(self, token):
        self.calls.append(("clear-session", token))


class FakeSupabaseAuth:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.closed = False
        self.calls = []
        self.refresh_token = "refresh-secret-value"
        self.refresh_failure = None
        self.refresh_gate = None

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.closed = True

    async def request(self, method, path, *, payload=None, headers=None):
        self.calls.append((method, path, payload, headers))
        if path == "token?grant_type=refresh_token":
            if self.refresh_gate is not None:
                self.refresh_gate[0].set()
                await self.refresh_gate[1].wait()
            if self.refresh_failure is not None:
                raise self.refresh_failure
            return {
                "access_token": "access-secret-value",
                "refresh_token": self.refresh_token,
                "expires_in": 3600,
            }
        if self.fail:
            raise auth.SupabaseAuthError("sanitized", status_code=401)
        if path == "user":
            return {"id": "verified-user-id", "email": "person@example.com"}
        return {
            "access_token": "access-secret-value",
            "refresh_token": "refresh-secret-value",
            "expires_in": 3600,
        }


def make_request():
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/auth/login",
        "raw_path": b"/api/auth/login",
        "query_string": b"",
        "headers": [],
        "client": ("203.0.113.10", 1234),
        "server": ("localhost", 8000),
    }
    return Request(scope)


class AuthCookieConfigurationTests(unittest.TestCase):
    @staticmethod
    def _set_cookie_headers(response):
        return [value.decode("latin-1") for name, value in response.raw_headers if name.lower() == b"set-cookie"]

    def test_production_auth_cookies_are_cross_site_compatible(self):
        response = Response()
        with patch.object(auth.settings, "app_env", "production"):
            auth._set_refresh_response_cookies(
                response,
                {
                    "access_token": "synthetic-access-token",
                    "access_expires_at": time.time() + 3600,
                    "csrf_token": "synthetic-csrf-token",
                },
                "synthetic-session-id",
                time.time() + 3600,
            )

        cookies = self._set_cookie_headers(response)
        self.assertEqual(len(cookies), 4)
        for cookie_name in (
            auth.ACCESS_COOKIE,
            auth.SESSION_COOKIE,
            auth.REFRESH_COOKIE,
            auth.CSRF_COOKIE,
        ):
            header = next(item for item in cookies if item.startswith(cookie_name + "="))
            self.assertIn("secure", header.lower())
            self.assertIn("samesite=none", header.lower())
            if cookie_name == auth.CSRF_COOKIE:
                self.assertNotIn("httponly", header.lower())
            else:
                self.assertIn("httponly", header.lower())

    def test_local_auth_cookies_keep_development_attributes(self):
        with patch.object(auth.settings, "app_env", "development"):
            options = auth._cookie_options()
        self.assertFalse(options["secure"])
        self.assertEqual(options["samesite"], "lax")
        self.assertTrue(options["httponly"])

    def test_cors_uses_explicit_credentialed_origins(self):
        from starlette.middleware.cors import CORSMiddleware

        cors = next(item for item in fastapi_app.user_middleware if item.cls is CORSMiddleware)
        self.assertTrue(cors.kwargs["allow_credentials"])
        self.assertTrue(cors.kwargs["allow_origins"])
        self.assertNotIn("*", cors.kwargs["allow_origins"])
        self.assertEqual(
            Settings(frontend_origins="https://careercoach.example.netlify.app").cors_origins,
            ["https://careercoach.example.netlify.app"],
        )
        with self.assertRaises(ValueError):
            Settings(frontend_origins="*").cors_origins


class AuthRateLimitRouteTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.original_service = auth.rate_limit_service
        self.original_supabase = auth.SupabaseAuthService
        self.rate_service = FakeRateService()
        self.supabase = FakeSupabaseAuth()
        auth.rate_limit_service = self.rate_service
        auth.SupabaseAuthService = lambda: self.supabase

    def tearDown(self):
        auth.rate_limit_service = self.original_service
        auth.SupabaseAuthService = self.original_supabase

    @staticmethod
    def _set_cookie_headers(response):
        return [value.decode("latin-1") for name, value in response.raw_headers if name.lower() == b"set-cookie"]

    @staticmethod
    def _cookie_values(cookie_headers):
        values = {}
        for header in cookie_headers:
            name, value = header.split(";", 1)[0].split("=", 1)
            values[name] = value
        return values

    def _assert_refresh_cookie_contract(self, response):
        cookies = self._set_cookie_headers(response)
        self.assertEqual(len(cookies), 4)
        names = [header.split(";", 1)[0].split("=", 1)[0] for header in cookies]
        self.assertCountEqual(
            names,
            [auth.ACCESS_COOKIE, auth.SESSION_COOKIE, auth.REFRESH_COOKIE, auth.CSRF_COOKIE],
        )
        self.assertTrue(any(header.startswith(auth.ACCESS_COOKIE + "=") and "httponly" in header.lower() for header in cookies))
        self.assertTrue(any(header.startswith(auth.SESSION_COOKIE + "=") and "httponly" in header.lower() for header in cookies))
        self.assertTrue(any(header.startswith(auth.REFRESH_COOKIE + "=") and "max-age=0" in header.lower() and "path=/api" in header.lower() for header in cookies))
        self.assertTrue(any(header.startswith(auth.CSRF_COOKIE + "=") and "httponly" not in header.lower() for header in cookies))

    async def test_successful_login_maps_session_before_setting_http_only_cookies(self):
        response = Response()
        result = await auth.login(
            LoginRequest(email="person@example.com", password="SecretPass1!"),
            response,
            make_request(),
        )
        self.assertTrue(result.authenticated)
        self.assertIn(("establish", "refresh-secret-value"), self.rate_service.calls)
        cookies = self._set_cookie_headers(response)
        self.assertEqual(len(cookies), 4)
        self.assertTrue(any("careercoach_access_token=access-secret-value" in value and "httponly" in value.lower() for value in cookies))
        self.assertTrue(any("careercoach_session_id=" in value and "httponly" in value.lower() for value in cookies))
        self.assertTrue(any("careercoach_refresh_token=\"\"" in value and "max-age=0" in value.lower() for value in cookies))
        body = result.model_dump_json()
        self.assertNotIn("access-secret-value", body)
        self.assertNotIn("refresh-secret-value", body)
        self.assertTrue(self.supabase.closed)

    async def test_login_mapping_failure_does_not_issue_auth_cookies(self):
        self.rate_service.fail_mapping = True
        response = Response()
        result = await auth.login(
            LoginRequest(email="person@example.com", password="SecretPass1!"),
            response,
            make_request(),
        )
        self.assertEqual(result.status_code, 503)
        self.assertEqual(self._set_cookie_headers(response), [])
        self.assertNotIn("refresh-secret-value", result.body.decode())

    async def test_failed_authentication_does_not_set_cookies(self):
        self.supabase.fail = True
        response = Response()
        result = await auth.login(
            LoginRequest(email="person@example.com", password="SecretPass1!"),
            response,
            make_request(),
        )
        self.assertEqual(result.status_code, 401)
        self.assertEqual(self._set_cookie_headers(response), [])
        self.assertFalse(any(call[0] == "establish" for call in self.rate_service.calls))

    async def test_current_user_migrates_verified_legacy_session_to_stable_cookie(self):
        response = Response()
        user = await auth.get_current_user(
            response,
            access_token="access-secret-value",
            session_id=None,
            legacy_refresh_token="legacy-refresh-value",
        )
        self.assertEqual(user.id, "verified-user-id")
        values = self._cookie_values(self._set_cookie_headers(response))
        self.assertEqual(values[auth.SESSION_COOKIE], "opaque-session")
        refresh_deletion = next(
            header for header in self._set_cookie_headers(response)
            if header.startswith(auth.REFRESH_COOKIE + "=")
        )
        self.assertIn("max-age=0", refresh_deletion.lower())
        self.assertIn("path=/api", refresh_deletion.lower())
        self.assertIn(("bind-user", "opaque-session", "verified-user-id"), self.rate_service.calls)

    async def test_successful_refresh_maps_rotation_before_cookie_update_and_keeps_csrf(self):
        response = Response()
        result = await auth.refresh_session(make_request(), response, "old-refresh-cookie", session_id=None)
        self.assertTrue(result.authenticated)
        self.assertTrue(self.rate_service.rotated)
        self.assertLess(
            next(i for i, call in enumerate(self.rate_service.calls) if call[0] == "rotate"),
            len(self.rate_service.calls),
        )
        cookies = self._set_cookie_headers(response)
        self.assertEqual(len(cookies), 4)
        self.assertTrue(any("careercoach_access_token=access-secret-value" in value and "httponly" in value.lower() for value in cookies))
        self.assertTrue(any("careercoach_session_id=" in value and "httponly" in value.lower() for value in cookies))
        self.assertTrue(any("careercoach_refresh_token=\"\"" in value and "max-age=0" in value.lower() for value in cookies))
        self.assertTrue(any("careercoach_csrf_token=" in value and "httponly" not in value.lower() for value in cookies))
        self.assertNotIn("access-secret-value", result.model_dump_json())
        self.assertNotIn("refresh-secret-value", result.model_dump_json())

    async def _make_real_rate_service(self, *, recovery_key=b"e" * 32):
        redis = FakeRedis()
        service = RateLimitService(
            redis_client=redis,
            hmac_secret=b"test-secret-for-rate-limits-32-bytes",
            recovery_encryption_key=recovery_key,
            recovery_encryption_key_id="test-key",
            limiter_class=FakeRatelimit,
            sliding_window_class=FakeSlidingWindow,
        )
        await service.startup()
        return service, redis

    async def test_refresh_marker_write_failure_is_pre_request_and_retryable(self):
        service, redis = await self._make_real_rate_service()
        original_service = auth.rate_limit_service
        auth.rate_limit_service = service
        try:
            old_token = "pre-request-refresh-token"
            await service.establish_session(old_token)
            redis.fail_next_mark = True
            response = Response()
            result = await auth.refresh_session(make_request(), response, old_token, session_id=None)
            self.assertEqual(result.status_code, 503)
            self.assertEqual(self._set_cookie_headers(response), [])
            self.assertFalse(any(call[1] == "token?grant_type=refresh_token" for call in self.supabase.calls))

            digest = service._digest("refresh-token", old_token)
            session = service._decode_mapping(await redis.get(service._mapping_key(digest)))
            self.assertIsNone(await redis.get(service._attempt_key(session["session_id"])))
            self.assertIsNone(await redis.get(service._lock_key(session["session_id"])))

            # The original token remains current and a later request can proceed.
            self.supabase.refresh_token = old_token
            retry_response = Response()
            retry = await auth.refresh_session(make_request(), retry_response, old_token, session_id=None)
            self.assertTrue(retry.authenticated)
            self._assert_refresh_cookie_contract(retry_response)
        finally:
            auth.rate_limit_service = original_service
            await service.shutdown()

    async def test_confirmed_httpx_connection_failure_clears_attempt_and_can_retry(self):
        service, redis = await self._make_real_rate_service()
        original_service = auth.rate_limit_service
        auth.rate_limit_service = service
        try:
            token = "connect-failure-refresh-token"
            await service.establish_session(token)
            try:
                raise httpx.ConnectError("connection was not established")
            except httpx.ConnectError:
                try:
                    raise auth.SupabaseAuthError("sanitized connection failure") from None
                except auth.SupabaseAuthError as wrapped:
                    self.supabase.refresh_failure = wrapped

            response = Response()
            result = await auth.refresh_session(make_request(), response, token, session_id=None)
            self.assertEqual(result.status_code, 503)
            self.assertEqual(self._set_cookie_headers(response), [])
            digest = service._digest("refresh-token", token)
            mapping = service._decode_mapping(await redis.get(service._mapping_key(digest)))
            self.assertIsNone(await redis.get(service._attempt_key(mapping["session_id"])))
            self.assertIsNone(await redis.get(service._lock_key(mapping["session_id"])))

            # A connection failure did not consume the still-current token.
            self.supabase.refresh_failure = None
            self.supabase.refresh_token = token
            retry_response = Response()
            retry = await auth.refresh_session(make_request(), retry_response, token, session_id=None)
            self.assertTrue(retry.authenticated)
            self._assert_refresh_cookie_contract(retry_response)
        finally:
            auth.rate_limit_service = original_service
            await service.shutdown()

    async def test_ambiguous_upstream_failure_keeps_marker_then_expires_closed(self):
        service, redis = await self._make_real_rate_service()
        original_service = auth.rate_limit_service
        auth.rate_limit_service = service
        try:
            old_token = "ambiguous-route-refresh-token"
            await service.establish_session(old_token)
            self.supabase.refresh_failure = auth.SupabaseAuthError("sanitized transport error")
            response = Response()
            result = await auth.refresh_session(make_request(), response, old_token, session_id=None)
            self.assertEqual(result.status_code, 503)
            self.assertEqual(self._set_cookie_headers(response), [])

            digest = service._digest("refresh-token", old_token)
            session = service._decode_mapping(await redis.get(service._mapping_key(digest)))
            marker_key = service._attempt_key(session["session_id"])
            marker = json.loads(await redis.get(marker_key))
            self.assertEqual(marker["phase"], "ambiguous")
            self.assertIsNotNone(await redis.get(service._lock_key(session["session_id"])))

            marker["started_at"] = time.time() - REFRESH_REUSE_INTERVAL_SECONDS - 1
            redis.values[marker_key] = json.dumps(marker)
            self.supabase.refresh_failure = None
            upstream_calls = sum(call[1] == "token?grant_type=refresh_token" for call in self.supabase.calls)
            expired_response = Response()
            expired = await auth.refresh_session(make_request(), expired_response, old_token, session_id=None)
            self.assertFalse(expired.authenticated)
            self.assertEqual(expired_response.status_code, 401)
            self.assertEqual(
                sum(call[1] == "token?grant_type=refresh_token" for call in self.supabase.calls),
                upstream_calls,
            )
            self.assertTrue(any("careercoach_access_token=" in value and "max-age=0" in value.lower() for value in self._set_cookie_headers(expired_response)))
        finally:
            auth.rate_limit_service = original_service
            await service.shutdown()

    async def test_refresh_succeeds_when_supabase_does_not_rotate_token(self):
        service, redis = await self._make_real_rate_service()
        original_service = auth.rate_limit_service
        auth.rate_limit_service = service
        try:
            token = "unchanged-refresh-token"
            await service.establish_session(token)
            self.supabase.refresh_token = token
            response = Response()
            result = await auth.refresh_session(make_request(), response, token, session_id=None)
            self.assertTrue(result.authenticated)
            self._assert_refresh_cookie_contract(response)
            digest = service._digest("refresh-token", token)
            mapping = service._decode_mapping(await redis.get(service._mapping_key(digest)))
            self.assertEqual(mapping["state"], "current")
            self.assertEqual(await redis.get(service._pointer_key(mapping["session_id"])), digest)
            self.assertIsNone(await redis.get(service._attempt_key(mapping["session_id"])))
        finally:
            auth.rate_limit_service = original_service
            await service.shutdown()

    async def test_lost_redis_response_after_commit_recovers_through_route(self):
        service, redis = await self._make_real_rate_service()
        original_service = auth.rate_limit_service
        auth.rate_limit_service = service
        try:
            old_token = "old-token-lost-response-route"
            new_token = "new-token-lost-response-route"
            await service.establish_session(old_token)
            self.supabase.refresh_token = new_token
            redis.fail_after_rotation_commit = True

            first_response = Response()
            first = await auth.refresh_session(make_request(), first_response, old_token, session_id=None)
            self.assertTrue(first.authenticated)
            first_cookies = self._set_cookie_headers(first_response)

            new_digest = service._digest("refresh-token", new_token)
            new_mapping = service._decode_mapping(await redis.get(service._mapping_key(new_digest)))
            self.assertEqual(new_mapping["state"], "current")

            # Simulate the browser losing the first HTTP response and retrying
            # with the old cookie after Redis committed but lost its reply.
            second_response = Response()
            second = await auth.refresh_session(make_request(), second_response, old_token, session_id=None)
            self.assertTrue(second.authenticated)
            second_cookies = self._set_cookie_headers(second_response)
            self.assertEqual(self._cookie_values(first_cookies), self._cookie_values(second_cookies))
            self.assertEqual(
                sum(call[1] == "token?grant_type=refresh_token" for call in self.supabase.calls),
                1,
            )
        finally:
            auth.rate_limit_service = original_service
            await service.shutdown()

    async def test_precommit_redis_failure_keeps_cookies_and_blocks_another_upstream_refresh(self):
        service, redis = await self._make_real_rate_service()
        original_service = auth.rate_limit_service
        auth.rate_limit_service = service
        try:
            old_token = "precommit-failure-old-refresh"
            await service.establish_session(old_token)
            redis.fail_rotation_attempts = 2

            first_response = Response()
            first = await auth.refresh_session(make_request(), first_response, old_token, session_id=None)
            self.assertEqual(first.status_code, 503)
            self.assertEqual(self._set_cookie_headers(first_response), [])
            self.assertEqual(
                sum(call[1] == "token?grant_type=refresh_token" for call in self.supabase.calls),
                1,
            )

            second_response = Response()
            with self.assertRaises(HTTPException) as raised:
                await auth.refresh_session(make_request(), second_response, old_token, session_id=None)
            self.assertIn(raised.exception.status_code, {429, 503})
            self.assertEqual(self._set_cookie_headers(second_response), [])
            self.assertEqual(
                sum(call[1] == "token?grant_type=refresh_token" for call in self.supabase.calls),
                1,
            )
        finally:
            auth.rate_limit_service = original_service
            await service.shutdown()

    async def test_invalid_recovery_key_fails_before_supabase_refresh(self):
        service, _ = await self._make_real_rate_service(recovery_key=b"invalid")
        original_service = auth.rate_limit_service
        auth.rate_limit_service = service
        try:
            response = Response()
            with self.assertRaises(HTTPException) as raised:
                await auth.refresh_session(
                    make_request(), response, "refresh-cookie-before-invalid-key", session_id=None
                )
            self.assertEqual(raised.exception.status_code, 503)
            self.assertEqual(self._set_cookie_headers(response), [])
            self.assertFalse(
                any(call[1] == "token?grant_type=refresh_token" for call in self.supabase.calls)
            )
        finally:
            auth.rate_limit_service = original_service
            await service.shutdown()

    async def test_reversed_refresh_responses_replay_the_current_rotated_session(self):
        service, redis = await self._make_real_rate_service()
        original_service = auth.rate_limit_service
        auth.rate_limit_service = service
        try:
            old_token = "ordering-old-refresh-token"
            first_rotated_token = "ordering-first-rotated-token"
            second_rotated_token = "ordering-second-rotated-token"
            await service.establish_session(old_token)

            returned_tokens = iter((first_rotated_token, second_rotated_token))

            async def request_with_distinct_rotations(method, path, *, payload=None, headers=None):
                self.supabase.calls.append((method, path, payload, headers))
                if path == "token?grant_type=refresh_token":
                    rotated_token = next(returned_tokens)
                    return {
                        "access_token": "access-for-" + rotated_token,
                        "refresh_token": rotated_token,
                        "expires_in": 3600,
                    }
                if path == "user":
                    return {"id": "verified-user-id", "email": "person@example.com"}
                raise AssertionError(f"Unexpected mocked Supabase path: {path}")

            self.supabase.request = request_with_distinct_rotations

            sid = await service.establish_session(old_token)
            first_response = Response()
            first_result = await auth.refresh_session(
                make_request(), first_response, session_id=sid
            )
            self.assertTrue(first_result.authenticated)
            first_cookies = self._set_cookie_headers(first_response)

            second_response = Response()
            second_result = await auth.refresh_session(
                make_request(), second_response, session_id=sid
            )
            self.assertTrue(second_result.authenticated)
            second_cookies = self._set_cookie_headers(second_response)

            # Apply the second HTTP response first and the first response last,
            # matching the browser's last-arriving Set-Cookie behavior.
            browser_cookies = {}
            for cookie_headers in (second_cookies, first_cookies):
                browser_cookies.update(self._cookie_values(cookie_headers))

            self.assertEqual(browser_cookies[auth.SESSION_COOKIE], sid)
            self.assertEqual(self._cookie_values(first_cookies)[auth.SESSION_COOKIE], self._cookie_values(second_cookies)[auth.SESSION_COOKIE])
            self.assertEqual(self._cookie_values(first_cookies).get("careercoach_refresh_token"), "")
            self.assertEqual(self._cookie_values(second_cookies).get("careercoach_refresh_token"), "")
            self.assertEqual(sum(call[1] == "token?grant_type=refresh_token" for call in self.supabase.calls), 2)

            self.assertEqual(browser_cookies["careercoach_access_token"], self._cookie_values(first_cookies)["careercoach_access_token"])
            self.assertEqual(browser_cookies["careercoach_csrf_token"], self._cookie_values(first_cookies)["careercoach_csrf_token"])

            first_digest = service._digest("refresh-token", first_rotated_token)
            second_digest = service._digest("refresh-token", second_rotated_token)
            first_mapping = service._decode_mapping(
                await redis.get(service._mapping_key(first_digest))
            )
            second_mapping_raw = await redis.get(service._mapping_key(second_digest))
            second_mapping = (
                None
                if second_mapping_raw is None
                else service._decode_mapping(second_mapping_raw)
            )
            self.assertEqual(first_mapping["state"], "consumed")
            self.assertEqual(second_mapping["state"], "current")
            self.assertEqual(await redis.get(service._pointer_key(sid)), second_digest)
            persistent = await redis.get(service._session_refresh_key(sid))
            self.assertNotIn(second_rotated_token, persistent)
            state = service._decode_session_state(
                await redis.get(service._session_state_key(sid)), sid
            )
            self.assertEqual(
                service._decrypt_session_refresh(persistent, sid, state["expires_at"])[0],
                second_rotated_token,
            )
        finally:
            auth.rate_limit_service = original_service
            await service.shutdown()

    async def test_legacy_refresh_cookie_migrates_to_same_encrypted_session(self):
        service, redis = await self._make_real_rate_service()
        original_service = auth.rate_limit_service
        auth.rate_limit_service = service
        try:
            legacy_token = "pre-stable-cookie-legacy-token"
            sid = await service.establish_session(legacy_token)
            redis.values.pop(service._session_state_key(sid), None)
            redis.values.pop(service._session_refresh_key(sid), None)
            redis.expirations.pop(service._session_state_key(sid), None)
            redis.expirations.pop(service._session_refresh_key(sid), None)
            self.supabase.refresh_token = "legacy-migrated-rotated-token"

            response = Response()
            result = await auth.refresh_session(
                make_request(), response, refresh_token=legacy_token, session_id=None
            )
            self.assertTrue(result.authenticated)
            values = self._cookie_values(self._set_cookie_headers(response))
            self.assertEqual(values[auth.SESSION_COOKIE], sid)
            self.assertEqual(values[auth.REFRESH_COOKIE], "")
            self.assertNotIn("legacy-migrated-rotated-token", await redis.get(service._session_refresh_key(sid)))
            self.assertEqual(
                (await service.get_session_refresh_token(sid))[0],
                "legacy-migrated-rotated-token",
            )
        finally:
            auth.rate_limit_service = original_service
            await service.shutdown()

    async def test_logout_closes_session_before_delayed_refresh_can_set_cookies(self):
        service, redis = await self._make_real_rate_service()
        original_service = auth.rate_limit_service
        auth.rate_limit_service = service
        try:
            sid = await service.establish_session("logout-race-refresh-token")
            started = asyncio.Event()
            release = asyncio.Event()
            self.supabase.refresh_gate = (started, release)
            refresh_response = Response()
            refresh_task = asyncio.create_task(
                auth.refresh_session(make_request(), refresh_response, session_id=sid)
            )
            await asyncio.wait_for(started.wait(), timeout=1)

            logout_response = Response()
            logout_result = await auth.logout(
                logout_response, access_token="access-for-logout", session_id=sid
            )
            self.assertFalse(logout_result.authenticated)
            state = service._decode_session_state(
                await redis.get(service._session_state_key(sid)), sid
            )
            self.assertEqual(state["status"], "closed")

            release.set()
            refresh_result = await refresh_task
            self.assertEqual(refresh_result.status_code, 503)
            self.assertEqual(self._set_cookie_headers(refresh_response), [])
            with self.assertRaises(HTTPException):
                await service.get_session_refresh_token(sid)
        finally:
            if "release" in locals():
                release.set()
            auth.rate_limit_service = original_service
            await service.shutdown()

    async def test_concurrent_route_refresh_gets_busy_without_second_upstream_call(self):
        service, _ = await self._make_real_rate_service()
        original_service = auth.rate_limit_service
        auth.rate_limit_service = service
        try:
            token = "concurrent-route-refresh-token"
            sid = await service.establish_session(token)
            started = asyncio.Event()
            release = asyncio.Event()
            self.supabase.refresh_gate = (started, release)
            self.supabase.refresh_token = "concurrent-rotated-token"

            first_response = Response()
            first_task = asyncio.create_task(
                auth.refresh_session(make_request(), first_response, session_id=sid)
            )
            await asyncio.wait_for(started.wait(), timeout=1)
            second_response = Response()
            with self.assertRaises(HTTPException) as raised:
                await auth.refresh_session(make_request(), second_response, session_id=sid)
            self.assertEqual(raised.exception.status_code, 429)
            self.assertEqual(self._set_cookie_headers(second_response), [])
            self.assertEqual(
                sum(call[1] == "token?grant_type=refresh_token" for call in self.supabase.calls),
                1,
            )
            release.set()
            first = await first_task
            self.assertTrue(first.authenticated)
            first_cookies = self._cookie_values(self._set_cookie_headers(first_response))
            self.assertEqual(first_cookies[auth.SESSION_COOKIE], sid)
            self.assertEqual(first_cookies[auth.REFRESH_COOKIE], "")
        finally:
            auth.rate_limit_service = original_service
            await service.shutdown()

    async def test_rotation_mapping_failure_never_sets_new_cookies(self):
        self.rate_service.fail_mapping = True
        response = Response()
        result = await auth.refresh_session(
            make_request(), response, "old-refresh-cookie", session_id=None
        )
        self.assertEqual(result.status_code, 503)
        self.assertEqual(self._set_cookie_headers(response), [])
        self.assertFalse(any(call[0] == "release" for call in self.rate_service.calls))

    async def test_registration_maps_session_before_issuing_cookies(self):
        response = Response()
        result = await auth.register(
            RegisterRequest(email="person@example.com", password="SecretPass1!"),
            response,
            make_request(),
        )
        self.assertTrue(result.authenticated)
        self.assertIn(("establish", "refresh-secret-value"), self.rate_service.calls)
        self.assertEqual(len(self._set_cookie_headers(response)), 4)

    async def test_refresh_route_keeps_csrf_dependency(self):
        route = next(route for route in auth.router.routes if getattr(route, "path", None) == "/auth/refresh")
        self.assertTrue(
            any(dependency.dependency is auth.require_csrf_protection for dependency in route.dependencies)
        )

    async def test_logout_still_clears_auth_and_csrf_cookies(self):
        response = Response()
        result = await auth.logout(response, "access-token", "refresh-token", session_id=None)
        self.assertFalse(result.authenticated)
        self.assertIn(("clear-session", "refresh-token"), self.rate_service.calls)
        cookies = self._set_cookie_headers(response)
        self.assertEqual(len(cookies), 4)
        self.assertTrue(all("max-age=0" in value.lower() and "path=/api" in value.lower() for value in cookies))

    async def test_auth_endpoints_remain_registered(self):
        paths = fastapi_app.openapi()["paths"]
        for path, method in (
            ("/api/auth/login", "post"),
            ("/api/auth/register", "post"),
            ("/api/auth/refresh", "post"),
            ("/api/auth/logout", "post"),
            ("/api/auth/me", "get"),
        ):
            self.assertIn(method, paths[path])


if __name__ == "__main__":
    unittest.main()
