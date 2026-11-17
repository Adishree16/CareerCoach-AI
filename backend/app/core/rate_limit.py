"""Fail-closed Redis-backed limits and refresh-token rotation bookkeeping."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import ipaddress
import json
import math
import re
import secrets
import time
from dataclasses import dataclass
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from fastapi import HTTPException, Request

from app.core.config import settings


REDIS_TIMEOUT_SECONDS = 2.5
REFRESH_UPSTREAM_TIMEOUT_SECONDS = 3.0
# Declared waits on the normal path total up to 18.5s: acquisition (2.5s), two
# under-lock reads (5s), ambiguity marker (2.5s), two Supabase calls (6s), and
# mapping commit (2.5s). One same-envelope Redis retry adds up to 2.5s; it is
# attempted only when that timeout fits before this conservative lease deadline.
# Event-loop stalls and HTTPX context cleanup are not hard wall-clock guarantees.
# The 10s recovery deadline independently prevents a late refresh retry upstream.
REFRESH_LOCK_TTL_MS = 22_000
REFRESH_REUSE_INTERVAL_SECONDS = 10
SESSION_ABSOLUTE_LIFETIME_SECONDS = 30 * 24 * 60 * 60
# Session records and mappings expire at the fixed 30-day absolute lifetime.
# HMAC replay tombstones intentionally outlive them so an expired token can
# never be reclassified as a new legacy session. Short recovery aliases expire
# at the verified 10-second Supabase reuse deadline.


class RefreshRecoveryExpired(RuntimeError):
    """The old refresh token is outside the verified Supabase reuse interval."""


class RefreshLockBusy(RuntimeError):
    """Another refresh operation currently owns this limiter session."""


@dataclass(frozen=True)
class RefreshAttempt:
    session_id: str
    token_digest: str
    current_digest: str
    lock_owner: str
    started_at: float


@dataclass(frozen=True)
class RefreshRecovery:
    """A previously committed refresh response ready for safe replay."""

    session_id: str
    token_digest: str
    payload: dict[str, Any]


_ENSURE_SESSION_SCRIPT = """#!lua flags=allow-key-locking
-- CC_ENSURE_SESSION
local existing = redis.call('GET', KEYS[1])
if existing then
  local ok, old = pcall(cjson.decode, existing)
  if not ok or (old['state'] ~= 'current' and old['state'] ~= 'consumed') or old['session_id'] ~= ARGV[1] then return {-1, ''} end
  if old['expires_at'] and tonumber(old['expires_at']) <= tonumber(ARGV[4]) then return {-1, ''} end
  local state_raw = redis.call('GET', KEYS[5])
  if state_raw then
    local state_ok, state = pcall(cjson.decode, state_raw)
    if not state_ok or state['session_id'] ~= ARGV[1] or state['status'] ~= 'open' or tonumber(state['expires_at']) <= tonumber(ARGV[4]) then return {-1, ''} end
    if state['user_digest'] and ARGV[5] ~= '' and state['user_digest'] ~= ARGV[5] then return {-1, ''} end
    if old['state'] == 'consumed' then return {0, ARGV[1]} end
    return {0, ARGV[1]}
  end
  -- Atomic migration of a pre-Task-3H limiter association.
  if old['state'] ~= 'current' or redis.call('GET', KEYS[2]) ~= ARGV[2] then return {-1, ''} end
  old['expires_at'] = tonumber(ARGV[9])
  redis.call('SET', KEYS[1], cjson.encode(old), 'EX', ARGV[7])
  redis.call('EXPIRE', KEYS[2], ARGV[7])
  redis.call('EXPIRE', KEYS[3], ARGV[7])
  redis.call('SET', KEYS[4], ARGV[1])
  redis.call('SET', KEYS[5], ARGV[6], 'EX', ARGV[7])
  redis.call('SET', KEYS[6], ARGV[8], 'EX', ARGV[7])
  redis.call('SET', KEYS[4], ARGV[1])
  redis.call('ZADD', KEYS[7], ARGV[9], ARGV[10])
  return {0, ARGV[1]}
end
local replay = redis.call('GET', KEYS[4])
if replay then return {-1, replay} end
local record = cjson.encode({session_id=ARGV[1], state='current', recovery_until=0, expires_at=tonumber(ARGV[9])})
redis.call('SET', KEYS[1], record, 'EX', ARGV[7])
redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[7])
redis.call('SADD', KEYS[3], ARGV[3])
redis.call('EXPIRE', KEYS[3], ARGV[7])
redis.call('SET', KEYS[4], ARGV[1])
redis.call('SET', KEYS[5], ARGV[6], 'EX', ARGV[7])
redis.call('SET', KEYS[6], ARGV[8], 'EX', ARGV[7])
redis.call('SET', KEYS[4], ARGV[1])
redis.call('ZADD', KEYS[7], ARGV[9], ARGV[10])
return {1, ARGV[1]}
"""

_REWRAP_SESSION_TOKEN_SCRIPT = """#!lua flags=allow-key-locking
-- CC_REWRAP_SESSION_TOKEN
local state_raw = redis.call('GET', KEYS[1])
if not state_raw then return -1 end
local ok, state = pcall(cjson.decode, state_raw)
if not ok or state['session_id'] ~= ARGV[7] or state['status'] ~= 'open' or tonumber(state['expires_at']) <= tonumber(ARGV[4]) then return -1 end
if redis.call('GET', KEYS[2]) ~= ARGV[1] then return 0 end
redis.call('SET', KEYS[2], ARGV[2], 'EX', ARGV[3])
redis.call('ZREM', KEYS[3], ARGV[5])
redis.call('ZADD', KEYS[4], ARGV[6], ARGV[7])
return 1
"""

_BIND_SESSION_USER_SCRIPT = """#!lua flags=allow-key-locking
-- CC_BIND_SESSION_USER
local raw = redis.call('GET', KEYS[1])
if not raw then return -1 end
local ok, state = pcall(cjson.decode, raw)
if not ok or state['session_id'] ~= ARGV[4] or state['status'] ~= 'open' or tonumber(state['expires_at']) <= tonumber(ARGV[2]) then return -1 end
if state['user_digest'] and state['user_digest'] ~= ARGV[1] then return 0 end
state['user_digest'] = ARGV[1]
redis.call('SET', KEYS[1], cjson.encode(state), 'EX', ARGV[3])
return 1
"""

_CLOSE_SESSION_SCRIPT = """#!lua flags=allow-key-locking
-- CC_CLOSE_SESSION
local raw = redis.call('GET', KEYS[1])
local state
if raw then
  local ok
  ok, state = pcall(cjson.decode, raw)
  if not ok or state['session_id'] ~= ARGV[3] then return {-1, ''} end
else
  state = {version=1, session_id=ARGV[3], status='closed', expires_at=math.floor(tonumber(ARGV[1])) + tonumber(ARGV[7])}
end
local envelope = redis.call('GET', KEYS[2]) or ''
local envelope_ok, encrypted = pcall(cjson.decode, envelope)
if envelope ~= '' and (not envelope_ok or not encrypted['key_id']) then return {-1, ''} end
state['status'] = 'closed'
local remaining = math.max(1, math.floor(tonumber(state['expires_at']) - tonumber(ARGV[1])))
redis.call('SET', KEYS[1], cjson.encode(state), 'EX', remaining)
redis.call('DEL', KEYS[4], KEYS[5], KEYS[6])
if envelope_ok and encrypted['key_id'] then
  redis.call('ZREM', ARGV[6] .. encrypted['key_id'], ARGV[3])
end
local digests = redis.call('SMEMBERS', KEYS[3])
for _, digest in ipairs(digests) do
  redis.call('SET', ARGV[2] .. digest, ARGV[3])
  local recovery_key = ARGV[5] .. ARGV[3] .. ':' .. digest
  local recovery_raw = redis.call('GET', recovery_key)
  if recovery_raw then
    local recovery_ok, recovery = pcall(cjson.decode, recovery_raw)
    if recovery_ok and recovery['key_id'] then
      redis.call('ZREM', ARGV[6] .. recovery['key_id'], ARGV[3] .. ':recovery:' .. digest)
    end
    redis.call('DEL', recovery_key)
  end
end
redis.call('DEL', KEYS[2], KEYS[3])
return {1, envelope}
"""

_ACQUIRE_REFRESH_SCRIPT = """#!lua flags=allow-key-locking
-- CC_ACQUIRE_REFRESH
if redis.call('EXISTS', KEYS[1]) == 1 then return {0, ''} end
local started = ARGV[3]
local phase = 'prepared'
local prior = redis.call('GET', KEYS[2])
if prior then
  local ok, decoded = pcall(cjson.decode, prior)
  if ok and decoded['token_digest'] == ARGV[4] and tonumber(decoded['started_at']) then
    if decoded['phase'] ~= 'prepared' then
      started = tostring(decoded['started_at'])
      phase = 'ambiguous'
    end
  end
end
redis.call('SET', KEYS[1], ARGV[1], 'PX', ARGV[2])
local attempt = cjson.encode({token_digest=ARGV[4], started_at=tonumber(started), phase=phase, lock_owner=ARGV[1]})
if phase == 'prepared' then
  redis.call('SET', KEYS[2], attempt, 'PX', ARGV[5])
else
  redis.call('SET', KEYS[2], attempt)
end
return {1, started}
"""

_MARK_REFRESH_AMBIGUOUS_SCRIPT = """#!lua flags=allow-key-locking
-- CC_MARK_REFRESH_AMBIGUOUS
if redis.call('GET', KEYS[1]) ~= ARGV[1] then return 0 end
local raw = redis.call('GET', KEYS[2])
if not raw then return 0 end
local ok, attempt = pcall(cjson.decode, raw)
if not ok or attempt['token_digest'] ~= ARGV[2] then return 0 end
attempt['phase'] = 'ambiguous'
redis.call('SET', KEYS[2], cjson.encode(attempt))
return 1
"""

_EXPIRE_REFRESH_ATTEMPT_SCRIPT = """#!lua flags=allow-key-locking
-- CC_EXPIRE_REFRESH_ATTEMPT
local raw = redis.call('GET', KEYS[2])
if not raw then return 2 end
local ok, attempt = pcall(cjson.decode, raw)
if not ok or attempt['token_digest'] ~= ARGV[2] then return -1 end
local started = tonumber(attempt['started_at'])
if not started then return -1 end
local phase = attempt['phase'] or 'ambiguous'
if phase ~= 'ambiguous' or started + tonumber(ARGV[3]) > tonumber(ARGV[4]) then return 2 end
local lock_owner = redis.call('GET', KEYS[1])
if lock_owner then
  if not attempt['lock_owner'] or lock_owner ~= attempt['lock_owner'] then return 0 end
  redis.call('DEL', KEYS[1])
end
local mapped = redis.call('GET', KEYS[3])
if mapped then
  local ok_mapping, mapping = pcall(cjson.decode, mapped)
  if not ok_mapping or mapping['session_id'] ~= ARGV[1] then return -1 end
end
redis.call('DEL', KEYS[3])
redis.call('SET', KEYS[4], ARGV[1])
redis.call('DEL', KEYS[2])
return 1
"""

_ROTATE_REFRESH_SCRIPT = """#!lua flags=allow-key-locking
-- CC_ROTATE_REFRESH
local session_raw = redis.call('GET', KEYS[12])
local session_ok, session = pcall(cjson.decode, session_raw or '')
if not session_ok or session['session_id'] ~= ARGV[3] or session['status'] ~= 'open' or tonumber(session['expires_at']) <= tonumber(ARGV[6]) then return -2 end
if session['user_digest'] and ARGV[12] ~= '' and session['user_digest'] ~= ARGV[12] then return -2 end
local prior_session_token = redis.call('GET', KEYS[13])
local prior_key_id = nil
if prior_session_token then
  local prior_ok, prior_envelope = pcall(cjson.decode, prior_session_token)
  if not prior_ok or not prior_envelope['key_id'] then return -2 end
  prior_key_id = prior_envelope['key_id']
end
local existing_recovery = redis.call('GET', KEYS[11])
if existing_recovery then
  if existing_recovery ~= ARGV[9] then return -1 end
  local pointer_now = redis.call('GET', KEYS[3])
  local old_now = redis.call('GET', KEYS[1])
  local new_now = redis.call('GET', KEYS[2])
  local ok_old_now, old_value_now = pcall(cjson.decode, old_now or '')
  local ok_new_now, new_value_now = pcall(cjson.decode, new_now or '')
  if pointer_now == ARGV[5] and ok_old_now and ok_new_now
    and old_value_now['session_id'] == ARGV[3]
    and new_value_now['session_id'] == ARGV[3]
    and new_value_now['state'] == 'current' then return 2 end
  return -1
end
if redis.call('GET', KEYS[5]) ~= ARGV[1] then return 0 end
local pointer = redis.call('GET', KEYS[3])
if not pointer or pointer ~= ARGV[2] then return 0 end
local old_raw = redis.call('GET', KEYS[1])
if not old_raw then return 0 end
local ok_old, old = pcall(cjson.decode, old_raw)
if not ok_old or old['session_id'] ~= ARGV[3] then return 0 end
local new_raw = redis.call('GET', KEYS[2])
if new_raw then
  local ok_new, new_value = pcall(cjson.decode, new_raw)
  if not ok_new or new_value['session_id'] ~= ARGV[3] then return 0 end
end
local current_raw = redis.call('GET', KEYS[4])
local ok_current, current_value = pcall(cjson.decode, current_raw or '')
if not ok_current or current_value['session_id'] ~= ARGV[3] then return 0 end
local deadline = tonumber(ARGV[4])
if ARGV[2] ~= ARGV[5] then
  current_value['state'] = 'consumed'
  current_value['recovery_until'] = deadline
  if deadline > tonumber(ARGV[6]) then
    redis.call('SET', KEYS[4], cjson.encode(current_value), 'PX', ARGV[7])
  else
    redis.call('DEL', KEYS[4])
  end
  redis.call('SET', KEYS[9], ARGV[3])
end
local remaining = math.max(1, math.floor(tonumber(session['expires_at']) - tonumber(ARGV[6])))
redis.call('SET', KEYS[2], ARGV[8], 'EX', remaining)
redis.call('SET', KEYS[3], ARGV[5], 'EX', remaining)
redis.call('SADD', KEYS[7], ARGV[5])
redis.call('EXPIRE', KEYS[7], remaining)
redis.call('SET', KEYS[11], ARGV[9], 'PX', ARGV[10])
redis.call('SET', KEYS[10], ARGV[3])
if prior_key_id then redis.call('ZREM', ARGV[14] .. prior_key_id, ARGV[3]) end
redis.call('SET', KEYS[13], ARGV[11], 'EX', remaining)
if not session['user_digest'] and ARGV[12] ~= '' then session['user_digest'] = ARGV[12] end
redis.call('SET', KEYS[12], cjson.encode(session), 'EX', remaining)
redis.call('ZADD', KEYS[14], session['expires_at'], ARGV[13])
redis.call('ZADD', KEYS[14], tonumber(ARGV[4]), ARGV[3] .. ':recovery:' .. ARGV[2])
redis.call('DEL', KEYS[6])
redis.call('DEL', KEYS[5])
return 1
"""

_RELEASE_REFRESH_SCRIPT = """#!lua flags=allow-key-locking
-- CC_RELEASE_REFRESH
local owns_lock = redis.call('GET', KEYS[1]) == ARGV[1]
if owns_lock then redis.call('DEL', KEYS[1]) end
if owns_lock and ARGV[3] == '1' then
  local raw = redis.call('GET', KEYS[2])
  if raw then
    local ok, attempt = pcall(cjson.decode, raw)
    if ok and attempt['token_digest'] == ARGV[2] then redis.call('DEL', KEYS[2]) end
  end
end
return 1
"""


class RateLimitService:
    """Async limits and opaque session mapping backed by shared Upstash Redis."""

    def __init__(
        self,
        *,
        redis_client: Any | None = None,
        hmac_secret: str | bytes | None = None,
        recovery_encryption_key: str | bytes | None = None,
        recovery_encryption_key_id: str | None = None,
        previous_recovery_encryption_key: str | bytes | None = None,
        previous_recovery_encryption_key_id: str | None = None,
        limiter_class: Any | None = None,
        sliding_window_class: Any | None = None,
    ) -> None:
        self._redis = redis_client
        self._secret_input = hmac_secret
        self._recovery_key_input = recovery_encryption_key
        self._recovery_key_id_input = recovery_encryption_key_id
        self._previous_recovery_key_input = previous_recovery_encryption_key
        self._previous_recovery_key_id_input = previous_recovery_encryption_key_id
        self._limiter_class = limiter_class
        self._sliding_window_class = sliding_window_class
        self._secret: bytes | None = None
        self._recovery_keys: dict[str, bytes] = {}
        self._active_recovery_key_id: str | None = None
        self._recovery_unavailable = False
        self._limiters: dict[str, Any] = {}
        self._initialized = False
        self._unavailable = False

    async def startup(self) -> None:
        """Initialize SDK clients without making network requests or logging secrets."""
        if self._initialized:
            return
        self._initialized = True
        self._unavailable = False
        self._load_recovery_keys()
        try:
            secret_value = self._secret_input
            if secret_value is None:
                secret_setting = settings.rate_limit_hmac_secret
                secret_value = (
                    secret_setting.get_secret_value() if secret_setting is not None else None
                )
            if isinstance(secret_value, str):
                secret = secret_value.encode("utf-8")
            elif isinstance(secret_value, bytes):
                secret = secret_value
            else:
                self._unavailable = True
                return
            if len(secret) < 32:
                self._unavailable = True
                return
            self._secret = secret

            if self._redis is None:
                url_setting = settings.upstash_redis_rest_url
                token_setting = settings.upstash_redis_rest_token
                if url_setting is None or token_setting is None:
                    self._unavailable = True
                    return
                from upstash_redis.asyncio import Redis

                self._redis = Redis(
                    url=url_setting.get_secret_value(),
                    token=token_setting.get_secret_value(),
                    rest_retries=0,
                    allow_telemetry=False,
                )

            if self._limiter_class is None or self._sliding_window_class is None:
                from upstash_ratelimit.asyncio import Ratelimit, SlidingWindow

                self._limiter_class = self._limiter_class or Ratelimit
                self._sliding_window_class = self._sliding_window_class or SlidingWindow

            specifications = {
                "login_email": (5, 15 * 60),
                "login_ip": (30, 15 * 60),
                "register_email": (3, 60 * 60),
                "register_ip": (5, 60 * 60),
                "refresh_ip": (120, 60 * 60),
                "refresh_session_5m": (10, 5 * 60),
                "refresh_session_1h": (60, 60 * 60),
            }
            for name, (maximum, window) in specifications.items():
                limiter = self._sliding_window_class(max_requests=maximum, window=window, unit="s")
                self._limiters[name] = self._limiter_class(
                    redis=self._redis,
                    limiter=limiter,
                    prefix=f"careercoach:auth:rate:v1:{name}",
                )
        except Exception:
            # A missing SDK/config or malformed initialization is closed at request time.
            self._unavailable = True

    @staticmethod
    def _decode_recovery_key(value: str | bytes | None) -> bytes:
        if isinstance(value, bytes):
            key = value
        elif isinstance(value, str):
            try:
                key = base64.b64decode(value, validate=True)
            except (ValueError, base64.binascii.Error):
                raise ValueError("Invalid refresh recovery key configuration.") from None
        else:
            raise ValueError("Missing refresh recovery key configuration.")
        if len(key) != 32:
            raise ValueError("Invalid refresh recovery key configuration.")
        return key

    @staticmethod
    def _validate_recovery_key_id(key_id: str | None) -> str:
        if not isinstance(key_id, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,32}", key_id) is None:
            raise ValueError("Invalid refresh recovery key identifier.")
        return key_id

    def _load_recovery_keys(self) -> None:
        active_value = self._recovery_key_input
        active_id = self._recovery_key_id_input
        previous_value = self._previous_recovery_key_input
        previous_id = self._previous_recovery_key_id_input
        if active_value is None:
            active_setting = settings.refresh_recovery_encryption_key
            active_value = active_setting.get_secret_value() if active_setting is not None else None
        if active_id is None:
            active_id = settings.refresh_recovery_encryption_key_id
        if previous_value is None:
            previous_setting = settings.refresh_recovery_previous_encryption_key
            previous_value = previous_setting.get_secret_value() if previous_setting is not None else None
        if previous_id is None:
            previous_id = settings.refresh_recovery_previous_encryption_key_id

        self._recovery_keys.clear()
        self._active_recovery_key_id = None
        self._recovery_unavailable = False
        try:
            active_id = self._validate_recovery_key_id(active_id)
            self._recovery_keys[active_id] = self._decode_recovery_key(active_value)
            self._active_recovery_key_id = active_id
            if previous_value is not None or previous_id is not None:
                previous_id = self._validate_recovery_key_id(previous_id)
                if previous_id == active_id:
                    raise ValueError("Refresh recovery key identifiers must be unique.")
                self._recovery_keys[previous_id] = self._decode_recovery_key(previous_value)
        except (TypeError, ValueError):
            self._recovery_keys.clear()
            self._active_recovery_key_id = None
            self._recovery_unavailable = True

    def ensure_refresh_recovery_ready(self) -> None:
        """Fail before contacting Supabase if recovery encryption is unavailable."""
        self._ensure_ready()
        if self._recovery_unavailable or self._active_recovery_key_id is None:
            raise HTTPException(
                status_code=503,
                detail="Authentication service temporarily unavailable.",
            )

    async def shutdown(self) -> None:
        redis, self._redis = self._redis, None
        self._limiters.clear()
        self._initialized = False
        self._secret = None
        self._recovery_keys.clear()
        self._active_recovery_key_id = None
        if redis is not None:
            close = getattr(redis, "close", None)
            if close is not None:
                try:
                    await asyncio.wait_for(close(), timeout=REDIS_TIMEOUT_SECONDS)
                except Exception:
                    pass

    def _ensure_ready(self) -> None:
        if self._unavailable or self._redis is None or self._secret is None:
            raise HTTPException(
                status_code=503,
                detail="Authentication service temporarily unavailable.",
            )

    async def _bounded(self, awaitable: Any) -> Any:
        try:
            return await asyncio.wait_for(awaitable, timeout=REDIS_TIMEOUT_SECONDS)
        except Exception:
            raise HTTPException(
                status_code=503,
                detail="Authentication service temporarily unavailable.",
            ) from None

    def _digest(self, namespace: str, value: str) -> str:
        self._ensure_ready()
        assert self._secret is not None
        message = f"careercoach:{namespace}:v1:{value}".encode("utf-8")
        return hmac.new(self._secret, message, hashlib.sha256).hexdigest()

    @staticmethod
    def _client_ip(request: Request) -> str:
        client = request.client
        if client is None:
            raise HTTPException(
                status_code=503,
                detail="Authentication service temporarily unavailable.",
            )
        try:
            # Deliberately ignore forwarded headers; this is the socket peer only.
            return ipaddress.ip_address(client.host).compressed
        except (ValueError, TypeError):
            raise HTTPException(
                status_code=503,
                detail="Authentication service temporarily unavailable.",
            ) from None

    @staticmethod
    def _reject(retry_after: int) -> None:
        raise HTTPException(
            status_code=429,
            detail="Too many attempts. Please try again later.",
            headers={"Retry-After": str(max(1, retry_after))},
        )

    async def _consume(self, limiter_name: str, identifier: str) -> None:
        self._ensure_ready()
        limiter = self._limiters.get(limiter_name)
        if limiter is None:
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
        try:
            result = await self._bounded(limiter.limit(identifier))
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.") from None
        allowed = getattr(result, "allowed", None)
        reset = getattr(result, "reset", None)
        if not isinstance(allowed, bool) or not isinstance(reset, (int, float)) or isinstance(reset, bool) or not math.isfinite(reset):
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
        if not allowed:
            self._reject(math.ceil(reset - time.time()))

    async def check_credentials(self, request: Request, email: str, *, registration: bool) -> None:
        normalized_email = email.strip().casefold()
        if not normalized_email:
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
        ip = self._client_ip(request)
        flow = "register" if registration else "login"
        await self._consume(f"{flow}_email", self._digest("email", normalized_email))
        await self._consume(f"{flow}_ip", self._digest("ip", ip))

    async def check_refresh_ip(self, request: Request) -> None:
        ip = self._client_ip(request)
        await self._consume("refresh_ip", self._digest("ip", ip))

    async def check_refresh_session(self, session_id: str) -> None:
        await self._consume("refresh_session_5m", session_id)
        await self._consume("refresh_session_1h", session_id)

    async def _eval(self, script: str, keys: list[str], args: list[str]) -> Any:
        self._ensure_ready()
        command = ["EVAL", script, str(len(keys)), *keys, *args]
        return await self._bounded(self._redis.execute(command=command))

    @staticmethod
    def _mapping_key(token_digest: str) -> str:
        return f"careercoach:auth:session-token:v1:{token_digest}"

    @staticmethod
    def _pointer_key(session_id: str) -> str:
        return f"careercoach:auth:session-current:v1:{session_id}"

    @staticmethod
    def _lock_key(session_id: str) -> str:
        return f"careercoach:auth:refresh-lock:v1:{session_id}"

    @staticmethod
    def _attempt_key(session_id: str) -> str:
        return f"careercoach:auth:refresh-attempt:v1:{session_id}"

    @staticmethod
    def _index_key(session_id: str) -> str:
        return f"careercoach:auth:session-token-index:v1:{session_id}"

    @staticmethod
    def _session_state_key(session_id: str) -> str:
        return f"careercoach:auth:session-state:v1:{session_id}"

    @staticmethod
    def _session_refresh_key(session_id: str) -> str:
        return f"careercoach:auth:session-refresh:v1:{session_id}"

    @staticmethod
    def _key_session_index(key_id: str) -> str:
        return f"careercoach:auth:encrypted-sessions-by-key:v1:{key_id}"

    @staticmethod
    def _replay_key(token_digest: str) -> str:
        return f"careercoach:auth:refresh-replay:v1:{token_digest}"

    @staticmethod
    def _recovery_key(session_id: str, token_digest: str) -> str:
        return f"careercoach:auth:refresh-recovery:v1:{session_id}:{token_digest}"

    @staticmethod
    def _recovery_aad(
        key_id: str,
        session_id: str,
        token_digest: str,
        recovery_deadline: float,
    ) -> bytes:
        return json.dumps(
            ["careercoach-refresh-recovery", 1, key_id, session_id, token_digest, recovery_deadline],
            separators=(",", ":"),
        ).encode("utf-8")

    @staticmethod
    def _session_refresh_aad(key_id: str, session_id: str, expires_at: float) -> bytes:
        return json.dumps(
            # Session expiry is stored as integer epoch seconds. Canonicalize
            # here so encryption with an int and decryption with a float produce
            # identical AAD bytes; existing envelopes were encrypted as ints.
            ["careercoach-session-refresh", 1, key_id, session_id, int(expires_at)],
            separators=(",", ":"),
        ).encode("utf-8")

    def _encrypt_session_refresh(self, session_id: str, refresh_token: str, expires_at: float) -> str:
        self.ensure_refresh_recovery_ready()
        key_id = self._active_recovery_key_id
        assert key_id is not None
        nonce = secrets.token_bytes(12)
        ciphertext = AESGCM(self._recovery_keys[key_id]).encrypt(
            nonce,
            refresh_token.encode("utf-8"),
            self._session_refresh_aad(key_id, session_id, expires_at),
        )
        return json.dumps(
            {
                "version": 1,
                "key_id": key_id,
                "expires_at": expires_at,
                "nonce": base64.urlsafe_b64encode(nonce).decode("ascii").rstrip("="),
                "ciphertext": base64.urlsafe_b64encode(ciphertext).decode("ascii").rstrip("="),
            },
            separators=(",", ":"),
        )

    def _decrypt_session_refresh(self, raw: Any, session_id: str, expires_at: float) -> tuple[str, str]:
        self.ensure_refresh_recovery_ready()
        try:
            if not isinstance(raw, str) or len(raw) > 32768:
                raise ValueError
            envelope = json.loads(raw)
            if (
                not isinstance(envelope, dict)
                or set(envelope) != {"version", "key_id", "expires_at", "nonce", "ciphertext"}
                or envelope.get("version") != 1
                or not isinstance(envelope.get("key_id"), str)
                or envelope["key_id"] not in self._recovery_keys
                or envelope.get("expires_at") != expires_at
            ):
                raise ValueError
            nonce = self._decode_urlsafe(envelope["nonce"])
            ciphertext = self._decode_urlsafe(envelope["ciphertext"])
            if len(nonce) != 12 or len(ciphertext) < 17:
                raise ValueError
            token = AESGCM(self._recovery_keys[envelope["key_id"]]).decrypt(
                nonce, ciphertext,
                self._session_refresh_aad(envelope["key_id"], session_id, expires_at),
            ).decode("utf-8")
            if not token or len(token) > 8192:
                raise ValueError
            return token, envelope["key_id"]
        except Exception:
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.") from None

    @staticmethod
    def _decode_urlsafe(value: Any) -> bytes:
        if not isinstance(value, str) or not value or len(value) > 32768:
            raise ValueError("Malformed recovery envelope.")
        padded = value + "=" * ((4 - len(value) % 4) % 4)
        return base64.b64decode(padded, altchars=b"-_", validate=True)

    def encrypt_refresh_response(
        self, attempt: RefreshAttempt, payload: dict[str, Any]
    ) -> str:
        self.ensure_refresh_recovery_ready()
        deadline = attempt.started_at + REFRESH_REUSE_INTERVAL_SECONDS
        if time.time() >= deadline:
            raise HTTPException(
                status_code=503,
                detail="Authentication service temporarily unavailable.",
            )
        key_id = self._active_recovery_key_id
        assert key_id is not None
        recovery_payload = dict(payload)
        recovery_payload.update(
            {
                "version": 1,
                "session_id": attempt.session_id,
                "old_token_digest": attempt.token_digest,
                "new_token_digest": self._digest(
                    "refresh-token", recovery_payload.get("refresh_token", "")
                ),
                "recovery_deadline": deadline,
            }
        )
        if (
            not isinstance(recovery_payload.get("access_token"), str)
            or not recovery_payload["access_token"]
            or len(recovery_payload["access_token"]) > 8192
            or not isinstance(recovery_payload.get("refresh_token"), str)
            or not recovery_payload["refresh_token"]
            or len(recovery_payload["refresh_token"]) > 8192
            or not isinstance(recovery_payload.get("csrf_token"), str)
            or not 32 <= len(recovery_payload["csrf_token"] ) <= 128
            or not isinstance(recovery_payload.get("access_expires_at"), (int, float))
            or isinstance(recovery_payload.get("access_expires_at"), bool)
            or not math.isfinite(recovery_payload["access_expires_at"])
            or recovery_payload["access_expires_at"] <= time.time()
        ):
            raise HTTPException(
                status_code=503,
                detail="Authentication service temporarily unavailable.",
            )
        try:
            plaintext = json.dumps(
                recovery_payload, separators=(",", ":"), ensure_ascii=False
            ).encode("utf-8")
            nonce = secrets.token_bytes(12)
            ciphertext = AESGCM(self._recovery_keys[key_id]).encrypt(
                nonce,
                plaintext,
                self._recovery_aad(key_id, attempt.session_id, attempt.token_digest, deadline),
            )
            envelope = {
                "version": 1,
                "key_id": key_id,
                "recovery_deadline": deadline,
                "nonce": base64.urlsafe_b64encode(nonce).decode("ascii").rstrip("="),
                "ciphertext": base64.urlsafe_b64encode(ciphertext).decode("ascii").rstrip("="),
            }
            return json.dumps(envelope, separators=(",", ":"))
        except HTTPException:
            raise
        except Exception:
            raise HTTPException(
                status_code=503,
                detail="Authentication service temporarily unavailable.",
            ) from None

    def decrypt_refresh_response(
        self,
        envelope_raw: Any,
        *,
        session_id: str,
        token_digest: str,
    ) -> dict[str, Any]:
        self.ensure_refresh_recovery_ready()
        try:
            if not isinstance(envelope_raw, str) or len(envelope_raw) > 32768:
                raise ValueError("Malformed recovery envelope.")
            envelope = json.loads(envelope_raw)
            if (
                not isinstance(envelope, dict)
                or set(envelope) != {"version", "key_id", "recovery_deadline", "nonce", "ciphertext"}
                or envelope.get("version") != 1
                or not isinstance(envelope.get("key_id"), str)
                or envelope["key_id"] not in self._recovery_keys
                or not isinstance(envelope.get("recovery_deadline"), (int, float))
                or isinstance(envelope.get("recovery_deadline"), bool)
            ):
                raise ValueError("Malformed recovery envelope.")
            deadline = float(envelope["recovery_deadline"])
            if not math.isfinite(deadline) or time.time() >= deadline:
                raise ValueError("Expired recovery envelope.")
            nonce = self._decode_urlsafe(envelope.get("nonce"))
            ciphertext = self._decode_urlsafe(envelope.get("ciphertext"))
            if len(nonce) != 12 or len(ciphertext) < 16:
                raise ValueError("Malformed recovery envelope.")
            plaintext = AESGCM(self._recovery_keys[envelope["key_id"]]).decrypt(
                nonce,
                ciphertext,
                self._recovery_aad(envelope["key_id"], session_id, token_digest, deadline),
            )
            payload = json.loads(plaintext)
            if (
                not isinstance(payload, dict)
                or payload.get("version") != 1
                or payload.get("session_id") != session_id
                or payload.get("old_token_digest") != token_digest
                or payload.get("new_token_digest")
                != self._digest("refresh-token", payload.get("refresh_token", ""))
                or payload.get("recovery_deadline") != deadline
                or not isinstance(payload.get("access_token"), str)
                or not payload["access_token"]
                or not isinstance(payload.get("refresh_token"), str)
                or not payload["refresh_token"]
                or not isinstance(payload.get("csrf_token"), str)
                or not 32 <= len(payload["csrf_token"]) <= 128
                or not isinstance(payload.get("access_expires_at"), (int, float))
                or isinstance(payload.get("access_expires_at"), bool)
                or not math.isfinite(payload["access_expires_at"])
                or payload["access_expires_at"] <= time.time()
            ):
                raise ValueError("Invalid recovery payload.")
            return payload
        except Exception:
            raise HTTPException(
                status_code=503,
                detail="Authentication service temporarily unavailable.",
            ) from None

    @staticmethod
    def _decode_mapping(raw: Any) -> dict[str, Any]:
        if not isinstance(raw, str):
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
        try:
            value = json.loads(raw)
        except (TypeError, ValueError):
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.") from None
        if (
            not isinstance(value, dict)
            or not isinstance(value.get("session_id"), str)
            or value.get("state") not in {"current", "consumed"}
        ):
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
        return value

    async def establish_session(self, refresh_token: str, user_id: str | None = None) -> str:
        """Create or safely migrate an opaque session with encrypted refresh state."""
        if not isinstance(refresh_token, str) or not refresh_token or len(refresh_token) > 8192:
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
        if user_id is not None and (not isinstance(user_id, str) or not user_id or len(user_id) > 256):
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
        user_digest = self._digest("verified-user", user_id) if user_id else None
        digest = self._digest("refresh-token", refresh_token)
        existing = await self._get_mapping(digest)
        session_id = existing["session_id"] if existing is not None else secrets.token_urlsafe(32)
        now = time.time()
        state_raw = await self._bounded(self._redis.get(self._session_state_key(session_id))) if existing else None
        if existing is not None and state_raw is None and existing.get("state") == "consumed":
            recovery_raw = await self._bounded(
                self._redis.get(self._recovery_key(session_id, digest))
            )
            if recovery_raw is None:
                raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
            payload = self.decrypt_refresh_response(
                recovery_raw, session_id=session_id, token_digest=digest
            )
            current_digest = self._digest("refresh-token", payload["refresh_token"])
            if await self._bounded(self._redis.get(self._pointer_key(session_id))) != current_digest:
                raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
            # The legacy alias is recoverable only during the existing verified
            # reuse window. Initialize server-side state from the committed token.
            return await self.establish_session(payload["refresh_token"], user_id)
        if state_raw is not None:
            state = self._decode_session_state(state_raw, session_id)
            if state["status"] != "open" or state["expires_at"] <= now:
                raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
            if state.get("user_digest") and user_digest and not hmac.compare_digest(state["user_digest"], user_digest):
                raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
            # Existing records are authoritative. A consumed old alias is kept
            # only for the existing short recovery replay; never replace it.
            if existing.get("state") == "current":
                current = await self.get_session_refresh_token(session_id)
                if not hmac.compare_digest(self._digest("refresh-token", current[0]), digest):
                    raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
            elif existing.get("state") != "consumed":
                raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
            if user_digest:
                await self.bind_session_user(session_id, user_id)
            return session_id

        if existing is not None and "expires_at" in existing:
            expires_at = existing["expires_at"]
            if not isinstance(expires_at, (int, float)):
                raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
            expires_at = math.floor(expires_at)
        else:
            expires_at = math.floor(now + SESSION_ABSOLUTE_LIFETIME_SECONDS)
        if isinstance(expires_at, bool) or not math.isfinite(expires_at) or expires_at <= now:
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
        ttl = max(1, math.ceil(expires_at - now))
        state_data = {"version": 1, "session_id": session_id, "status": "open", "created_at": now,
                      "expires_at": expires_at}
        if user_digest:
            state_data["user_digest"] = user_digest
        state_value = json.dumps(
            state_data,
            separators=(",", ":"),
        )
        encrypted_refresh = self._encrypt_session_refresh(session_id, refresh_token, expires_at)
        result = await self._eval(
            _ENSURE_SESSION_SCRIPT,
            [
                self._mapping_key(digest),
                self._pointer_key(session_id),
                self._index_key(session_id),
                self._replay_key(digest),
                self._session_state_key(session_id),
                self._session_refresh_key(session_id),
                self._key_session_index(self._active_recovery_key_id or ""),
            ],
            [session_id, digest, digest, repr(now), user_digest or "", state_value,
             str(ttl), encrypted_refresh, repr(expires_at), session_id],
        )
        if not isinstance(result, (list, tuple)) or len(result) != 2:
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
        if str(result[0]) in {"-1", "-1.0"}:
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
        resolved = str(result[1])
        if resolved != session_id:
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
        return resolved

    @staticmethod
    def _decode_session_state(raw: Any, session_id: str) -> dict[str, Any]:
        try:
            state = json.loads(raw) if isinstance(raw, str) else None
        except (TypeError, ValueError):
            state = None
        if (
            not isinstance(state, dict) or state.get("version") != 1
            or state.get("session_id") != session_id
            or state.get("status") not in {"open", "closed"}
            or not isinstance(state.get("expires_at"), (int, float))
            or isinstance(state.get("expires_at"), bool)
            or not math.isfinite(state["expires_at"])
        ):
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
        return state

    async def get_session_refresh_token(self, session_id: str) -> tuple[str, float, int]:
        """Return the current refresh credential only for an open, unexpired SID."""
        if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", session_id):
            raise HTTPException(status_code=401, detail="Authentication required.")
        state_raw = await self._bounded(self._redis.get(self._session_state_key(session_id)))
        if state_raw is None:
            raise HTTPException(status_code=401, detail="Authentication required.")
        state = self._decode_session_state(state_raw, session_id)
        now = time.time()
        if state["status"] != "open" or state["expires_at"] <= now:
            raise HTTPException(status_code=401, detail="Authentication required.")
        expires_at = float(state["expires_at"])
        refresh_raw = await self._bounded(self._redis.get(self._session_refresh_key(session_id)))
        refresh_token, key_id = self._decrypt_session_refresh(refresh_raw, session_id, expires_at)
        digest = self._digest("refresh-token", refresh_token)
        mapping = await self._get_mapping(digest)
        pointer = await self._bounded(self._redis.get(self._pointer_key(session_id)))
        if mapping is None or mapping.get("session_id") != session_id or mapping.get("state") != "current" or pointer != digest:
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
        if key_id != self._active_recovery_key_id:
            encrypted = self._encrypt_session_refresh(session_id, refresh_token, expires_at)
            ttl = max(1, math.ceil(expires_at - now))
            result = await self._eval(
                _REWRAP_SESSION_TOKEN_SCRIPT,
                [self._session_state_key(session_id), self._session_refresh_key(session_id),
                 self._key_session_index(key_id), self._key_session_index(self._active_recovery_key_id or "")],
                [refresh_raw, encrypted, str(ttl), repr(now), key_id,
                 repr(expires_at), session_id],
            )
            if str(result) not in {"1", "True", "true"}:
                raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
        return refresh_token, expires_at, max(1, math.ceil(expires_at - now))

    async def bind_session_user(self, session_id: str, user_id: str) -> None:
        state_raw = await self._bounded(self._redis.get(self._session_state_key(session_id)))
        if state_raw is None:
            raise HTTPException(status_code=401, detail="Authentication required.")
        state = self._decode_session_state(state_raw, session_id)
        user_digest = self._digest("verified-user", user_id)
        result = await self._eval(
            _BIND_SESSION_USER_SCRIPT, [self._session_state_key(session_id)],
            [user_digest, repr(time.time()), str(max(1, math.ceil(state["expires_at"] - time.time()))), session_id],
        )
        if str(result) not in {"1", "True", "true"}:
            raise HTTPException(status_code=401, detail="Authentication required.")

    async def close_session(self, session_id: str) -> str | None:
        if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", session_id):
            return None
        result = await self._eval(
            _CLOSE_SESSION_SCRIPT,
            [self._session_state_key(session_id), self._session_refresh_key(session_id),
             self._index_key(session_id), self._pointer_key(session_id),
             self._attempt_key(session_id), self._lock_key(session_id)],
            [repr(time.time()), "careercoach:auth:refresh-replay:v1:", session_id, "", "",
              "careercoach:auth:refresh-recovery:v1:",
              "careercoach:auth:encrypted-sessions-by-key:v1:",
             str(SESSION_ABSOLUTE_LIFETIME_SECONDS)],
        )
        if not isinstance(result, (list, tuple)) or len(result) != 2:
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
        if str(result[0]) in {"-1", "-1.0"}:
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
        envelope = result[1]
        if not envelope:
            return None
        state_raw = await self._bounded(self._redis.get(self._session_state_key(session_id)))
        state = self._decode_session_state(state_raw, session_id)
        refresh_token, _key_id = self._decrypt_session_refresh(envelope, session_id, float(state["expires_at"]))
        return refresh_token

    async def previous_key_retirement_ready(self, previous_key_id: str) -> bool:
        """True only when no unexpired encrypted session still needs this key.

        Idle records remain indexed until used and rewrapped or until their fixed
        absolute expiry. Retire the previous key only when this returns True.
        A one-time 10-second fence also covers Task 3E recovery envelopes that
        predate the persistent-session key index and may still be in flight.
        All application instances must already be using a different active key;
        otherwise an older instance could create new records under this key.
        """
        self._ensure_ready()
        try:
            previous_key_id = self._validate_recovery_key_id(previous_key_id)
        except ValueError:
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.") from None
        if previous_key_id not in self._recovery_keys or previous_key_id == self._active_recovery_key_id:
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
        index = self._key_session_index(previous_key_id)
        fence_score = time.time() + REFRESH_REUSE_INTERVAL_SECONDS
        await self._bounded(
            self._redis.execute(
                command=["ZADD", index, "NX", str(fence_score), "recovery-window-fence"]
            )
        )
        await self._bounded(
            self._redis.execute(
                command=["ZREMRANGEBYSCORE", index, "-inf", str(time.time())]
            )
        )
        count = await self._bounded(self._redis.execute(command=["ZCARD", index]))
        if isinstance(count, bool) or not isinstance(count, int):
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
        return count == 0

    async def _get_mapping(self, digest: str) -> dict[str, Any] | None:
        self._ensure_ready()
        raw = await self._bounded(self._redis.get(self._mapping_key(digest)))
        return None if raw is None else self._decode_mapping(raw)

    async def prepare_refresh(self, refresh_token: str) -> RefreshAttempt | RefreshRecovery:
        """Find the session, replay a committed result, or lock a fresh attempt."""
        digest = self._digest("refresh-token", refresh_token)
        mapping = await self._get_mapping(digest)
        if mapping is None:
            replay_marker = await self._bounded(self._redis.get(self._replay_key(digest)))
            if replay_marker is not None:
                raise RefreshRecoveryExpired()
            session_id = await self.establish_session(refresh_token)
            mapping = await self._get_mapping(digest)
            if mapping is None:
                raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
        else:
            session_id = mapping["session_id"]

        state_raw = await self._bounded(self._redis.get(self._session_state_key(session_id)))
        state = self._decode_session_state(state_raw, session_id)
        if state["status"] != "open" or state["expires_at"] <= time.time():
            raise RefreshRecoveryExpired()

        now = time.time()
        recovery_until = mapping.get("recovery_until", 0)
        if mapping["state"] == "consumed":
            if not isinstance(recovery_until, (int, float)) or isinstance(recovery_until, bool):
                raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
            if now >= recovery_until:
                raise RefreshRecoveryExpired()
        elif mapping["state"] != "current":
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")

        await self.check_refresh_session(session_id)
        envelope = await self._bounded(
            self._redis.get(self._recovery_key(session_id, digest))
        )
        if envelope is not None:
            payload = self.decrypt_refresh_response(
                envelope,
                session_id=session_id,
                token_digest=digest,
            )
            if mapping["state"] == "consumed" and payload["recovery_deadline"] != recovery_until:
                raise HTTPException(
                    status_code=503,
                    detail="Authentication service temporarily unavailable.",
                )
            pointer = await self._bounded(self._redis.get(self._pointer_key(session_id)))
            returned_digest = self._digest("refresh-token", payload["refresh_token"])
            if pointer != returned_digest:
                raise HTTPException(
                    status_code=503,
                    detail="Authentication service temporarily unavailable.",
                )
            return RefreshRecovery(session_id, digest, payload)
        if mapping["state"] == "consumed":
            # A consumed alias without its committed encrypted response must never
            # be treated as a new legacy session or sent upstream again.
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")

        attempt_raw = await self._bounded(self._redis.get(self._attempt_key(session_id)))
        if attempt_raw is not None:
            try:
                prior_attempt = json.loads(attempt_raw) if isinstance(attempt_raw, str) else None
            except (ValueError, TypeError):
                prior_attempt = None
            if not isinstance(prior_attempt, dict):
                raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
            if prior_attempt.get("token_digest") == digest:
                prior_started = prior_attempt.get("started_at")
                if not isinstance(prior_started, (int, float)) or isinstance(prior_started, bool):
                    raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
                phase = prior_attempt.get("phase", "ambiguous")
                if phase not in {"prepared", "ambiguous"}:
                    raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
                if phase == "ambiguous" and now >= prior_started + REFRESH_REUSE_INTERVAL_SECONDS:
                    expired = await self._expire_ambiguous_attempt(
                        session_id, digest, now
                    )
                    if expired:
                        raise RefreshRecoveryExpired()
                    raise RefreshLockBusy()
                if phase == "ambiguous":
                    active_lock = await self._bounded(
                        self._redis.get(self._lock_key(session_id))
                    )
                    if active_lock is not None:
                        raise RefreshLockBusy()
                    # The upstream outcome may have rotated the token, but no
                    # committed encrypted result exists to replay. Do not rotate again.
                    raise HTTPException(
                        status_code=503,
                        detail="Authentication service temporarily unavailable.",
                    )

        owner = secrets.token_urlsafe(24)
        attempt_start = now
        acquired = await self._eval(
            _ACQUIRE_REFRESH_SCRIPT,
            [self._lock_key(session_id), self._attempt_key(session_id)],
            [owner, str(REFRESH_LOCK_TTL_MS), repr(now), digest, str(REFRESH_LOCK_TTL_MS)],
        )
        if not isinstance(acquired, (list, tuple)) or len(acquired) != 2:
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
        if str(acquired[0]) not in {"1", "True", "true"}:
            raise RefreshLockBusy()
        try:
            attempt_start = float(acquired[1])
            if not math.isfinite(attempt_start):
                raise ValueError
        except (ValueError, TypeError):
            await self.release_refresh(RefreshAttempt(session_id, digest, "", owner, now))
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.") from None

        # Re-read under the session lock so a concurrent rotation cannot validate stale state.
        latest = await self._get_mapping(digest)
        pointer = await self._bounded(self._redis.get(self._pointer_key(session_id)))
        valid_state = latest is not None and latest["session_id"] == session_id
        if latest is not None and latest["state"] == "consumed":
            valid_state = valid_state and now < latest.get("recovery_until", 0)
        elif latest is not None and latest["state"] == "current":
            valid_state = valid_state and pointer == digest
        else:
            valid_state = False
        if not valid_state or not isinstance(pointer, str):
            await self.release_refresh(RefreshAttempt(session_id, digest, "", owner, attempt_start))
            raise RefreshRecoveryExpired()
        if time.time() >= attempt_start + REFRESH_REUSE_INTERVAL_SECONDS:
            await self.release_refresh(RefreshAttempt(session_id, digest, pointer, owner, attempt_start))
            raise RefreshRecoveryExpired()
        return RefreshAttempt(session_id, digest, pointer, owner, attempt_start)

    async def _expire_ambiguous_attempt(
        self, session_id: str, token_digest: str, now: float
    ) -> bool:
        """Replace expired ambiguous attempt state with the durable replay tombstone."""
        result = await self._eval(
            _EXPIRE_REFRESH_ATTEMPT_SCRIPT,
            [
                self._lock_key(session_id),
                self._attempt_key(session_id),
                self._mapping_key(token_digest),
                self._replay_key(token_digest),
            ],
            [
                session_id,
                token_digest,
                str(REFRESH_REUSE_INTERVAL_SECONDS),
                repr(now),
            ],
        )
        if str(result) in {"1", "True", "true"}:
            return True
        if str(result) in {"0", "0.0"}:
            return False
        if str(result) in {"2", "2.0"}:
            # A concurrent request may have resolved or replaced this marker.
            return False
        raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")

    async def mark_refresh_ambiguous(self, attempt: RefreshAttempt) -> None:
        """Mark immediately before sending the refresh request to Supabase."""
        result = await self._eval(
            _MARK_REFRESH_AMBIGUOUS_SCRIPT,
            [self._lock_key(attempt.session_id), self._attempt_key(attempt.session_id)],
            [attempt.lock_owner, attempt.token_digest],
        )
        if str(result) not in {"1", "True", "true"}:
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")

    async def rotate_session(
        self,
        attempt: RefreshAttempt,
        new_refresh_token: str,
        recovery_envelope: str,
        user_id: str | None = None,
    ) -> None:
        """Atomically rotate mappings and persist the encrypted response for recovery."""
        self.ensure_refresh_recovery_ready()
        new_digest = self._digest("refresh-token", new_refresh_token)
        payload = self.decrypt_refresh_response(
            recovery_envelope,
            session_id=attempt.session_id,
            token_digest=attempt.token_digest,
        )
        if not hmac.compare_digest(payload["new_token_digest"], new_digest):
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
        now = time.time()
        recovery_until = attempt.started_at + REFRESH_REUSE_INTERVAL_SECONDS
        recovery_ttl_ms = math.ceil((recovery_until - now) * 1000)
        if recovery_ttl_ms < 1:
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
        session_state_raw = await self._bounded(
            self._redis.get(self._session_state_key(attempt.session_id))
        )
        session_state = self._decode_session_state(session_state_raw, attempt.session_id)
        if session_state["status"] != "open" or session_state["expires_at"] <= now:
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")
        current_record = json.dumps(
            {"session_id": attempt.session_id, "state": "current", "recovery_until": 0,
             "expires_at": float(session_state["expires_at"])},
            separators=(",", ":"),
        )
        encrypted_session_refresh = self._encrypt_session_refresh(
            attempt.session_id, new_refresh_token, float(session_state["expires_at"])
        )
        active_key_id = self._active_recovery_key_id
        assert active_key_id is not None
        command_keys = [
            self._mapping_key(attempt.token_digest),
            self._mapping_key(new_digest),
            self._pointer_key(attempt.session_id),
            self._mapping_key(attempt.current_digest),
            self._lock_key(attempt.session_id),
            self._attempt_key(attempt.session_id),
            self._index_key(attempt.session_id),
            self._replay_key(attempt.token_digest),
            self._replay_key(attempt.current_digest),
            self._replay_key(new_digest),
            self._recovery_key(attempt.session_id, attempt.token_digest),
            self._session_state_key(attempt.session_id),
            self._session_refresh_key(attempt.session_id),
            self._key_session_index(active_key_id),
        ]
        command_args = [
            attempt.lock_owner,
            attempt.current_digest,
            attempt.session_id,
            repr(recovery_until),
            new_digest,
            repr(now),
            str(recovery_ttl_ms),
            current_record,
            recovery_envelope,
            str(recovery_ttl_ms),
            encrypted_session_refresh,
            self._digest("verified-user", user_id) if user_id else "",
            attempt.session_id,
            "careercoach:auth:encrypted-sessions-by-key:v1:",
        ]
        result: Any = None
        for retry_index in range(2):
            try:
                result = await self._eval(_ROTATE_REFRESH_SCRIPT, command_keys, command_args)
                break
            except HTTPException:
                retry_before_lease_expiry = (
                    time.time() + REDIS_TIMEOUT_SECONDS
                    < attempt.started_at + REFRESH_LOCK_TTL_MS / 1000
                )
                if retry_index or not retry_before_lease_expiry:
                    raise
        if str(result) not in {"1", "2", "True", "true"}:
            raise HTTPException(status_code=503, detail="Authentication service temporarily unavailable.")

    async def release_refresh(self, attempt: RefreshAttempt, *, clear_attempt: bool = False) -> None:
        """Release this request's lock; clear an attempt only while still its owner."""
        try:
            await self._eval(
                _RELEASE_REFRESH_SCRIPT,
                [self._lock_key(attempt.session_id), self._attempt_key(attempt.session_id)],
                [attempt.lock_owner, attempt.token_digest, "1" if clear_attempt else "0"],
            )
        except HTTPException:
            # The bounded lock lease ensures liveness after a Redis outage; rate checks remain closed.
            pass

    async def clear_session(self, refresh_token: str) -> None:
        """Close a session found through a legacy refresh-token mapping."""
        digest = self._digest("refresh-token", refresh_token)
        mapping = await self._get_mapping(digest)
        if mapping is not None:
            await self.close_session(mapping["session_id"])
        else:
            session_id = await self._bounded(self._redis.get(self._replay_key(digest)))
            if isinstance(session_id, str) and session_id:
                await self.close_session(session_id)


rate_limit_service = RateLimitService()
