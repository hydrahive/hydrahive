from __future__ import annotations

import logging
import os
import secrets
import time
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from fastapi import HTTPException

RATE_LIMIT_LUA = """
local key = KEYS[1]
local now_ms = tonumber(ARGV[1])
local window_ms = tonumber(ARGV[2])
local limit = tonumber(ARGV[3])
local member = ARGV[4]
local ttl_s = tonumber(ARGV[5])

redis.call("ZREMRANGEBYSCORE", key, "-inf", now_ms - window_ms)
local count = redis.call("ZCARD", key)
if count >= limit then
    return 0
end

redis.call("ZADD", key, now_ms, member)
redis.call("EXPIRE", key, ttl_s)
return 1
"""


@dataclass(frozen=True)
class RateLimitSettings:
    login_max: int = 5
    login_window_s: int = 60
    message_max: int = 50
    message_window_s: int = 60
    # Agent-interne Calls (ask_agent, delegate_agent, spawn_agent)
    agent_call_max: int = 30       # max. interne Calls pro Agent pro Minute
    agent_call_window_s: int = 60
    # Token-Budget-Warning (grobe Schätzung, nicht exact)
    agent_token_warn_per_hour: int = 500_000
    max_login_keys: int = 10000
    max_message_keys: int = 50000
    redis_retry_after_s: int = 30


class RateLimiter:
    def __init__(
        self,
        *,
        settings: RateLimitSettings | None = None,
        backend: str = "auto",
        redis_url: str = "",
        redis_timeout_s: float = 0.5,
        logger: logging.Logger | None = None,
        redis_client: Any | None = None,
        redis_script: Any | None = None,
    ) -> None:
        self.settings = settings or RateLimitSettings()
        self.backend = backend
        self.redis_url = redis_url.strip()
        self.redis_timeout_s = redis_timeout_s
        self.logger = logger or logging.getLogger(__name__)
        self._redis_client = redis_client
        self._redis_script = redis_script
        self._redis_failed = False
        self._redis_failed_at = 0.0
        self._login_attempts: dict[str, list[float]] = defaultdict(list)
        self._message_attempts: dict[tuple[str, str], list[float]] = defaultdict(list)
        self._agent_call_attempts: dict[str, list[float]] = defaultdict(list)
        self._agent_token_usage: dict[str, list[tuple[float, int]]] = defaultdict(list)

        if self._redis_client is None and self._redis_script is None:
            self._configure_redis_backend()

    @classmethod
    def from_env(cls, logger: logging.Logger | None = None) -> "RateLimiter":
        backend = os.environ.get("HYDRAHIVE_RATE_LIMIT_BACKEND", os.environ.get("HYDRAHIVE_RATE_LIMIT_BACKEND", "auto")).strip().lower()
        redis_url = os.environ.get("HYDRAHIVE_RATE_LIMIT_REDIS_URL", os.environ.get("HYDRAHIVE_RATE_LIMIT_REDIS_URL", "")).strip()
        redis_timeout_s = float(os.environ.get("HYDRAHIVE_RATE_LIMIT_REDIS_TIMEOUT_S", os.environ.get("HYDRAHIVE_RATE_LIMIT_REDIS_TIMEOUT_S", "0.5")))
        return cls(
            backend=backend,
            redis_url=redis_url,
            redis_timeout_s=redis_timeout_s,
            logger=logger,
        )

    def _configure_redis_backend(self) -> None:
        use_redis = self.backend == "redis" or (self.backend == "auto" and self.redis_url)
        if not use_redis or not self.redis_url:
            return

        try:
            import redis as redis_lib
        except Exception as exc:  # pragma: no cover - optional dependency fallback
            self.logger.warning("Redis-Paket nicht verfuegbar, nutze lokales Rate-Limiting: %s", exc)
            return

        try:
            self._redis_client = redis_lib.Redis.from_url(
                self.redis_url,
                decode_responses=True,
                socket_timeout=self.redis_timeout_s,
                socket_connect_timeout=self.redis_timeout_s,
                health_check_interval=30,
            )
            self._redis_script = self._redis_client.register_script(RATE_LIMIT_LUA)
            self.logger.info("Distributed rate limiting aktiviert via Redis: %s", self.redis_url)
        except Exception as exc:
            self.logger.warning("Redis-Rate-Limiter nicht aktivierbar, nutze lokales Fallback: %s", exc)
            self._redis_client = None
            self._redis_script = None

    def _redis_check(self, key: str, limit: int, window_s: int) -> bool | None:
        if self._redis_client is None or self._redis_script is None:
            return None

        if self._redis_failed:
            retry_after = max(int(self.settings.redis_retry_after_s), 1)
            if time.time() - self._redis_failed_at < retry_after:
                return None

        now_ms = int(time.time() * 1000)
        ttl_s = max(window_s + 60, 120)
        member = f"{now_ms}:{secrets.token_hex(8)}"
        try:
            result = self._redis_script(
                keys=[key],
                args=[now_ms, window_s * 1000, limit, member, ttl_s],
            )
            self._redis_failed = False
            self._redis_failed_at = 0.0
            return bool(int(result))
        except Exception as exc:
            self._redis_failed = True
            self._redis_failed_at = time.time()
            self.logger.warning("Redis-Rate-Limiter nicht erreichbar, wechsle auf lokales Fallback: %s", exc)
            return None

    def _prune_local(self, attempts: dict[Any, list[float]], window_s: int) -> None:
        now = time.time()
        remove_keys = [k for k, ts in attempts.items() if not any(now - t < window_s for t in ts)]
        for key in remove_keys:
            attempts.pop(key, None)

    def cleanup_local(self) -> None:
        self._prune_local(self._login_attempts, self.settings.login_window_s)
        self._prune_local(self._message_attempts, self.settings.message_window_s)

    def _check_local(
        self,
        attempts: dict[Any, list[float]],
        key: Any,
        *,
        limit: int,
        window_s: int,
    ) -> bool:
        now = time.time()
        max_keys = self.settings.max_login_keys if attempts is self._login_attempts else self.settings.max_message_keys
        if len(attempts) > max_keys:
            self._prune_local(attempts, window_s)
            if len(attempts) > max_keys:
                overflow = len(attempts) - max_keys
                for old_key in list(attempts.keys())[:overflow]:
                    attempts.pop(old_key, None)

        attempts[key] = [t for t in attempts[key] if now - t < window_s]
        if len(attempts[key]) >= limit:
            return False
        attempts[key].append(now)
        return True

    def check_login(self, ip: str) -> None:
        key = f"hydrahive:rate:login:{ip}"
        redis_allowed = self._redis_check(key, self.settings.login_max, self.settings.login_window_s)
        if redis_allowed is not None:
            if not redis_allowed:
                raise HTTPException(429, "Zu viele Login-Versuche — bitte eine Minute warten")
            return

        if not self._check_local(
            self._login_attempts,
            ip,
            limit=self.settings.login_max,
            window_s=self.settings.login_window_s,
        ):
            raise HTTPException(429, "Zu viele Login-Versuche — bitte eine Minute warten")

    def check_message(self, user_id: str, project_id: str) -> None:
        key = (user_id, project_id)
        redis_key = f"hydrahive:rate:message:{user_id}:{project_id}"
        redis_allowed = self._redis_check(redis_key, self.settings.message_max, self.settings.message_window_s)
        if redis_allowed is not None:
            if not redis_allowed:
                raise HTTPException(429, f"Zu viele Nachrichten — max. {self.settings.message_max} pro Minute")
            return

        if not self._check_local(
            self._message_attempts,
            key,
            limit=self.settings.message_max,
            window_s=self.settings.message_window_s,
        ):
            raise HTTPException(429, f"Zu viele Nachrichten — max. {self.settings.message_max} pro Minute")

    def check_agent_call(self, agent_id: str) -> None:
        """Rate-Limit für interne Agent-Calls (ask_agent, delegate_agent, spawn_agent).
        Verhindert unkontrollierte Agent-Kaskaden und Kostenexplosionen.
        """
        redis_key = f"hydrahive:rate:agent_call:{agent_id}"
        redis_allowed = self._redis_check(redis_key, self.settings.agent_call_max, self.settings.agent_call_window_s)
        if redis_allowed is not None:
            if not redis_allowed:
                raise RuntimeError(
                    f"Agent '{agent_id}' hat das Call-Limit überschritten "
                    f"({self.settings.agent_call_max} interne Calls/Minute). "
                    f"Möglicher Agent-Loop oder Kostenexplosion — wird blockiert."
                )
            return

        if not self._check_local(
            self._agent_call_attempts,
            agent_id,
            limit=self.settings.agent_call_max,
            window_s=self.settings.agent_call_window_s,
        ):
            raise RuntimeError(
                f"Agent '{agent_id}' hat das Call-Limit überschritten "
                f"({self.settings.agent_call_max} interne Calls/Minute). "
                f"Möglicher Agent-Loop oder Kostenexplosion — wird blockiert."
            )

    def track_token_usage(self, agent_id: str, tokens: int) -> None:
        """Tokn-Verbrauch eines Agents tracken und Warning loggen wenn zu hoch."""
        now = time.time()
        hour_ago = now - 3600
        usage = self._agent_token_usage[agent_id]
        # Alte Einträge bereinigen
        self._agent_token_usage[agent_id] = [(t, n) for t, n in usage if t > hour_ago]
        self._agent_token_usage[agent_id].append((now, tokens))
        total_hour = sum(n for _, n in self._agent_token_usage[agent_id])
        if total_hour > self.settings.agent_token_warn_per_hour:
            self.logger.warning(
                "Token-Budget-Warnung: Agent '%s' hat ~%d Tokens in der letzten Stunde verbraucht "
                "(Limit: %d). Prüfe auf Agent-Loops oder unerwartete Aktivität.",
                agent_id, total_hour, self.settings.agent_token_warn_per_hour,
            )

    def get_token_usage_hour(self, agent_id: str) -> int:
        """Gibt den geschätzten Token-Verbrauch des Agents in der letzten Stunde zurück."""
        now = time.time()
        hour_ago = now - 3600
        return sum(n for t, n in self._agent_token_usage.get(agent_id, []) if t > hour_ago)

    def get_token_history(self, agent_id: str, minutes: int = 60, bucket_minutes: int = 1) -> list[dict]:
        """Token-Usage als Zeitreihe in Minuten-Buckets (für Sparkline-Charts)."""
        now = time.time()
        cutoff = now - minutes * 60
        entries = [(t, n) for t, n in self._agent_token_usage.get(agent_id, []) if t > cutoff]
        num_buckets = minutes // bucket_minutes
        buckets = [0] * num_buckets
        for ts, tokens in entries:
            age_min = (now - ts) / 60
            idx = num_buckets - 1 - int(age_min / bucket_minutes)
            if 0 <= idx < num_buckets:
                buckets[idx] += tokens
        return [{"minute": i * bucket_minutes, "tokens": b} for i, b in enumerate(buckets)]
