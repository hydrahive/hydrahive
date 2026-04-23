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
    # Token-Budget (grobe Schätzung, nicht exact)
    agent_token_warn_per_hour: int = 500_000   # logger.warning
    # #750 Hard-Stop: raise TokenBudgetExceeded bei Überschreitung.
    # 0 = disabled. Check vor LLM-Call greift am Entry des Orchestrators.
    agent_token_hard_per_hour: int = 2_000_000
    max_login_keys: int = 10000
    max_message_keys: int = 50000
    redis_retry_after_s: int = 30
    # #783: Progressive Backoff Schwellen (Anzahl Fehlversuche → Wartezeit)
    login_failures_to_admin_notify: int = 10   # Admin-Benachrichtigung nach X Fehlversuchen
    login_backoff_tiers: tuple[tuple[int, int], ...] = (  # (failures, cooldown_s)
        (5,  300),    # 5 Fehler → 5 min Sperre
        (10, 3600),   # 10 Fehler → 1h Sperre
        (20, 86400),  # 20 Fehler → 24h Sperre
    )


class TokenBudgetExceeded(RuntimeError):
    """#750: Agent hat das Hard-Stop-Token-Budget überschritten. Subclass
    von RuntimeError für Backward-Compat mit bestehenden Handlern."""
    def __init__(self, agent_id: str, tokens_used: int, limit: int) -> None:
        self.agent_id = agent_id
        self.tokens_used = tokens_used
        self.limit = limit
        super().__init__(
            f"Agent '{agent_id}' hat das Token-Hard-Limit überschritten: "
            f"{tokens_used} > {limit} Tokens in der letzten Stunde."
        )


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
        backend = os.environ.get("HYDRAHIVE_RATE_LIMIT_BACKEND", "auto").strip().lower()
        redis_url = os.environ.get("HYDRAHIVE_RATE_LIMIT_REDIS_URL", "").strip()
        redis_timeout_s = float(os.environ.get("HYDRAHIVE_RATE_LIMIT_REDIS_TIMEOUT_S", "0.5"))

        # Token-Budget Override per Env (vorher hardcoded in RateLimitSettings).
        # Hard=0 disabled das Limit komplett. Negative Werte werden als 0 behandelt.
        def _env_int(name: str, default: int) -> int:
            raw = os.environ.get(name, "").strip()
            if not raw:
                return default
            try:
                v = int(raw)
                return v if v >= 0 else 0
            except ValueError:
                if logger:
                    logger.warning("Ungueltiger Wert fuer %s='%s' — nutze Default %d", name, raw, default)
                return default

        rl_settings = RateLimitSettings(
            agent_token_warn_per_hour=_env_int(
                "HYDRAHIVE_TOKEN_WARN_PER_HOUR", RateLimitSettings.agent_token_warn_per_hour
            ),
            agent_token_hard_per_hour=_env_int(
                "HYDRAHIVE_TOKEN_HARD_PER_HOUR", RateLimitSettings.agent_token_hard_per_hour
            ),
        )
        if logger:
            logger.info(
                "RateLimit Token-Budget: warn=%s/h hard=%s/h (override-able via HYDRAHIVE_TOKEN_WARN_PER_HOUR / HYDRAHIVE_TOKEN_HARD_PER_HOUR; 0=disabled)",
                rl_settings.agent_token_warn_per_hour,
                rl_settings.agent_token_hard_per_hour,
            )

        return cls(
            settings=rl_settings,
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
        """Prüft Login-Versuch mit progressivem Backoff (#783).

        Backoff-Tiers (per IP):
          5  Fehlversuche in Fenstern →  5 min Sperre
          10 Fehlversuche             →  1h Sperre
          20 Fehlversuche             → 24h Sperre

        Nach ``login_failures_to_admin_notify`` Fehlversuchen wird der Admin
        über HYDRAHIVE_MATRIX_WEBHOOK benachrichtigt (falls gesetzt).
        """
        import json as _json
        # Track failed attempts for progressive backoff
        failures_key = f"hydrahive:rate:login_failures:{ip}"
        failures_count = 0
        now = time.time()

        # Load existing failure count from Redis or local fallback
        def _load_failures() -> list[float]:
            if self._redis_client:
                try:
                    raw = self._redis_client.get(failures_key)
                    if raw:
                        return [float(t) for t in _json.loads(raw)]
                except Exception:
                    pass
            return []

        def _save_failures(failures: list[float]) -> None:
            # Prune entries older than 24h
            cutoff = now - 86400
            failures = [t for t in failures if t > cutoff]
            if self._redis_client:
                try:
                    self._redis_client.setex(failures_key, 86400, _json.dumps(failures))
                except Exception:
                    pass
            else:
                # Store in local dict as ip → [timestamps]
                self._login_failures = getattr(self, "_login_failures", {})
                self._login_failures[ip] = failures

        failures = _load_failures()
        if failures:
            cutoff = now - 86400
            failures = [t for t in failures if t > cutoff]
            failures_count = len(failures)

        # Determine backoff tier
        backoff_s = 0
        for threshold, cooldown_s in self.settings.login_backoff_tiers:
            if failures_count >= threshold:
                backoff_s = cooldown_s

        # Check rate limit first
        key = f"hydrahive:rate:login:{ip}"
        redis_allowed = self._redis_check(key, self.settings.login_max, self.settings.login_window_s)
        if redis_allowed is not None:
            if not redis_allowed:
                # Add failure timestamp for progressive backoff tracking
                failures.append(now)
                _save_failures(failures)
                # Notify admin if threshold exceeded
                self._notify_admin_brute_force(ip, failures_count + 1)
                raise HTTPException(429, "Zu viele Login-Versuche — bitte warten")
            return

        if not self._check_local(
            self._login_attempts,
            ip,
            limit=self.settings.login_max,
            window_s=self.settings.login_window_s,
        ):
            # Record failure for progressive backoff
            failures.append(now)
            _save_failures(failures)
            self._notify_admin_brute_force(ip, failures_count + 1)
            msg = "Zu viele Login-Versuche"
            if backoff_s >= 3600:
                msg += f" — Kontotyp gesperrt für {backoff_s // 3600}h"
            elif backoff_s >= 60:
                msg += f" — gesperrt für {backoff_s // 60} min"
            raise HTTPException(429, msg)

        # If we passed the check but have an active backoff, enforce it
        if backoff_s > 0:
            # Check if last failure is still within backoff window
            if failures and (now - failures[-1]) < backoff_s:
                remaining = int(backoff_s - (now - failures[-1]))
                msg = f"Zu viele Login-Versuche — gesperrt für {remaining}s"
                raise HTTPException(429, msg)

    def _notify_admin_brute_force(self, ip: str, failures_count: int) -> None:
        """Sendet Admin-Notification bei Brute-Force-Verdacht (#783)."""
        if failures_count < self.settings.login_failures_to_admin_notify:
            return
        webhook_url = os.environ.get("HYDRAHIVE_MATRIX_WEBHOOK", "").strip()
        if not webhook_url:
            webhook_url = os.environ.get("HYDRAHIVE_DISCORD_WEBHOOK", "").strip()
        if not webhook_url:
            return
        import asyncio
        try:
            import httpx
        except ImportError:
            return
        body = {
            "content": (
                f"🚨 **HydraHive Brute-Force Alarm**\n"
                f"IP: `{ip}`\n"
                f"Fehlversuche: {failures_count}\n"
                f"Zeit: <t:int>{int(time.time())}</t:int>"
            )
        }
        async def _send():
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(webhook_url, json=body)
            except Exception:
                pass
        # Fire-and-forget; don't block the login check
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                asyncio.create_task(_send())
            else:
                loop.run_until_complete(_send())
        except Exception:
            pass

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
        Für distributed Setup wird Redis benötigt — kein lokaler Fallback mehr.
        """
        redis_key = f"hydrahive:rate:agent_call:{agent_id}"
        redis_allowed = self._redis_check(
            redis_key, self.settings.agent_call_max, self.settings.agent_call_window_s
        )

        if redis_allowed is None:
            self.logger.warning(
                "Redis nicht erreichbar für Rate-Check von Agent '%s' — "
                "skippe Rate-Limit für diesen Call (distributed Setup benötigt Redis).",
                agent_id,
            )
            return

        if not redis_allowed:
            raise RuntimeError(
                f"Agent '{agent_id}' hat das Call-Limit überschritten "
                f"({self.settings.agent_call_max} interne Calls/Minute). "
                f"Möglicher Agent-Loop oder Kostenexplosion — wird blockiert."
            )

    def _resolve_thresholds(
        self,
        warn_override: int | None = None,
        hard_override: int | None = None,
    ) -> tuple[int, int]:
        """#820: Effektive (warn, hard) — Project-Override hat Vorrang vor
        globalem Default. None = nicht überschrieben, dann globaler Wert.
        0 = Limit deaktiviert (override turns it off pro Projekt)."""
        warn = self.settings.agent_token_warn_per_hour if warn_override is None else max(0, int(warn_override))
        hard = self.settings.agent_token_hard_per_hour if hard_override is None else max(0, int(hard_override))
        return warn, hard

    def track_token_usage(
        self,
        agent_id: str,
        tokens: int,
        *,
        warn_override: int | None = None,
        hard_override: int | None = None,
    ) -> None:
        """Token-Verbrauch eines Agents tracken, Warning + Hard-Stop (#750).

        Raises TokenBudgetExceeded wenn der Hard-Threshold nach dem Update
        überschritten wurde. Das aktuelle Usage-Event ist gespeichert, der
        nächste LLM-Call wird durch den Entry-Check im Orchestrator
        blockiert — diese Exception hier ist die letzte Verteidigungslinie.

        #820: warn/hard können pro Aufruf überschrieben werden (Project-
        Override). None = globaler Default. 0 = deaktiviert.
        """
        now = time.time()
        hour_ago = now - 3600
        usage = self._agent_token_usage[agent_id]
        # Alte Einträge bereinigen
        self._agent_token_usage[agent_id] = [(t, n) for t, n in usage if t > hour_ago]
        self._agent_token_usage[agent_id].append((now, tokens))
        total_hour = sum(n for _, n in self._agent_token_usage[agent_id])
        warn, hard = self._resolve_thresholds(warn_override, hard_override)
        if warn > 0 and total_hour > warn:
            self.logger.warning(
                "Token-Budget-Warnung: Agent '%s' hat ~%d Tokens in der letzten Stunde verbraucht "
                "(Warn-Limit: %d). Prüfe auf Agent-Loops oder unerwartete Aktivität.",
                agent_id, total_hour, warn,
            )
        if hard > 0 and total_hour > hard:
            self.logger.error(
                "AUDIT[token_budget_hard]: agent=%s tokens=%d limit=%d — Hard-Stop.",
                agent_id, total_hour, hard,
            )
            raise TokenBudgetExceeded(agent_id, total_hour, hard)

    def check_token_budget(
        self,
        agent_id: str,
        estimated_next_call_tokens: int = 0,
        *,
        hard_override: int | None = None,
    ) -> None:
        """#750/#778: Pre-Call-Gate mit optionaler Call-Groessen-Schaetzung.

        Raist TokenBudgetExceeded wenn `total + estimated_next_call_tokens > hard`.
        Damit wird ein einzelner Call mit riesigem Kontext geblockt, bevor er
        die Kosten verursacht — nicht erst danach wie bei track_token_usage.

        Backwards-compatible: estimated_next_call_tokens=0 → altes Verhalten
        (nur kumulierte History wird geprueft).

        #820: hard_override (None=globaler Default, 0=deaktiviert,
        >0=Projekt-Schwelle).
        """
        _, hard = self._resolve_thresholds(hard_override=hard_override)
        if hard <= 0:
            return
        total = self.get_token_usage_hour(agent_id)
        projected = total + max(0, int(estimated_next_call_tokens))
        if projected > hard:
            raise TokenBudgetExceeded(agent_id, projected, hard)

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
