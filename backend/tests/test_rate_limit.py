"""Offline tests for HMAC rate identifiers and refresh mapping behavior."""

import asyncio
import json
import time
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

from app.core.rate_limit import (
    REFRESH_REUSE_INTERVAL_SECONDS,
    RateLimitService,
    RefreshLockBusy,
    RefreshRecoveryExpired,
    RefreshRecovery,
)

if hasattr(asyncio, "WindowsSelectorEventLoopPolicy"):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())


class FakeSlidingWindow:
    def __init__(self, *, max_requests, window, unit="s"):
        self.max_requests = max_requests
        self.window = window


class FakeLimitResult:
    def __init__(self, allowed, reset):
        self.allowed = allowed
        self.reset = reset


class FakeRatelimit:
    def __init__(self, *, redis, limiter, prefix):
        self.redis = redis
        self.limiter = limiter
        self.prefix = prefix

    async def limit(self, identifier):
        key = f"{self.prefix}:{identifier}"
        count = await self.redis.increment(key)
        return FakeLimitResult(count <= self.limiter.max_requests, time.time() + self.limiter.window)


class FakeRedis:
    """Small atomic in-memory Redis substitute; no network or credentials used."""

    def __init__(self):
        self.values = {}
        self.expirations = {}
        self.counters = {}
        self.sets = {}
        self.zsets = {}
        self.lock = asyncio.Lock()
        self.fail_next_rotation = False
        self.fail_rotation_attempts = 0
        self.fail_after_rotation_commit = False
        self.fail_next_mark = False
        self.commands = []

    def _purge_expired(self):
        now = time.time()
        for key, deadline in list(self.expirations.items()):
            if deadline <= now:
                self.expirations.pop(key, None)
                self.values.pop(key, None)

    async def increment(self, key):
        async with self.lock:
            self.counters[key] = self.counters.get(key, 0) + 1
            return self.counters[key]

    async def get(self, key):
        async with self.lock:
            self._purge_expired()
            return self.values.get(key)

    async def smembers(self, key):
        async with self.lock:
            self._purge_expired()
            return set(self.sets.get(key, set()))

    async def zremrangebyscore(self, key, minimum, maximum):
        async with self.lock:
            self._purge_expired()
            members = self.zsets.get(key, {})
            ceiling = float(maximum)
            removed = [member for member, score in members.items() if score <= ceiling]
            for member in removed:
                members.pop(member, None)
            return len(removed)

    async def zcard(self, key):
        async with self.lock:
            self._purge_expired()
            return len(self.zsets.get(key, {}))

    async def execute(self, *, command):
        async with self.lock:
            self._purge_expired()
            self.commands.append(command)
            if command[0] == "DEL":
                deleted = 0
                for key in command[1:]:
                    deleted += int(key in self.values or key in self.sets)
                    self.values.pop(key, None)
                    self.sets.pop(key, None)
                    self.expirations.pop(key, None)
                return deleted
            if command[0] == "ZREMRANGEBYSCORE":
                _, key, _minimum, maximum = command
                members = self.zsets.get(key, {})
                ceiling = float(maximum)
                removed = [member for member, score in members.items() if score <= ceiling]
                for member in removed:
                    members.pop(member, None)
                return len(removed)
            if command[0] == "ZCARD":
                return len(self.zsets.get(command[1], {}))
            if command[0] == "ZADD":
                _, key, *values = command
                nx = values[0] == "NX"
                if nx:
                    values = values[1:]
                score, member = float(values[0]), values[1]
                members = self.zsets.setdefault(key, {})
                if nx and member in members:
                    return 0
                members[member] = score
                return 1
            _, script, count_text, *rest = command
            count = int(count_text)
            keys, args = rest[:count], rest[count:]
            if "CC_ENSURE_SESSION" in script:
                if keys[0] in self.values:
                    old = json.loads(self.values[keys[0]])
                    if old.get("session_id") != args[0] or old.get("state") not in {"current", "consumed"}:
                        return [-1, ""]
                    if old.get("expires_at") is not None and float(old["expires_at"]) <= float(args[3]):
                        return [-1, ""]
                    if keys[4] in self.values:
                        state = json.loads(self.values[keys[4]])
                        if state.get("status") != "open" or state["expires_at"] <= float(args[3]):
                            return [-1, ""]
                        return [0, args[0]]
                    if old.get("state") != "current" or self.values.get(keys[1]) != args[1]:
                        return [-1, ""]
                    old["expires_at"] = float(args[8])
                    self.values[keys[0]] = json.dumps(old)
                    self.expirations[keys[0]] = time.time() + int(args[6])
                    self.expirations[keys[1]] = time.time() + int(args[6])
                    self.expirations[keys[2]] = time.time() + int(args[6])
                    self.values[keys[3]] = args[0]
                    state_value = json.loads(args[5])
                    self.values[keys[4]] = json.dumps(state_value)
                    self.values[keys[5]] = args[7]
                    self.expirations[keys[4]] = time.time() + int(args[6])
                    self.expirations[keys[5]] = time.time() + int(args[6])
                    self.zsets.setdefault(keys[6], {})[args[9]] = float(args[8])
                    return [0, args[0]]
                if keys[3] in self.values:
                    return [-1, self.values[keys[3]]]
                self.values[keys[0]] = json.dumps({"session_id": args[0], "state": "current", "recovery_until": 0, "expires_at": float(args[8])})
                self.values[keys[1]] = args[1]
                self.expirations[keys[0]] = time.time() + int(args[6])
                self.expirations[keys[1]] = time.time() + int(args[6])
                self.sets.setdefault(keys[2], set()).add(args[2])
                self.expirations[keys[2]] = time.time() + int(args[6])
                self.values[keys[3]] = args[0]
                self.values[keys[4]] = args[5]
                self.values[keys[5]] = args[7]
                self.expirations[keys[4]] = time.time() + int(args[6])
                self.expirations[keys[5]] = time.time() + int(args[6])
                self.zsets.setdefault(keys[6], {})[args[9]] = float(args[8])
                return [1, args[0]]
            if "CC_REWRAP_SESSION_TOKEN" in script:
                state = json.loads(self.values[keys[0]])
                if state["status"] != "open" or state["expires_at"] <= float(args[3]) or self.values.get(keys[1]) != args[0]:
                    return 0
                self.values[keys[1]] = args[1]
                self.expirations[keys[1]] = time.time() + int(args[2])
                self.zsets.setdefault(keys[2], {}).pop(args[4], None)
                self.zsets.setdefault(keys[3], {})[args[6]] = float(args[5])
                return 1
            if "CC_BIND_SESSION_USER" in script:
                state = json.loads(self.values[keys[0]])
                if state.get("status") != "open" or state["expires_at"] <= float(args[1]):
                    return -1
                if state.get("user_digest") and state["user_digest"] != args[0]:
                    return 0
                state["user_digest"] = args[0]
                self.values[keys[0]] = json.dumps(state)
                self.expirations[keys[0]] = time.time() + int(args[2])
                return 1
            if "CC_CLOSE_SESSION" in script:
                now = float(args[0])
                state_raw = self.values.get(keys[0])
                if state_raw:
                    state = json.loads(state_raw)
                    if state.get("session_id") != args[2]:
                        return [-1, ""]
                else:
                    state = {"version": 1, "session_id": args[2], "status": "closed", "expires_at": now + int(args[6])}
                envelope = self.values.get(keys[1], "")
                state["status"] = "closed"
                self.values[keys[0]] = json.dumps(state)
                self.expirations[keys[0]] = now + max(1, state["expires_at"] - now)
                self.values.pop(keys[3], None)
                self.values.pop(keys[4], None)
                self.values.pop(keys[5], None)
                self.expirations.pop(keys[3], None)
                self.expirations.pop(keys[4], None)
                self.expirations.pop(keys[5], None)
                for token_digest in self.sets.get(keys[2], set()):
                    self.values[args[1] + token_digest] = args[2]
                    recovery_key = args[4] + args[2] + ":" + token_digest
                    recovery_raw = self.values.pop(recovery_key, None)
                    self.expirations.pop(recovery_key, None)
                    if recovery_raw:
                        try:
                            recovery_key_id = json.loads(recovery_raw).get("key_id")
                        except (TypeError, ValueError):
                            recovery_key_id = None
                        if recovery_key_id:
                            self.zsets.setdefault(args[5] + recovery_key_id, {}).pop(
                                args[2] + ":recovery:" + token_digest, None
                            )
                self.values.pop(keys[1], None)
                self.sets.pop(keys[2], None)
                if envelope:
                    key_id = json.loads(envelope)["key_id"]
                    self.zsets.setdefault(args[5] + key_id, {}).pop(args[2], None)
                return [1, envelope]
            if "CC_ACQUIRE_REFRESH" in script:
                self._purge_expired()
                if keys[0] in self.values:
                    return [0, ""]
                started = args[2]
                phase = "prepared"
                prior_raw = self.values.get(keys[1])
                if prior_raw:
                    prior = json.loads(prior_raw)
                    if prior.get("token_digest") == args[3] and prior.get("phase", "ambiguous") != "prepared":
                        started = str(prior["started_at"])
                        phase = "ambiguous"
                self.values[keys[0]] = args[0]
                self.expirations[keys[0]] = time.time() + int(args[1]) / 1000
                self.values[keys[1]] = json.dumps(
                    {
                        "token_digest": args[3],
                        "started_at": float(started),
                        "phase": phase,
                        "lock_owner": args[0],
                    }
                )
                if phase == "prepared":
                    self.expirations[keys[1]] = time.time() + int(args[4]) / 1000
                else:
                    self.expirations.pop(keys[1], None)
                return [1, started]
            if "CC_MARK_REFRESH_AMBIGUOUS" in script:
                if self.fail_next_mark:
                    self.fail_next_mark = False
                    raise OSError("mocked marker failure")
                owner, token_digest = args
                raw = self.values.get(keys[1])
                if self.values.get(keys[0]) != owner or raw is None:
                    return 0
                marker = json.loads(raw)
                if marker.get("token_digest") != token_digest:
                    return 0
                marker["phase"] = "ambiguous"
                self.values[keys[1]] = json.dumps(marker)
                self.expirations.pop(keys[1], None)
                return 1
            if "CC_EXPIRE_REFRESH_ATTEMPT" in script:
                raw = self.values.get(keys[1])
                if raw is None:
                    return 2
                marker = json.loads(raw)
                if marker.get("token_digest") != args[1]:
                    return -1
                started = float(marker["started_at"])
                phase = marker.get("phase", "ambiguous")
                if phase != "ambiguous" or started + float(args[2]) > float(args[3]):
                    return 2
                lock_owner = self.values.get(keys[0])
                if lock_owner is not None:
                    if marker.get("lock_owner") != lock_owner:
                        return 0
                    self.values.pop(keys[0], None)
                    self.expirations.pop(keys[0], None)
                mapped_raw = self.values.get(keys[2])
                if mapped_raw is not None and json.loads(mapped_raw).get("session_id") != args[0]:
                    return -1
                self.values.pop(keys[2], None)
                self.expirations.pop(keys[2], None)
                self.values[keys[3]] = args[0]
                self.values.pop(keys[1], None)
                self.expirations.pop(keys[1], None)
                return 1
            if "CC_ROTATE_REFRESH" in script:
                self_test_ttl_command = "SET', KEYS[11], ARGV[9], 'PX', ARGV[10]"
                if self_test_ttl_command not in script:
                    raise AssertionError("Recovery record TTL must use the millisecond PX unit")
                if self.fail_next_rotation or self.fail_rotation_attempts > 0:
                    self.fail_next_rotation = False
                    if self.fail_rotation_attempts > 0:
                        self.fail_rotation_attempts -= 1
                    raise OSError("mocked Redis outage")
                owner, current_digest, session_id = args[:3]
                deadline, new_digest, now = float(args[3]), args[4], float(args[5])
                session_state = json.loads(self.values[keys[11]])
                if session_state.get("status") != "open" or session_state["expires_at"] <= now:
                    return -2
                if session_state.get("user_digest") and session_state["user_digest"] != args[11]:
                    return -2
                existing_recovery = self.values.get(keys[10])
                if existing_recovery is not None:
                    if existing_recovery != args[8]:
                        return -1
                    pointer_now = self.values.get(keys[2])
                    old_now = self.values.get(keys[0])
                    new_now = self.values.get(keys[1])
                    if old_now and new_now and pointer_now == new_digest:
                        old_value_now = json.loads(old_now)
                        new_value_now = json.loads(new_now)
                        if (
                            old_value_now.get("session_id") == session_id
                            and new_value_now.get("session_id") == session_id
                            and new_value_now.get("state") == "current"
                        ):
                            return 2
                    return -1
                old_raw = self.values.get(keys[0])
                if self.values.get(keys[4]) != owner or self.values.get(keys[2]) != current_digest or old_raw is None:
                    return 0
                old = json.loads(old_raw)
                if old["session_id"] != session_id:
                    return 0
                current_raw = self.values.get(keys[3])
                if current_raw is None:
                    return 0
                current = json.loads(current_raw)
                if current["session_id"] != session_id:
                    return 0
                if current_digest != new_digest:
                    current["state"] = "consumed"
                    current["recovery_until"] = deadline
                    if deadline > now:
                        self.values[keys[3]] = json.dumps(current)
                        self.expirations[keys[3]] = time.time() + int(args[6]) / 1000
                    else:
                        self.values.pop(keys[3], None)
                        self.expirations.pop(keys[3], None)
                    self.values[keys[8]] = session_id
                new_raw = self.values.get(keys[1])
                if new_raw and json.loads(new_raw)["session_id"] != session_id:
                    return 0
                self.values[keys[1]] = args[7]
                remaining = max(1, session_state["expires_at"] - now)
                self.expirations[keys[1]] = time.time() + remaining
                self.values[keys[2]] = new_digest
                self.expirations[keys[2]] = time.time() + remaining
                self.sets.setdefault(keys[6], set()).add(new_digest)
                self.expirations[keys[6]] = time.time() + remaining
                self.values[keys[10]] = args[8]
                self.expirations[keys[10]] = time.time() + int(args[9]) / 1000
                self.values[keys[9]] = session_id
                prior_envelope = json.loads(self.values[keys[12]])
                self.zsets.setdefault("careercoach:auth:encrypted-sessions-by-key:v1:" + prior_envelope["key_id"], {}).pop(session_id, None)
                self.values[keys[12]] = args[10]
                self.expirations[keys[12]] = time.time() + max(1, session_state["expires_at"] - now)
                if args[11]:
                    session_state["user_digest"] = args[11]
                self.values[keys[11]] = json.dumps(session_state)
                self.expirations[keys[11]] = time.time() + max(1, session_state["expires_at"] - now)
                self.zsets.setdefault(keys[13], {})[args[12]] = session_state["expires_at"]
                self.zsets.setdefault(keys[13], {})[
                    session_id + ":recovery:" + args[1]
                ] = deadline
                if current_digest != new_digest:
                    self.values[keys[8]] = session_id
                self.values.pop(keys[5], None)
                self.values.pop(keys[4], None)
                self.expirations.pop(keys[5], None)
                self.expirations.pop(keys[4], None)
                if self.fail_after_rotation_commit:
                    self.fail_after_rotation_commit = False
                    raise OSError("mocked lost Redis response after commit")
                return 1
            if "CC_RELEASE_REFRESH" in script:
                owns_lock = self.values.get(keys[0]) == args[0]
                if owns_lock:
                    self.values.pop(keys[0], None)
                    self.expirations.pop(keys[0], None)
                if owns_lock and args[2] == "1":
                    raw = self.values.get(keys[1])
                    if raw and json.loads(raw).get("token_digest") == args[1]:
                        self.values.pop(keys[1], None)
                        self.expirations.pop(keys[1], None)
                return 1
            raise AssertionError("Unexpected mocked Redis script")

    async def close(self):
        return None


def make_request(host="203.0.113.7", headers=None):
    header_items = [(k.lower().encode(), v.encode()) for k, v in (headers or {}).items()]
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "http_version": "1.1",
        "method": "POST",
        "scheme": "http",
        "path": "/api/auth/login",
        "raw_path": b"/api/auth/login",
        "query_string": b"",
        "headers": header_items,
        "client": (host, 12345),
        "server": ("localhost", 8000),
    }
    return Request(scope)


class RateLimitTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.redis = FakeRedis()
        self.service = RateLimitService(
            redis_client=self.redis,
            hmac_secret=b"s" * 32,
            recovery_encryption_key=b"e" * 32,
            recovery_encryption_key_id="test-key",
            limiter_class=FakeRatelimit,
            sliding_window_class=FakeSlidingWindow,
        )
        await self.service.startup()

    def _make_recovery_envelope(self, attempt, refresh_token):
        return self.service.encrypt_refresh_response(
            attempt,
            {
                "access_token": "mock-access-token-for-" + refresh_token,
                "refresh_token": refresh_token,
                "access_expires_at": time.time() + 3600,
                "csrf_token": "csrf-token-" + ("x" * 32),
            },
        )

    async def _rotate(self, attempt, refresh_token):
        envelope = self._make_recovery_envelope(attempt, refresh_token)
        await self.service.rotate_session(attempt, refresh_token, envelope)
        return envelope

    async def asyncTearDown(self):
        await self.service.shutdown()

    async def test_login_email_boundary_and_casefolded_hmac(self):
        request = make_request()
        for _ in range(5):
            await self.service.check_credentials(request, " User@Example.com ", registration=False)
        with self.assertRaises(HTTPException) as raised:
            await self.service.check_credentials(request, "user@example.COM", registration=False)
        self.assertEqual(raised.exception.status_code, 429)
        self.assertIn("Retry-After", raised.exception.headers)

    async def test_registration_email_and_ip_boundaries(self):
        request = make_request()
        for _ in range(3):
            await self.service.check_credentials(request, "same@example.com", registration=True)
        with self.assertRaises(HTTPException) as raised:
            await self.service.check_credentials(request, "same@example.com", registration=True)
        self.assertEqual(raised.exception.status_code, 429)
        self.assertEqual(raised.exception.detail, "Too many attempts. Please try again later.")

    async def test_registration_ip_boundary(self):
        request = make_request()
        for index in range(5):
            await self.service.check_credentials(request, f"user{index}@example.com", registration=True)
        with self.assertRaises(HTTPException) as raised:
            await self.service.check_credentials(request, "sixth@example.com", registration=True)
        self.assertEqual(raised.exception.status_code, 429)

    async def test_login_ip_boundary(self):
        request = make_request()
        for index in range(30):
            await self.service.check_credentials(request, f"login{index}@example.com", registration=False)
        with self.assertRaises(HTTPException) as raised:
            await self.service.check_credentials(request, "login-31@example.com", registration=False)
        self.assertEqual(raised.exception.status_code, 429)

    async def test_refresh_session_five_minute_boundary(self):
        session_id = await self.service.establish_session("refresh-session-boundary")
        for _ in range(10):
            await self.service.check_refresh_session(session_id)
        with self.assertRaises(HTTPException) as raised:
            await self.service.check_refresh_session(session_id)
        self.assertEqual(raised.exception.status_code, 429)

    async def test_refresh_session_hourly_boundary(self):
        session_id = await self.service.establish_session("refresh-hourly-boundary")
        for _ in range(60):
            await self.service._consume("refresh_session_1h", session_id)
        with self.assertRaises(HTTPException) as raised:
            await self.service._consume("refresh_session_1h", session_id)
        self.assertEqual(raised.exception.status_code, 429)

    async def test_refresh_ip_hourly_boundary(self):
        request = make_request()
        for _ in range(120):
            await self.service.check_refresh_ip(request)
        with self.assertRaises(HTTPException) as raised:
            await self.service.check_refresh_ip(request)
        self.assertEqual(raised.exception.status_code, 429)

    async def test_identifiers_and_mapping_do_not_store_raw_identifiers_or_tokens(self):
        raw_email = "person@example.com"
        raw_ip = "203.0.113.7"
        raw_token = "refresh-secret-value"
        await self.service.check_credentials(make_request(raw_ip), raw_email, registration=False)
        session_id = await self.service.establish_session(raw_token)
        flattened = repr((self.redis.counters, self.redis.values, self.redis.commands))
        self.assertNotIn(raw_email, flattened)
        self.assertNotIn(raw_ip, flattened)
        self.assertNotIn(raw_token, flattened)
        self.assertTrue(session_id)

    async def test_recovery_payload_is_authenticated_and_contains_no_plaintext_tokens(self):
        old_token = "recovery-envelope-old-token"
        access_token = "recovery-envelope-access-secret"
        refresh_token = "recovery-envelope-refresh-secret"
        csrf_token = "recovery-envelope-csrf-secret-1234567890"
        await self.service.establish_session(old_token)
        attempt = await self.service.prepare_refresh(old_token)
        await self.service.mark_refresh_ambiguous(attempt)
        payload = {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "access_expires_at": time.time() + 3600,
            "csrf_token": csrf_token,
        }
        envelope_raw = self.service.encrypt_refresh_response(attempt, payload)
        self.assertNotIn(access_token, envelope_raw)
        self.assertNotIn(refresh_token, envelope_raw)
        self.assertNotIn(csrf_token, envelope_raw)
        self.assertEqual(
            self.service.decrypt_refresh_response(
                envelope_raw,
                session_id=attempt.session_id,
                token_digest=attempt.token_digest,
            )["refresh_token"],
            refresh_token,
        )
        with self.assertRaises(HTTPException) as wrong_session:
            self.service.decrypt_refresh_response(
                envelope_raw,
                session_id="another-session",
                token_digest=attempt.token_digest,
            )
        self.assertEqual(wrong_session.exception.status_code, 503)
        with self.assertRaises(HTTPException) as wrong_digest:
            self.service.decrypt_refresh_response(
                envelope_raw,
                session_id=attempt.session_id,
                token_digest="0" * 64,
            )
        self.assertEqual(wrong_digest.exception.status_code, 503)

        envelope = json.loads(envelope_raw)
        envelope["key_id"] = "unknown-key"
        with self.assertRaises(HTTPException):
            self.service.decrypt_refresh_response(
                json.dumps(envelope),
                session_id=attempt.session_id,
                token_digest=attempt.token_digest,
            )
        envelope = json.loads(envelope_raw)
        first_character = envelope["ciphertext"][0]
        envelope["ciphertext"] = ("A" if first_character != "A" else "B") + envelope["ciphertext"][1:]
        with self.assertRaises(HTTPException):
            self.service.decrypt_refresh_response(
                json.dumps(envelope),
                session_id=attempt.session_id,
                token_digest=attempt.token_digest,
            )
        await self.service.release_refresh(attempt, clear_attempt=True)

    async def test_invalid_recovery_key_fails_closed(self):
        service = RateLimitService(
            redis_client=FakeRedis(),
            hmac_secret=b"s" * 32,
            recovery_encryption_key=b"too-short",
            recovery_encryption_key_id="test-key",
            limiter_class=FakeRatelimit,
            sliding_window_class=FakeSlidingWindow,
        )
        await service.startup()
        try:
            with self.assertRaises(HTTPException) as raised:
                service.ensure_refresh_recovery_ready()
            self.assertEqual(raised.exception.status_code, 503)
        finally:
            await service.shutdown()

    async def test_previous_encryption_key_decrypts_unexpired_envelope(self):
        old_token = "previous-key-old-token"
        await self.service.establish_session(old_token)
        attempt = await self.service.prepare_refresh(old_token)
        await self.service.mark_refresh_ambiguous(attempt)
        envelope = self._make_recovery_envelope(attempt, "previous-key-new-token")
        rotating_service = RateLimitService(
            redis_client=FakeRedis(),
            hmac_secret=b"s" * 32,
            recovery_encryption_key=b"n" * 32,
            recovery_encryption_key_id="new-key",
            previous_recovery_encryption_key=b"e" * 32,
            previous_recovery_encryption_key_id="test-key",
            limiter_class=FakeRatelimit,
            sliding_window_class=FakeSlidingWindow,
        )
        await rotating_service.startup()
        try:
            payload = rotating_service.decrypt_refresh_response(
                envelope,
                session_id=attempt.session_id,
                token_digest=attempt.token_digest,
            )
            self.assertEqual(payload["refresh_token"], "previous-key-new-token")
        finally:
            await rotating_service.shutdown()
            await self.service.release_refresh(attempt, clear_attempt=True)

    async def test_expired_or_corrupted_recovery_record_fails_closed(self):
        old_token = "recovery-invalid-record-old-token"
        await self.service.establish_session(old_token)
        attempt = await self.service.prepare_refresh(old_token)
        await self.service.mark_refresh_ambiguous(attempt)
        envelope = await self._rotate(attempt, "recovery-invalid-record-new-token")
        recovery_key = self.service._recovery_key(attempt.session_id, attempt.token_digest)

        self.redis.values[recovery_key] = "not-an-envelope"
        with self.assertRaises(HTTPException) as corrupted:
            await self.service.prepare_refresh(old_token)
        self.assertEqual(corrupted.exception.status_code, 503)

        self.redis.values[recovery_key] = envelope
        mapping_key = self.service._mapping_key(attempt.token_digest)
        mapping = self.service._decode_mapping(await self.redis.get(mapping_key))
        mapping["recovery_until"] = time.time() - 1
        self.redis.values[mapping_key] = json.dumps(mapping)
        with self.assertRaises(RefreshRecoveryExpired):
            await self.service.prepare_refresh(old_token)

    async def test_forwarded_ip_is_ignored(self):
        first = self.service._digest("ip", self.service._client_ip(make_request("203.0.113.7", {"X-Forwarded-For": "198.51.100.8"})))
        second = self.service._digest("ip", self.service._client_ip(make_request("203.0.113.7", {"Forwarded": "for=198.51.100.9"})))
        self.assertEqual(first, second)

    async def test_refresh_limits_and_rotation_mapping(self):
        original = "old-refresh-token"
        rotated = "new-refresh-token"
        session_id = await self.service.establish_session(original)
        attempt = await self.service.prepare_refresh(original)
        await self.service.mark_refresh_ambiguous(attempt)
        self.assertEqual(attempt.session_id, session_id)
        envelope = await self._rotate(attempt, rotated)
        old_digest = self.service._digest("refresh-token", original)
        new_digest = self.service._digest("refresh-token", rotated)
        old_map = json.loads(await self.redis.get(self.service._mapping_key(old_digest)))
        new_map = json.loads(await self.redis.get(self.service._mapping_key(new_digest)))
        self.assertEqual(old_map["state"], "consumed")
        self.assertEqual(old_map["session_id"], session_id)
        self.assertGreater(old_map["recovery_until"], time.time())
        self.assertLessEqual(old_map["recovery_until"], attempt.started_at + REFRESH_REUSE_INTERVAL_SECONDS)
        self.assertEqual(new_map["state"], "current")
        self.assertEqual(new_map["session_id"], session_id)
        recovery_value = await self.redis.get(
            self.service._recovery_key(session_id, old_digest)
        )
        self.assertEqual(recovery_value, envelope)
        self.assertNotIn("old-refresh-token", recovery_value)
        self.assertNotIn("new-refresh-token", recovery_value)
        self.assertNotIn("mock-access-token", recovery_value)
        self.assertNotIn("csrf-token-" + ("x" * 32), recovery_value)
        recovered = await self.service.prepare_refresh(original)
        self.assertIsInstance(recovered, RefreshRecovery)
        self.assertEqual(recovered.payload["refresh_token"], rotated)
        self.assertEqual(recovered.payload["access_token"], "mock-access-token-for-new-refresh-token")

        # Once the short grace alias expires, its durable HMAC tombstone prevents
        # the stale token from being treated as an untracked legacy session.
        old_map["recovery_until"] = time.time() - 1
        self.redis.values[self.service._mapping_key(old_digest)] = json.dumps(old_map)
        self.redis.values.pop(self.service._mapping_key(old_digest), None)
        with self.assertRaises(RefreshRecoveryExpired):
            await self.service.prepare_refresh(original)

    async def test_consumed_tombstone_blocks_legacy_reassignment(self):
        token = "old-alias"
        await self.service.establish_session(token)
        attempt = await self.service.prepare_refresh(token)
        await self.service.mark_refresh_ambiguous(attempt)
        await self._rotate(attempt, "replacement-token")
        digest = self.service._digest("refresh-token", token)
        self.redis.values.pop(self.service._mapping_key(digest), None)
        self.assertEqual(
            await self.redis.get(self.service._replay_key(digest)),
            attempt.session_id,
        )
        with self.assertRaises(RefreshRecoveryExpired):
            await self.service.prepare_refresh(token)

    async def test_logout_closes_session_and_preserves_closed_mapping_state(self):
        old_token = "logout-old-token"
        new_token = "logout-new-token"
        await self.service.establish_session(old_token)
        attempt = await self.service.prepare_refresh(old_token)
        await self._rotate(attempt, new_token)
        old_digest = self.service._digest("refresh-token", old_token)
        new_digest = self.service._digest("refresh-token", new_token)
        await self.service.clear_session(new_token)
        old_mapping = self.service._decode_mapping(await self.redis.get(self.service._mapping_key(old_digest)))
        self.assertEqual(old_mapping["state"], "consumed")
        self.assertEqual(await self.redis.get(self.service._replay_key(old_digest)), attempt.session_id)
        self.assertIsNone(
            await self.redis.get(self.service._recovery_key(attempt.session_id, old_digest))
        )
        new_mapping = self.service._decode_mapping(await self.redis.get(self.service._mapping_key(new_digest)))
        self.assertEqual(new_mapping["state"], "current")
        self.assertIsNone(await self.redis.get(self.service._pointer_key(attempt.session_id)))
        state = self.service._decode_session_state(
            await self.redis.get(self.service._session_state_key(attempt.session_id)), attempt.session_id
        )
        self.assertEqual(state["status"], "closed")
        self.assertIsNone(await self.redis.get(self.service._session_refresh_key(attempt.session_id)))
        with self.assertRaises(RefreshRecoveryExpired):
            await self.service.prepare_refresh(new_token)

    async def test_legacy_session_is_assigned_opaque_id(self):
        attempt = await self.service.prepare_refresh("legacy-refresh-token")
        self.assertTrue(attempt.session_id)
        mapping = json.loads(await self.redis.get(self.service._mapping_key(attempt.token_digest)))
        self.assertEqual(mapping["session_id"], attempt.session_id)
        await self.service.release_refresh(attempt)

    async def test_stable_session_record_encrypts_token_and_has_absolute_expiry(self):
        token = "persistent-refresh-token-secret"
        session_id = await self.service.establish_session(token)
        raw = await self.redis.get(self.service._session_refresh_key(session_id))
        self.assertIsNotNone(raw)
        self.assertNotIn(token, raw)
        self.assertEqual(
            (await self.service.get_session_refresh_token(session_id))[0], token
        )
        state = self.service._decode_session_state(
            await self.redis.get(self.service._session_state_key(session_id)), session_id
        )
        mapping = self.service._decode_mapping(
            await self.redis.get(self.service._mapping_key(self.service._digest("refresh-token", token)))
        )
        self.assertAlmostEqual(
            state["expires_at"] - state["created_at"],
            30 * 24 * 60 * 60,
            delta=2,
        )
        self.assertEqual(mapping["expires_at"], state["expires_at"])
        self.assertGreater(self.redis.expirations[self.service._session_refresh_key(session_id)], time.time())

        attempt = await self.service.prepare_refresh(token)
        await self.service.mark_refresh_ambiguous(attempt)
        rotated_token = "persistent-refresh-token-after-rotation"
        await self._rotate(attempt, rotated_token)
        rotated_state = self.service._decode_session_state(
            await self.redis.get(self.service._session_state_key(session_id)), session_id
        )
        encrypted = await self.redis.get(self.service._session_refresh_key(session_id))
        self.assertEqual(rotated_state["expires_at"], state["expires_at"])
        self.assertNotIn(rotated_token, encrypted)
        self.assertNotIn(rotated_token, repr(self.redis.values))
        self.assertEqual(
            self.service._decrypt_session_refresh(encrypted, session_id, state["expires_at"])[0],
            rotated_token,
        )

    async def test_expired_absolute_session_cannot_be_renewed_by_refresh_or_legacy_migration(self):
        token = "absolute-expiry-refresh"
        session_id = await self.service.establish_session(token)
        state_key = self.service._session_state_key(session_id)
        state = json.loads(await self.redis.get(state_key))
        state["expires_at"] = time.time() - 1
        self.redis.values[state_key] = json.dumps(state)
        with self.assertRaises(HTTPException) as expired:
            await self.service.get_session_refresh_token(session_id)
        self.assertEqual(expired.exception.status_code, 401)
        with self.assertRaises(HTTPException) as no_renewal:
            await self.service.establish_session(token)
        self.assertEqual(no_renewal.exception.status_code, 503)
        digest = self.service._digest("refresh-token", token)
        self.redis.values.pop(self.service._mapping_key(digest), None)
        self.redis.values.pop(self.service._pointer_key(session_id), None)
        self.redis.values.pop(self.service._session_state_key(session_id), None)
        self.redis.values.pop(self.service._session_refresh_key(session_id), None)
        self.redis.expirations.pop(self.service._mapping_key(digest), None)
        self.redis.expirations.pop(self.service._pointer_key(session_id), None)
        self.redis.expirations.pop(self.service._session_state_key(session_id), None)
        self.redis.expirations.pop(self.service._session_refresh_key(session_id), None)
        with self.assertRaises(HTTPException) as replay_block:
            await self.service.establish_session(token)
        self.assertEqual(replay_block.exception.status_code, 503)

    async def test_legacy_cookie_migration_reuses_existing_sid_and_encrypts_token(self):
        token = "legacy-browser-refresh-token"
        sid = await self.service.establish_session(token)
        digest = self.service._digest("refresh-token", token)
        self.redis.values.pop(self.service._session_state_key(sid), None)
        self.redis.values.pop(self.service._session_refresh_key(sid), None)
        self.redis.expirations.pop(self.service._session_state_key(sid), None)
        self.redis.expirations.pop(self.service._session_refresh_key(sid), None)
        migrated_sid = await self.service.establish_session(token)
        self.assertEqual(migrated_sid, sid)
        self.assertEqual((await self.service.get_session_refresh_token(sid))[0], token)
        self.assertNotIn(token, await self.redis.get(self.service._session_refresh_key(sid)))
        self.assertEqual(
            self.service._decode_mapping(await self.redis.get(self.service._mapping_key(digest)))["session_id"],
            sid,
        )

    async def test_consumed_legacy_cookie_migrates_only_through_live_encrypted_recovery(self):
        old_token = "legacy-consumed-old-token"
        new_token = "legacy-consumed-current-token"
        sid = await self.service.establish_session(old_token)
        attempt = await self.service.prepare_refresh(old_token)
        await self.service.mark_refresh_ambiguous(attempt)
        await self._rotate(attempt, new_token)
        self.redis.values.pop(self.service._session_state_key(sid), None)
        self.redis.values.pop(self.service._session_refresh_key(sid), None)
        self.redis.expirations.pop(self.service._session_state_key(sid), None)
        self.redis.expirations.pop(self.service._session_refresh_key(sid), None)

        migrated_sid = await self.service.establish_session(old_token)
        self.assertEqual(migrated_sid, sid)
        self.assertEqual((await self.service.get_session_refresh_token(sid))[0], new_token)
        self.assertNotIn(new_token, await self.redis.get(self.service._session_refresh_key(sid)))

    async def test_consumed_legacy_cookie_fails_closed_after_recovery_deadline(self):
        old_token = "legacy-expired-old-token"
        sid = await self.service.establish_session(old_token)
        attempt = await self.service.prepare_refresh(old_token)
        await self.service.mark_refresh_ambiguous(attempt)
        await self._rotate(attempt, "legacy-expired-current-token")
        mapping_key = self.service._mapping_key(attempt.token_digest)
        mapping = json.loads(await self.redis.get(mapping_key))
        mapping["recovery_until"] = time.time() - 1
        self.redis.values[mapping_key] = json.dumps(mapping)
        recovery_key = self.service._recovery_key(sid, attempt.token_digest)
        self.redis.values.pop(recovery_key, None)
        self.redis.values.pop(self.service._session_state_key(sid), None)
        self.redis.values.pop(self.service._session_refresh_key(sid), None)
        with self.assertRaises(HTTPException) as raised:
            await self.service.establish_session(old_token)
        self.assertEqual(raised.exception.status_code, 503)

    async def test_encrypted_session_rewraps_with_active_key_before_previous_key_retirement(self):
        token = "key-rotation-persistent-session"
        sid = await self.service.establish_session(token)
        attempt = await self.service.prepare_refresh(token)
        await self.service.mark_refresh_ambiguous(attempt)
        current_token = "key-rotation-current-token"
        await self._rotate(attempt, current_token)
        rotated = RateLimitService(
            redis_client=self.redis,
            hmac_secret=b"s" * 32,
            recovery_encryption_key=b"n" * 32,
            recovery_encryption_key_id="new-key",
            previous_recovery_encryption_key=b"e" * 32,
            previous_recovery_encryption_key_id="test-key",
            limiter_class=FakeRatelimit,
            sliding_window_class=FakeSlidingWindow,
        )
        await rotated.startup()
        try:
            before = json.loads(await self.redis.get(rotated._session_refresh_key(sid)))
            self.assertEqual(before["key_id"], "test-key")
            self.assertFalse(await rotated.previous_key_retirement_ready("test-key"))
            self.assertEqual((await rotated.get_session_refresh_token(sid))[0], current_token)
            after = json.loads(await self.redis.get(rotated._session_refresh_key(sid)))
            self.assertEqual(after["key_id"], "new-key")
            index = rotated._key_session_index("test-key")
            retirement_deadline = max(self.redis.zsets[index].values())
            with patch("app.core.rate_limit.time.time", return_value=retirement_deadline + 1):
                self.assertTrue(await rotated.previous_key_retirement_ready("test-key"))
        finally:
            await rotated.shutdown()

    async def test_session_envelope_missing_previous_key_fails_closed(self):
        token = "missing-previous-session-key"
        sid = await self.service.establish_session(token)
        rotated = RateLimitService(
            redis_client=self.redis,
            hmac_secret=b"s" * 32,
            recovery_encryption_key=b"n" * 32,
            recovery_encryption_key_id="new-key",
            limiter_class=FakeRatelimit,
            sliding_window_class=FakeSlidingWindow,
        )
        await rotated.startup()
        try:
            with self.assertRaises(HTTPException) as raised:
                await rotated.get_session_refresh_token(sid)
            self.assertEqual(raised.exception.status_code, 503)
        finally:
            await rotated.shutdown()

    async def test_logout_wins_atomic_race_against_refresh_commit(self):
        token = "logout-races-rotation-token"
        sid = await self.service.establish_session(token)
        attempt = await self.service.prepare_refresh(token)
        await self.service.mark_refresh_ambiguous(attempt)
        await self.service.close_session(sid)
        with self.assertRaises(HTTPException) as closed:
            await self._rotate(attempt, "late-rotated-token")
        self.assertEqual(closed.exception.status_code, 503)
        state = self.service._decode_session_state(
            await self.redis.get(self.service._session_state_key(sid)), sid
        )
        self.assertEqual(state["status"], "closed")
        self.assertIsNone(await self.redis.get(self.service._session_refresh_key(sid)))

    async def test_concurrent_refreshes_allow_only_one_lock_owner(self):
        await self.service.establish_session("shared-refresh")
        results = await asyncio.gather(
            self.service.prepare_refresh("shared-refresh"),
            self.service.prepare_refresh("shared-refresh"),
            return_exceptions=True,
        )
        attempts = [value for value in results if not isinstance(value, Exception)]
        busy = [value for value in results if isinstance(value, RefreshLockBusy)]
        self.assertEqual(len(attempts), 1)
        self.assertEqual(len(busy), 1)
        await self.service.release_refresh(attempts[0])

    async def test_prepared_attempt_can_be_restarted_without_an_upstream_call(self):
        token = "never-sent-refresh-token"
        first = await self.service.prepare_refresh(token)
        marker_key = self.service._attempt_key(first.session_id)
        self.assertEqual(json.loads(self.redis.values[marker_key])["phase"], "prepared")
        self.assertIn(marker_key, self.redis.expirations)
        lock_key = self.service._lock_key(first.session_id)
        # Simulate a process exit after preparation but before the request marker.
        self.redis.expirations[lock_key] = time.time() - 1
        second = await self.service.prepare_refresh(token)
        self.assertGreaterEqual(second.started_at, first.started_at)
        await self.service.release_refresh(second, clear_attempt=True)

    async def test_ambiguous_attempt_is_bounded_by_verified_reuse_deadline(self):
        token = "ambiguous-refresh-token"
        attempt = await self.service.prepare_refresh(token)
        await self.service.mark_refresh_ambiguous(attempt)
        marker_key = self.service._attempt_key(attempt.session_id)
        marker = json.loads(self.redis.values[marker_key])
        self.assertEqual(marker["phase"], "ambiguous")
        self.assertNotIn(marker_key, self.redis.expirations)
        marker["started_at"] = time.time() - REFRESH_REUSE_INTERVAL_SECONDS - 1
        self.redis.values[marker_key] = json.dumps(marker)
        await self.service.release_refresh(attempt)
        with self.assertRaises(RefreshRecoveryExpired):
            await self.service.prepare_refresh(token)

    async def test_expired_lock_owner_cannot_overwrite_new_owner_mapping(self):
        token = "lease-owner-refresh-token"
        await self.service.establish_session(token)
        first = await self.service.prepare_refresh(token)
        lock_key = self.service._lock_key(first.session_id)
        self.assertGreaterEqual(
            self.redis.expirations[lock_key] - time.time(),
            20,
        )

        # Simulate lease expiry while the old request is still in flight.
        self.redis.expirations[lock_key] = time.time() - 1
        second = await self.service.prepare_refresh(token)
        self.assertNotEqual(first.lock_owner, second.lock_owner)
        with self.assertRaises(HTTPException) as raised:
            await self._rotate(first, "stale-owner-token")
        self.assertEqual(raised.exception.status_code, 503)
        self.assertEqual(
            await self.redis.get(self.service._pointer_key(first.session_id)),
            first.token_digest,
        )
        await self.service.release_refresh(first, clear_attempt=True)
        self.assertEqual(await self.redis.get(lock_key), second.lock_owner)
        await self.service.release_refresh(second, clear_attempt=True)

    async def test_stale_owner_cannot_clear_new_owners_attempt_marker(self):
        token = "stale-release-refresh-token"
        await self.service.establish_session(token)
        first = await self.service.prepare_refresh(token)
        lock_key = self.service._lock_key(first.session_id)
        self.redis.expirations[lock_key] = time.time() - 1
        second = await self.service.prepare_refresh(token)
        marker_key = self.service._attempt_key(second.session_id)
        marker_before = await self.redis.get(marker_key)
        await self.service.release_refresh(first, clear_attempt=True)
        self.assertEqual(await self.redis.get(marker_key), marker_before)
        await self.service.release_refresh(second, clear_attempt=True)

    async def test_precommit_mapping_failure_does_not_retry_an_ambiguous_rotation(self):
        old_token = "recoverable-token"
        await self.service.establish_session(old_token)
        first = await self.service.prepare_refresh(old_token)
        await self.service.mark_refresh_ambiguous(first)
        self.redis.fail_rotation_attempts = 2
        with self.assertRaises(HTTPException) as raised:
            await self._rotate(first, "rotated-token")
        self.assertEqual(raised.exception.status_code, 503)
        await self.service.release_refresh(first)

        with self.assertRaises(HTTPException) as retry_raised:
            await self.service.prepare_refresh(old_token)
        self.assertEqual(retry_raised.exception.status_code, 503)
        old_digest = self.service._digest("refresh-token", old_token)
        self.assertEqual(
            self.service._decode_mapping(
                await self.redis.get(self.service._mapping_key(old_digest))
            )["state"],
            "current",
        )

    async def test_lost_redis_response_after_atomic_commit_can_recover(self):
        old_token = "lost-response-old-token"
        new_token = "lost-response-new-token"
        await self.service.establish_session(old_token)
        first = await self.service.prepare_refresh(old_token)
        await self.service.mark_refresh_ambiguous(first)
        self.redis.fail_after_rotation_commit = True
        await self._rotate(first, new_token)

        old_digest = self.service._digest("refresh-token", old_token)
        new_digest = self.service._digest("refresh-token", new_token)
        self.assertEqual(
            json.loads(await self.redis.get(self.service._mapping_key(new_digest)))["state"],
            "current",
        )
        recovered = await self.service.prepare_refresh(old_token)
        self.assertIsInstance(recovered, RefreshRecovery)
        self.assertEqual(recovered.payload["refresh_token"], new_token)
        self.assertEqual(recovered.payload["access_token"], "mock-access-token-for-lost-response-new-token")
        self.assertIsNotNone(await self.redis.get(self.service._replay_key(old_digest)))

    async def test_pending_rotation_marker_expires_conservatively(self):
        token = "pending-expired-token"
        await self.service.establish_session(token)
        attempt = await self.service.prepare_refresh(token)
        await self.service.mark_refresh_ambiguous(attempt)
        self.redis.values[self.service._attempt_key(attempt.session_id)] = json.dumps(
            {
                "token_digest": attempt.token_digest,
                "started_at": time.time() - REFRESH_REUSE_INTERVAL_SECONDS - 1,
                "phase": "ambiguous",
                "lock_owner": attempt.lock_owner,
            }
        )
        await self.service.release_refresh(attempt)
        with self.assertRaises(RefreshRecoveryExpired):
            await self.service.prepare_refresh(token)
        marker_key = self.service._attempt_key(attempt.session_id)
        self.assertIsNone(await self.redis.get(marker_key))
        self.assertEqual(
            await self.redis.get(self.service._replay_key(attempt.token_digest)),
            attempt.session_id,
        )

    async def test_redis_failure_fails_closed(self):
        self.redis.fail_next_rotation = True
        # Redis failures on rate counter requests are represented by an injected limiter error.
        class BrokenLimiter:
            async def limit(self, identifier):
                raise OSError("private mock error")

        self.service._limiters["login_email"] = BrokenLimiter()
        with self.assertRaises(HTTPException) as raised:
            await self.service.check_credentials(make_request(), "safe@example.com", registration=False)
        self.assertEqual(raised.exception.status_code, 503)
        self.assertNotIn("private mock error", str(raised.exception.detail))

    async def test_redis_timeout_and_invalid_limiter_response_fail_closed(self):
        class SlowLimiter:
            async def limit(self, identifier):
                await asyncio.sleep(0.05)

        self.service._limiters["login_email"] = SlowLimiter()
        with patch("app.core.rate_limit.REDIS_TIMEOUT_SECONDS", 0.001):
            with self.assertRaises(HTTPException) as raised:
                await self.service.check_credentials(make_request(), "safe@example.com", registration=False)
        self.assertEqual(raised.exception.status_code, 503)

        class InvalidLimiter:
            async def limit(self, identifier):
                return SimpleNamespace(allowed="yes", reset=None)

        self.service._limiters["login_email"] = InvalidLimiter()
        with self.assertRaises(HTTPException) as raised:
            await self.service.check_credentials(make_request(), "safe@example.com", registration=False)
        self.assertEqual(raised.exception.status_code, 503)


if __name__ == "__main__":
    unittest.main()
