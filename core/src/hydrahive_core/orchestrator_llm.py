"""
orchestrator_llm.py — LLM-Call-Maschinerie

Standalone-Funktionen für LLM-Aufrufe (kein Orchestrator-State nötig):
- Failover-Logik (_should_failover, _llm_with_retry)
- Token-Loader (_load_claude_oauth_token, _load_openai_codex_token)
- Model-Resolution (_resolve_model)
- Einzelner LLM-Call (_llm_call_single)
- Failover-LLM-Call (_llm_call)
- Anthropic OAuth-Call (non-streaming)
- OpenAI Codex OAuth-Call
"""
from __future__ import annotations

import asyncio
import contextvars
import json as _json
import logging
import os
from pathlib import Path
from typing import Any

import litellm
from pydantic import BaseModel, ValidationError

# #845: Anthropic erfordert tools= wenn die History tool_use-Blöcke enthält.
# Der Tool-Loop-Abort-Pfad (_finalize_tool_loop_response) ruft _llm_call mit
# tools=None auf, was sonst zu UnsupportedParamsError führt. modify_params=True
# lässt litellm bei Bedarf einen Dummy-Tool anhängen — macht den Recovery-Call
# robust, ohne dass Call-Sites sich darum kümmern müssen.
litellm.modify_params = True

from .llm_config_validation import clean_provider_base_url, clean_provider_secret
from .settings import settings

logger = logging.getLogger(__name__)

# #512: ContextVar für project_id — wird vom Orchestrator gesetzt,
# damit _llm_with_retry Retry/Failover-Metriken erfassen kann
_current_project_id: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "_current_project_id", default=None,
)


# #501: Cache-Break-Detection — Fingerprints pro Agent
_CACHE_FINGERPRINTS: dict[str, dict] = {}  # agent_id → {had_write, last_write, last_read}

# ---------------------------------------------------------------- Prompt Caching

def _split_system_at_memory_marker(content: str) -> list[dict] | None:
    """#629: Wenn der System-Prompt den <memory_dynamic>-Marker enthält (gesetzt
    durch ContextChannels in #627), splitten wir ihn in zwei content-blocks:
    - statischer Vorlauf (cacheable)
    - dynamischer Rest (memory + last_session + skills + ...; NICHT cachen)

    So bleibt der statische Block byte-identisch zwischen Turns und der Cache
    überlebt Memory-Wechsel. Returns None wenn kein Marker gefunden.
    """
    from .context_channels import MEMORY_OPEN
    if not isinstance(content, str) or MEMORY_OPEN not in content:
        return None
    static_part, _, dynamic_part = content.partition(MEMORY_OPEN)
    static_part = static_part.rstrip()
    dynamic_part = (MEMORY_OPEN + dynamic_part).strip()
    if not static_part or not dynamic_part:
        return None
    return [
        {"type": "text", "text": static_part, "cache_control": {"type": "ephemeral"}},
        {"type": "text", "text": dynamic_part},
    ]


def _apply_cache_control(messages: list[dict], is_anthropic: bool) -> list[dict]:
    """
    Fügt Anthropic Prompt Caching Cache-Breakpoints ein (max 4 erlaubt).
    Strategie (4 Breakpoints):
    1. System-Message: bei <memory_dynamic>-Marker → 2 Blöcke (static cached,
       dynamic ungecached). Sonst: gesamter System-Block cached.
    2. Erste User-Message (Kontext-Anfang)
    3. Mittlere History-Message (bei langen Conversations)
    4. Vorletzte Message (nahe am aktuellen Turn)
    Für nicht-Anthropic-Modelle werden die Messages unverändert zurückgegeben.
    """
    if not is_anthropic:
        return messages

    result = list(messages)
    _MAX_CACHE = 4
    used = 0

    def _tag_cache(msg: dict) -> dict:
        """Setzt cache_control auf den letzten Content-Block einer Message."""
        content = msg.get("content", "")
        if isinstance(content, str) and content:
            return {**msg, "content": [{"type": "text", "text": content,
                                        "cache_control": {"type": "ephemeral"}}]}
        elif isinstance(content, list) and content:
            new_c = list(content)
            if not new_c[-1].get("cache_control"):
                new_c[-1] = {**new_c[-1], "cache_control": {"type": "ephemeral"}}
            return {**msg, "content": new_c}
        return msg

    # Breakpoint 1: System-Message — #629 Segmentierung am Memory-Marker
    for i, m in enumerate(result):
        if m.get("role") == "system" and used < _MAX_CACHE:
            segmented = _split_system_at_memory_marker(m.get("content", ""))
            if segmented is not None:
                result[i] = {**m, "content": segmented}
                logger.debug("system-prompt segmentiert: static=%d chars (cached), dynamic=%d chars",
                             len(segmented[0]["text"]), len(segmented[1]["text"]))
            else:
                result[i] = _tag_cache(m)
            used += 1
            break

    # Breakpoints 2-4: strategische History-Positionen
    non_sys_indices = [i for i, m in enumerate(result) if m.get("role") in ("user", "assistant")]
    if non_sys_indices and used < _MAX_CACHE:
        # Positionen: Anfang, Mitte, vorletzter
        targets = set()
        targets.add(non_sys_indices[0])  # erste User/Assistant Message
        if len(non_sys_indices) >= 4:
            targets.add(non_sys_indices[len(non_sys_indices) // 2])  # Mitte
        if len(non_sys_indices) >= 2:
            targets.add(non_sys_indices[-2])  # vorletzte

        for idx in sorted(targets):
            if used >= _MAX_CACHE:
                break
            result[idx] = _tag_cache(result[idx])
            used += 1

    return result


# ---------------------------------------------------------------- OAuth Rate Limits

# Globaler State: letzte bekannte Rate-Limit-Werte aus Anthropic Response-Headers
# Persistiert unter /etc/hydrahive/oauth_usage.json damit Daten Neustarts überleben.
import json as _json
from pathlib import Path as _Path

_OAUTH_CACHE_FILE = _Path("/etc/hydrahive/oauth_usage.json")


def _load_oauth_cache() -> dict:
    """Beim Start gespeicherte OAuth-Daten von Disk laden."""
    try:
        if _OAUTH_CACHE_FILE.exists():
            return _json.loads(_OAUTH_CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def _save_oauth_cache(data: dict) -> None:
    """OAuth-Daten auf Disk persistieren (fire-and-forget)."""
    try:
        _OAUTH_CACHE_FILE.write_text(_json.dumps(data, indent=2), encoding="utf-8")
    except Exception:
        pass


_oauth_rate_limits: dict = _load_oauth_cache()


def _extract_rate_limit_headers(headers) -> None:
    """Anthropic Rate-Limit Headers parsen und in globalem State speichern.

    Headers (anthropic-ratelimit-unified-*):
    - 5h-utilization, 5h-reset, 5h-surpassed-threshold
    - 7d-utilization, 7d-reset, 7d-surpassed-threshold
    - overage-utilization, overage-reset, overage-status
    - status, representative-claim, fallback
    """
    from datetime import datetime, timezone as _tz

    prefix = "anthropic-ratelimit-unified-"
    data: dict = {"updated_at": datetime.now(_tz.utc).isoformat()}

    for key in ("status", "representative-claim", "fallback", "reset"):
        val = headers.get(f"{prefix}{key}")
        if val:
            data[key.replace("-", "_")] = val

    for window in ("5h", "7d"):
        util = headers.get(f"{prefix}{window}-utilization")
        reset = headers.get(f"{prefix}{window}-reset")
        threshold = headers.get(f"{prefix}{window}-surpassed-threshold")
        if util is not None:
            try:
                data[f"{window}_utilization"] = float(util)
            except ValueError:
                pass
        if reset:
            data[f"{window}_reset"] = reset
        if threshold:
            data[f"{window}_surpassed_threshold"] = threshold

    # Overage / Extra Usage
    for key in ("overage-status", "overage-reset", "overage-utilization",
                "overage-disabled-reason", "overage-surpassed-threshold"):
        val = headers.get(f"{prefix}{key}")
        if val:
            k = key.replace("-", "_")
            if "utilization" in key:
                try:
                    data[k] = float(val)
                except ValueError:
                    data[k] = val
            else:
                data[k] = val

    if len(data) > 1:  # mehr als nur updated_at
        global _oauth_rate_limits
        _oauth_rate_limits = data
        _save_oauth_cache(data)
        logger.debug("OAuth rate limits updated: %s", data)


def get_oauth_rate_limits() -> dict:
    """Aktuelle OAuth Rate-Limit-Daten abrufen (für API-Endpoint)."""
    return dict(_oauth_rate_limits)


# ---------------------------------------------------------------- Failover

_FAILOVER_SIGNALS = [
    "401", "authentication_error", "expired", "oauth token has expired",
    "invalid api key", "invalid x-api-key", "unauthorized",
    "402", "payment", "credit", "quota", "insufficient",
    "429", "rate_limit", "rate limit",
    "529", "overloaded", "capacity", "credit_balance",
    "your credit balance is too low",
    "exceeded your current quota",
    "this request would exceed",
    "billing",
]


def _should_failover(exc: Exception) -> bool:
    """True wenn der Fehler einen Modell-Wechsel rechtfertigt (Quota/Overload)."""
    err = str(exc).lower()
    return any(s in err for s in _FAILOVER_SIGNALS)


# #423: Preemptiver Rate-Limit Cooldown (shouldWait)
_rate_limit_cooldown: dict[str, float] = {}  # provider → resume_timestamp

def _should_wait(provider: str = "default") -> float:
    """Gibt verbleibende Wartezeit in Sekunden zurück (0 = kein Cooldown)."""
    import time as _t
    resume = _rate_limit_cooldown.get(provider, 0)
    remaining = resume - _t.time()
    return max(0.0, remaining)

def _set_cooldown(provider: str = "default", seconds: float = 5.0) -> None:
    import time as _t
    _rate_limit_cooldown[provider] = _t.time() + seconds


async def _llm_with_retry(coro_factory, max_attempts: int = 5, base_delay: float = 1.0):
    """
    Retry-Wrapper für LLM-Calls (#423: shouldWait + Exponential Backoff, #504 differenziert).
    - 429 Rate-Limit: retry mit Backoff (Cooldown setzen)
    - 529 Overloaded: max 2 Retries, dann sofort Failover
    - 5xx Server-Fehler: retry
    - 401/403 Auth: kein Retry
    - Quota/Billing: kein Retry (→ Failover)
    - Timeout: retry
    Exponential Backoff mit 10% Jitter, max 60s.
    """
    import random as _random

    # #423: Preemptiver Wait wenn Cooldown aktiv
    wait = _should_wait()
    if wait > 0:
        logger.info("Rate-Limit Cooldown aktiv — warte %.1fs", wait)
        await asyncio.sleep(wait)

    last_exc = None
    for attempt in range(max_attempts):
        try:
            return await coro_factory()
        except Exception as e:
            last_exc = e
            err_str = str(e).lower()

            # Auth-Fehler → kein Retry
            if any(x in err_str for x in ["401", "403", "unauthorized", "forbidden", "authentication"]):
                raise

            # Quota/Billing erschöpft → kein Retry, Failover
            if any(x in err_str for x in ["quota", "credit", "billing", "payment"]):
                raise

            # #504: 529 Overloaded — schneller failovern (max 2 Retries)
            is_overloaded = any(x in err_str for x in ["529", "overloaded", "capacity"])
            if is_overloaded:
                if attempt >= 2:  # Nach 2 Versuchen sofort Failover
                    logger.warning("Server overloaded nach %d Versuchen — Failover", attempt + 1)
                    raise
                delay = min(base_delay * (2 ** attempt) * 2, 30.0)  # Längere Pausen bei Overload
                delay *= (1 + _random.uniform(-0.1, 0.1))
                _pid = _current_project_id.get()
                if _pid:
                    from .session_metrics import metrics as _m
                    _m.record_retry(_pid)
                logger.warning(
                    "Server overloaded (Versuch %d/3): %s — retry in %.1fs",
                    attempt + 1, str(e)[:80], delay,
                )
                await asyncio.sleep(delay)
                continue

            # 429 Rate-Limit → retry mit Backoff + Cooldown setzen
            is_rate_limit = any(x in err_str for x in ["rate_limit", "rate limit", "429"])
            if is_rate_limit:
                delay = min(base_delay * (2 ** attempt), 60.0)
                delay *= (1 + _random.uniform(-0.1, 0.1))
                _set_cooldown(seconds=delay)
                # #512: Retry-Metrik für Rate-Limits
                _pid = _current_project_id.get()
                if _pid:
                    from .session_metrics import metrics as _m
                    _m.record_retry(_pid)
                    # #523: Turn Journal — Retry Event
                    try:
                        from .turn_journal import journal as _tj, EventType as _JE
                        _tj.append("", _pid, _JE.RETRY, {"attempt": attempt + 1, "reason": "rate_limit"})
                    except Exception:
                        pass
                logger.warning(
                    "Rate-Limit (Versuch %d/%d): %s — retry in %.1fs",
                    attempt + 1, max_attempts, str(e)[:80], delay,
                )
                if attempt == max_attempts - 1:
                    raise
                await asyncio.sleep(delay)
                continue

            # Letzter Versuch → aufgeben
            if attempt == max_attempts - 1:
                raise

            # #512: Retry-Metrik erfassen
            _pid = _current_project_id.get()
            if _pid:
                from .session_metrics import metrics as _m
                _m.record_retry(_pid)

            # Backoff berechnen: 1s, 2s, 4s... max 60s + Jitter
            delay = min(base_delay * (2 ** attempt), 60.0)
            delay *= (1 + _random.uniform(-0.1, 0.1))  # 10% Jitter

            logger.warning(
                "LLM-Fehler (Versuch %d/%d): %s — retry in %.1fs",
                attempt + 1, max_attempts, str(e)[:80], delay
            )
            await asyncio.sleep(delay)

    raise last_exc


# ---------------------------------------------------------------- Token-Loader

def _load_claude_oauth_token() -> str:
    """Laedt Claude OAuth Token — prüft zuerst ANTHROPIC_API_KEY (langlebiger
    Terminal-Token), dann claude_oauth_token (Console-OAuth, ggf. mit Refresh)."""
    import json as _json, time as _time

    # Priorität 1: Terminal-Token aus llm_env (1 Jahr gültig, kein Refresh nötig)
    env_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
    if env_key and env_key.startswith("sk-ant-oat01-"):
        return env_key

    # Priorität 2: Console-OAuth-Token (kurzlebig, mit Refresh)
    token_file = settings.claude_oauth_token
    try:
        raw = token_file.read_text(encoding="utf-8").strip()
    except OSError:
        return ""

    # Legacy-Format: nur der Token als String
    if raw.startswith("sk-ant-oat01-"):
        return raw

    # Neues JSON-Format mit refresh_token
    try:
        data = _json.loads(raw)
    except (ValueError, _json.JSONDecodeError):
        return ""

    access_token = data.get("access_token", "")
    refresh_token = data.get("refresh_token", "")
    expires_at = data.get("expires_at", 0)

    # Token noch gültig (5 Min Puffer)?
    if access_token and _time.time() < expires_at - 300:
        return access_token

    # Refresh nötig
    if not refresh_token:
        return access_token or ""

    try:
        refreshed = _refresh_claude_token(refresh_token, token_file)
        if refreshed:
            return refreshed
    except Exception as _e:
        _logger = logging.getLogger(__name__)
        _logger.warning("Claude OAuth refresh fehlgeschlagen: %s", _e)

    return access_token or ""


def _refresh_claude_token(refresh_token: str, token_file: Path) -> str:
    """Synchroner Token-Refresh via Anthropic OAuth endpoint."""
    import json as _json, time as _time
    import urllib.request

    body = _json.dumps({
        "grant_type": "refresh_token",
        "client_id": "9d1c250a-e61b-44d9-88ed-5944d1962f5e",
        "refresh_token": refresh_token,
    }).encode()

    req = urllib.request.Request(
        "https://console.anthropic.com/v1/oauth/token",
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = _json.loads(resp.read())

    new_access = data.get("access_token", "")
    new_refresh = data.get("refresh_token", refresh_token)
    expires_in = data.get("expires_in", 3600)

    if not new_access:
        return ""

    token_file.write_text(
        _json.dumps({
            "access_token": new_access,
            "refresh_token": new_refresh,
            "expires_at": int(_time.time()) + expires_in,
        }, indent=2),
        encoding="utf-8",
    )
    token_file.chmod(0o600)

    _logger = logging.getLogger(__name__)
    _logger.info("Claude OAuth Token refreshed (expires in %ds)", expires_in)
    return new_access


def _load_openai_codex_token() -> dict | None:
    """Laedt OpenAI Codex OAuth Token (ChatGPT Plus/Pro)."""
    import json as _json
    token_file = settings.openai_codex_token
    try:
        data = _json.loads(token_file.read_text(encoding="utf-8"))
        if data.get("access_token") and data.get("account_id"):
            return data
    except OSError:
        pass
    return None


# ---------------------------------------------------------------- Provider-Check

def check_llm_provider_available(models: list[str], ollama_base_url: str | None = None) -> str | None:
    """
    Prüft ob für die übergebenen Modelle ein Provider verfügbar ist.
    Gibt None zurück wenn OK, sonst eine nutzerfreundliche Fehlermeldung
    mit Debug-Info (welche Modelle geprüft, welche Kategorie erkannt).
    ollama_base_url: wenn gesetzt (WKS-Ollama), wird dieser Endpunkt geprüft statt localhost.
    """
    import os
    import socket
    from urllib.parse import urlparse

    # #813 Debug: für jeden gescheiterten Model-Check mitprotokollieren,
    # damit der User im Hint sehen kann welche Klassifizierung stattfand
    # und welche Key-Quelle erwartet wurde.
    _trace: list[str] = []

    for model in models:
        if not model:
            continue
        is_claude  = model.startswith(("claude-", "anthropic/"))
        is_openai  = model.startswith(("gpt-", "o1-", "o3-", "openai/", "openai-codex/"))
        is_minimax = model.startswith("MiniMax-") or model.startswith("minimax/")
        is_nvidia  = _is_nvidia_model(model)
        is_ollama  = (not is_minimax) and (not is_nvidia) and (
            model.startswith(("ollama/", "ollama_chat/")) or (
                not is_claude and not is_openai and "/" not in model
            )
        )
        category = (
            "nvidia" if is_nvidia else
            "minimax" if is_minimax else
            "claude" if is_claude else
            "openai" if is_openai else
            "ollama" if is_ollama else
            "unknown"
        )

        if is_minimax:
            if os.environ.get("MINIMAX_API_KEY", "").strip():
                return None
            try:
                cfg = _load_llm_config()
                if cfg.get("providers", {}).get("minimax", {}).get("api_key", "").strip():
                    return None
            except Exception:
                pass
            try:
                _env_file = settings.llm_env
                if _env_file.exists():
                    for line in _env_file.read_text().splitlines():
                        if line.startswith("MINIMAX_API_KEY=") and line.split("=", 1)[1].strip():
                            return None
            except OSError:
                pass
            _trace.append(
                f"'{model}' [minimax] — kein Key in providers.minimax.api_key, "
                "$MINIMAX_API_KEY oder llm_env"
            )
            continue

        if is_nvidia:
            if os.environ.get("NVIDIA_API_KEY", "").strip():
                return None
            try:
                cfg = _load_llm_config()
                if cfg.get("providers", {}).get("nvidia", {}).get("api_key", "").strip():
                    return None
            except Exception:
                pass
            try:
                _env_file = settings.llm_env
                if _env_file.exists():
                    for line in _env_file.read_text().splitlines():
                        if line.startswith("NVIDIA_API_KEY=") and line.split("=", 1)[1].strip():
                            return None
            except OSError:
                pass
            _trace.append(
                f"'{model}' [nvidia] — kein Key in providers.nvidia.api_key, "
                "$NVIDIA_API_KEY oder llm_env. Hinweis: Einstellungen → LLM → "
                "NVIDIA speichert unter providers.nvidia.api_key; pruefe dass "
                "dort wirklich ein Wert steht (nicht nur enabled:true)."
            )
            continue

        if is_claude:
            if _load_claude_oauth_token():
                return None
            if os.environ.get("ANTHROPIC_API_KEY", "").strip():
                return None
            try:
                cfg = _load_llm_config()
                for p in ("anthropic", "claude_max"):
                    if cfg.get("providers", {}).get(p, {}).get("api_key", "").strip():
                        return None
            except Exception:
                pass
            _trace.append(
                f"'{model}' [claude] — weder Claude-OAuth-Token, $ANTHROPIC_API_KEY "
                "noch providers.anthropic/claude_max.api_key gesetzt"
            )

        elif is_openai:
            if _load_openai_codex_token():
                return None
            if os.environ.get("OPENAI_API_KEY", "").strip():
                return None
            try:
                cfg = _load_llm_config()
                if cfg.get("providers", {}).get("openai", {}).get("api_key", "").strip():
                    return None
            except Exception:
                pass
            _trace.append(
                f"'{model}' [openai] — weder OpenAI-Codex-Token, $OPENAI_API_KEY "
                "noch providers.openai.api_key gesetzt"
            )

        elif is_ollama:
            if ollama_base_url:
                # WKS-Ollama: Endpunkt auf der Workstation prüfen
                try:
                    parsed = urlparse(ollama_base_url)
                    host = parsed.hostname or "127.0.0.1"
                    port = parsed.port or 11434
                    with socket.create_connection((host, port), timeout=2):
                        return None
                except Exception:
                    pass
                _trace.append(
                    f"'{model}' [ollama] — Endpunkt {ollama_base_url} nicht erreichbar "
                    "(Timeout/Connection refused)"
                )
            else:
                try:
                    with socket.create_connection(("127.0.0.1", 11434), timeout=1):
                        return None
                except Exception:
                    pass
                _trace.append(
                    f"'{model}' [ollama] — lokaler Endpunkt 127.0.0.1:11434 nicht "
                    "erreichbar. Ollama installieren oder WKS-URL in Agent-Config setzen."
                )
        else:
            _trace.append(
                f"'{model}' [unknown] — Modell-ID wurde keinem Provider zugeordnet. "
                "Pruefe Schreibweise oder nimm ein Modell aus Einstellungen → LLM."
            )

    # Auch ins Core-Log schreiben (falls der Admin doch mal reinschaut).
    try:
        import logging as _logging
        _logging.getLogger(__name__).warning(
            "check_llm_provider_available: kein Provider gefunden — Modelle=%s, Trace=%s",
            models, _trace,
        )
    except Exception:
        pass

    trace_block = ""
    if _trace:
        trace_block = (
            "\n\n---\n**Geprüft beim Aufruf:**\n"
            + "\n".join(f"- {t}" for t in _trace)
        )

    return (
        "## ⚠️ Kein LLM-Provider konfiguriert\n\n"
        "Um HydraHive zu nutzen, einen Provider unter **Einstellungen → LLM** einrichten "
        "(und dort API-Key speichern — `enabled:true` allein reicht nicht):\n\n"
        "**Cloud (kein GPU nötig — empfohlen zum Starten)**\n"
        "• **Anthropic Claude** — API-Key auf [console.anthropic.com](https://console.anthropic.com) → Einstellungen → LLM → Anthropic\n"
        "• **Claude Max** (Abo) — Einstellungen → LLM → Claude Max → OAuth verbinden\n"
        "• **OpenAI GPT-4** — API-Key auf [platform.openai.com](https://platform.openai.com) → Einstellungen → LLM → OpenAI\n"
        "• **NVIDIA NIM** — API-Key auf [build.nvidia.com](https://build.nvidia.com) → Einstellungen → LLM → NVIDIA\n"
        "• **MiniMax** — API-Key auf [platform.minimax.io](https://platform.minimax.io) → Einstellungen → LLM → MiniMax\n\n"
        "**Lokal via Ollama (GPU empfohlen: NVIDIA RTX 3060+, 8 GB VRAM)**\n"
        "• `ollama pull mistral-nemo:12b` → Einstellungen → LLM → Ollama-URL eintragen"
        + trace_block
    )




def _load_llm_config() -> dict:
    # #391: Nutze mtime-cached Loader aus router_llm
    from .router_llm import _cached_json_load
    return _cached_json_load(str(settings.llm_config), {"providers": {}})


# ---------------------------------------------------------------- Model-Resolution

# #616/#771: MiniMax-M2 als eigenständiger Provider. Token-Plan-Keys sind
# mit dem Anthropic-kompatiblen Endpoint am stabilsten; OpenClaw und die
# MiniMax-Coding-Tool-Doku empfehlen diesen Transport für Agenten.
MINIMAX_DEFAULT_BASE_URL = "https://api.minimax.io/anthropic"

# #773 Followup: MiniMax-Media-Endpoints (Image/Video/Music) haben eine
# andere Base als der Chat-Endpoint — /v1 statt /anthropic. Wenn beide
# zusammengelegt werden (wie vor dem Fix), landen Media-Requests auf
# /anthropic/image_generation → 404. Media und Chat trennen.
MINIMAX_DEFAULT_MEDIA_BASE_URL = "https://api.minimax.io/v1"

# #684: NVIDIA NIM als eigenständiger Provider (OpenAI-kompatibel, eigener Key +
# eigener Endpoint). Phase-1-Startliste ist ein explizites Set — keine breite
# Namespace-Prefix-Whitelist, damit "meta/..." etc. nicht später mit anderen
# Providern kollidiert. Dynamische /v1/models-Discovery kommt später.
NVIDIA_DEFAULT_BASE_URL = "https://integrate.api.nvidia.com/v1"

NVIDIA_MODELS: frozenset[str] = frozenset({
    "minimaxai/minimax-m2.7",
    "minimaxai/minimax-m2.5",
    "meta/llama-3.3-70b-instruct",
    "nvidia/llama-3.3-nemotron-super-49b-v1.5",
    "deepseek-ai/deepseek-v3.2",
    "qwen/qwen3-coder-480b-a35b-instruct",
    "moonshotai/kimi-k2-thinking",
})


def _minimax_base_url() -> str:
    """Liefert den aktuellen MiniMax-Chat-Endpoint aus llm_config, sonst Default.

    Gilt NUR für Chat (M2.7). Media-Tools (Image/Video/Music) haben eine
    eigene Base — siehe :func:`_minimax_media_base_url`.
    """
    try:
        cfg = _load_llm_config()
        url = (cfg.get("providers", {}).get("minimax", {}).get("base_url") or "").strip()
        if url:
            return clean_provider_base_url(url, label="MiniMax base_url")
    except Exception:
        pass
    return MINIMAX_DEFAULT_BASE_URL


def _minimax_media_base_url() -> str:
    """#773 Followup: Liefert den MiniMax-Media-Endpoint (Image/Video/Music).

    Quellen: ``providers.minimax.media_base_url`` > Default ``/v1``.
    Bewusst getrennt von ``_minimax_base_url()`` — der Chat-Endpoint ist
    ``/anthropic``, der Media-Endpoint ``/v1``. Ein gemeinsamer Resolver
    kappt eine der beiden Routen (vor dem Fix: Image-Calls → 404).
    """
    try:
        cfg = _load_llm_config()
        url = (cfg.get("providers", {}).get("minimax", {}).get("media_base_url") or "").strip()
        if url:
            return clean_provider_base_url(url, label="MiniMax media_base_url")
    except Exception:
        pass
    return MINIMAX_DEFAULT_MEDIA_BASE_URL


def _nvidia_base_url() -> str:
    """Liefert den aktuellen NVIDIA NIM-Endpoint aus llm_config, sonst Default."""
    try:
        cfg = _load_llm_config()
        url = (cfg.get("providers", {}).get("nvidia", {}).get("base_url") or "").strip()
        if url:
            return clean_provider_base_url(url, label="NVIDIA base_url")
    except Exception:
        pass
    return NVIDIA_DEFAULT_BASE_URL


def _is_nvidia_model(model: str) -> bool:
    """#684: True wenn Model-ID in der Phase-1-Startliste ist. Set-basiert,
    nicht präfix-basiert, um zukünftige Provider-Kollisionen (z.B. mit
    OpenRouter, Vertex) zu vermeiden."""
    return model in NVIDIA_MODELS


def _provider_call_kwargs(model_name: str, agent_cfg) -> dict:
    """#616: Provider-spezifische litellm-kwargs (api_base, api_key) für
    kompatible Endpoints mit separatem Key (z.B. MiniMax).

    Vorrang für api_key: agent_cfg.llm.api_key_env > MINIMAX_API_KEY (env) > providers.minimax.api_key.
    Für andere Provider: leeres dict — bestehende Pfade bleiben unverändert.
    """
    kwargs: dict = {}
    if model_name.startswith("MiniMax-") or model_name.startswith("minimax/"):
        kwargs["api_base"] = _minimax_base_url()
        _akv = getattr(getattr(agent_cfg, "llm", None), "api_key_env", "") or ""
        key = os.environ.get(_akv, "") if _akv else ""
        if not key:
            key = os.environ.get("MINIMAX_API_KEY", "")
        if not key:
            try:
                cfg = _load_llm_config()
                key = (cfg.get("providers", {}).get("minimax", {}).get("api_key") or "").strip()
            except Exception:
                key = ""
        if key:
            kwargs["api_key"] = clean_provider_secret(key, label="MiniMax API-Key")
        return kwargs

    # #684: NVIDIA NIM — OpenAI-kompatibler Transport, eigener Key + Endpoint.
    if _is_nvidia_model(model_name):
        kwargs["api_base"] = _nvidia_base_url()
        _akv = getattr(getattr(agent_cfg, "llm", None), "api_key_env", "") or ""
        key = os.environ.get(_akv, "") if _akv else ""
        if not key:
            key = os.environ.get("NVIDIA_API_KEY", "")
        if not key:
            try:
                cfg = _load_llm_config()
                key = (cfg.get("providers", {}).get("nvidia", {}).get("api_key") or "").strip()
            except Exception:
                key = ""
        if key:
            kwargs["api_key"] = clean_provider_secret(key, label="NVIDIA API-Key")
    return kwargs


def _is_direct_minimax_model(model_name: str, resolved_model: str | None = None) -> bool:
    """True für MiniMax Token-Plan-Modelle.

    MiniMax nutzt zwar den Anthropic-kompatiblen Endpoint, läuft in HydraHive
    aber über LiteLLM Chat-Completions. LiteLLM erwartet dort OpenAI-Chat-
    Messages und validiert `user.content` entsprechend; Anthropic-native
    `tool_result`-Blöcke sind deshalb nur im direkten Anthropic-SDK/OAuth-Pfad
    korrekt.
    """
    return (
        model_name.startswith(("MiniMax-", "minimax/"))
        or bool(resolved_model and resolved_model.startswith("anthropic/MiniMax-"))
    )


def _resolve_model(model: str, ollama_base_url: str | None = None) -> tuple[str, str | None]:
    """
    Gibt (litellm_model, api_base) zurück.
    Provider-Prefix (z.B. anthropic/, openai/) → direkt weiterreichen.
    Claude/GPT-Modellnamen → passenden Provider-Prefix ergänzen.
    MiniMax-Modelle → Anthropic-kompatibler Transport mit MiniMax-Endpoint (#771).
    Kein Prefix, kein bekannter Cloud-Name → Ollama auf localhost.
    ollama_base_url: wenn gesetzt, wird statt localhost dieser Endpunkt genutzt (WKS-Ollama).
    """
    # #771: MiniMax vor Ollama-Fallback checken, damit "MiniMax-M2.7" nicht
    # als Ollama-Modell gemappt wird. Token-Plan-Keys laufen über den von
    # MiniMax/OpenClaw empfohlenen Anthropic-kompatiblen Endpoint.
    if model.startswith("MiniMax-"):
        return f"anthropic/{model}", _minimax_base_url()
    if model.startswith("minimax/"):
        return f"anthropic/{model[len('minimax/'):]}", _minimax_base_url()

    # #684: NVIDIA NIM — Set-basierte Erkennung der Phase-1-Modelle (bare
    # Namespace-Form wie in der NVIDIA-Doku). Vor dem Ollama-Fallback, damit
    # "meta/llama-3.3-70b-instruct" nicht als Ollama-Modell landet.
    if _is_nvidia_model(model):
        return f"openai/{model}", _nvidia_base_url()

    # Wenn kein ollama_base_url aber Modell Ollama: WKS-URL aus User-Config suchen
    if not ollama_base_url and (model.startswith("ollama/") or "/" not in model):
        try:
            # 1. Globale Ollama-Config
            from .router_llm import _load_llm_config
            cfg = _load_llm_config()
            ollama_base_url = cfg.get("providers", {}).get("ollama", {}).get("base_url")
        except Exception:
            pass
        if not ollama_base_url:
            try:
                # 2. Erste WKS-Config aus users.json (für WKS-Ollama Fallback)
                import json as _j
                users = _j.loads(settings.users_config.read_text())
                for u in users.values():
                    wks = u.get("wks", {})
                    if wks.get("ip"):
                        ollama_base_url = f"http://{wks['ip']}:{wks.get('ollama_port', 11434)}"
                        break
            except Exception:
                pass
    ollama_base = ollama_base_url or "http://localhost:11434"
    # ollama/ → ollama_chat/ damit /api/chat (mit Tool Calling) statt /api/generate genutzt wird
    if model.startswith("ollama/"):
        return f"ollama_chat/{model[len('ollama/'):]}", ollama_base
    if "/" in model:
        return model, None
    # Bekannte Cloud-Modell-Prefixe automatisch ergänzen
    if model.startswith(("claude-",)):
        return f"anthropic/{model}", None
    if model.startswith(("gpt-", "o1-", "o3-")):
        return f"openai/{model}", None
    # Kein Prefix → lokales Ollama-Modell (chat)
    return f"ollama_chat/{model}", ollama_base


# ---------------------------------------------------------------- OAuth Calls

# --------------------------------------------- #865: Shared Anthropic-SDK helpers
# Gemeinsame Logik-Bausteine für direkte Anthropic-SDK-Calls (OAuth + MiniMax).
# Bewusst KEIN to_anthropic_format-Call hier — das soll in jeder Caller-Site
# explizit sichtbar bleiben (Invariant 7b in test_architecture_invariants.py).

def _apply_anthropic_history_cache_breakpoints(
    filtered: list[dict],
    max_breakpoints: int = 3,
) -> list[dict]:
    """Setzt ephemeral cache_control auf ältere History-Messages.

    Modifiziert die Liste in-place und gibt sie zurück (für Chaining).
    Maximal ``max_breakpoints`` Messages bekommen einen Breakpoint — Anthropic
    erlaubt insgesamt 4 Breakpoints (1 System + 3 History).

    Geschont werden die letzten 4 User/Assistant-Turns — dort ist die
    dynamische Schicht, ein Breakpoint dort wäre konstant invalidiert.
    """
    cache_cutoff = max(0, len(filtered) - 4)
    count = 0
    for idx, fm in enumerate(filtered):
        if count >= max_breakpoints:
            break
        if idx < cache_cutoff and fm.get("role") in ("user", "assistant"):
            content = fm.get("content", "")
            if isinstance(content, str) and content:
                filtered[idx] = {**fm, "content": [
                    {"type": "text", "text": content, "cache_control": {"type": "ephemeral"}}
                ]}
                count += 1
            elif isinstance(content, list) and content and not content[-1].get("cache_control"):
                new_c = list(content)
                new_c[-1] = {**new_c[-1], "cache_control": {"type": "ephemeral"}}
                filtered[idx] = {**fm, "content": new_c}
                count += 1
    return filtered


def _convert_openai_tools_to_anthropic(tools: list[dict] | None) -> list[dict] | None:
    """Wandelt OpenAI-Tool-Schemas in Anthropic-Format um.

    OpenAI: ``{"function": {"name", "description", "parameters"}}``
    Anthropic: ``{"name", "description", "input_schema"}``

    Return ``None`` wenn Input leer/None — so bleibt die kwargs-Konstruktion
    bei den Callern schlank (``if anth_tools: kwargs["tools"] = anth_tools``).
    """
    if not tools:
        return None
    return [
        {
            "name":         t["function"]["name"],
            "description":  t["function"].get("description", ""),
            "input_schema": t["function"].get("parameters", {"type": "object", "properties": {}}),
        }
        for t in tools
    ]


def _track_anthropic_cache_usage(resp, model: str, agent_cfg) -> None:
    """Cache-Usage loggen + Cache-Break-Detection + Fingerprint-Update.

    Reiner Side-Effekt: schreibt in ``_CACHE_FINGERPRINTS`` und ggf. in
    ``session_metrics``. Keine Rückgabe.
    """
    if not (hasattr(resp, "usage") and resp.usage):
        return
    u = resp.usage
    cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
    cache_read  = getattr(u, "cache_read_input_tokens", 0) or 0
    input_tok   = getattr(u, "input_tokens", 0) or 0
    pct = 100 * cache_read / max(input_tok, 1)
    logger.info(
        "cache [%s] input=%d cache_write=%d cache_read=%d (≈%.0f%% gecacht)",
        model, input_tok, cache_write, cache_read, pct,
    )
    _agent_id = getattr(agent_cfg, "id", "unknown")
    _prev = _CACHE_FINGERPRINTS.get(_agent_id)
    if _prev and _prev["had_write"] and cache_read == 0 and input_tok > 1000:
        _pid = _current_project_id.get()
        logger.warning(
            "CACHE-BREAK [%s]: Vorheriger Call hatte %d cache_write, jetzt 0 cache_read (%d input). "
            "System-Prompt oder Tool-Schema hat sich geändert.",
            _agent_id, _prev["last_write"], input_tok,
        )
        if _pid:
            from .session_metrics import metrics as _m
            from .orchestrator_context import _diagnose_cache_break
            _reason = "unknown"
            if hasattr(agent_cfg, "agent_dir") and agent_cfg.agent_dir:
                _break = _diagnose_cache_break(_agent_id, agent_cfg.agent_dir, "normal")
                if _break:
                    _reason = _break
            _m.record_cache_break(_pid, f"{_reason} (write={_prev['last_write']} → read=0)")
    _CACHE_FINGERPRINTS[_agent_id] = {
        "had_write": cache_write > 0,
        "last_write": cache_write,
        "last_read": cache_read,
    }


# =============================================================================
# Pydantic Response Models — #785: Schema-Validierung externer LLM-APIs
# =============================================================================

class ToolCallInput(BaseModel):
    """Validiert ein tool_call arguments-Objekt (beliebige Schema-Shape)."""
    model_config = {"extra": "allow"}

class ToolCall(BaseModel):
    id: str
    type: str = "function"
    function: Any  # {name: str, arguments: str | dict}

class MessageContent(BaseModel):
    type: str
    text: str | None = None
    id: str | None = None
    name: str | None = None
    input: Any | None = None
    tool_use_id: str | None = None
    model_config = {"extra": "allow"}

class LLMResponseUsage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0

class LLMResponseMessage(BaseModel):
    role: str = "assistant"
    content: str | list[Any] = ""
    tool_calls: list[Any] | None = None

class LLMResponseChoice(BaseModel):
    message: LLMResponseMessage
    finish_reason: str | None = None

class LLMResponse(BaseModel):
    """Pydantic-Schema für LLM-API-Responses aller Provider.
    
    #785: Wird nach jedem API-Call aufgerufen um malformed data
    zu erkennen BEVOR tool_calls/content verarbeitet werden.
    """
    model: str | None = None
    choices: list[LLMResponseChoice] = []
    usage: LLMResponseUsage | None = None
    # Extra-Felder werden akzeptiert (provider-spezifische Felder)
    model_config = {"extra": "allow"}

def _validate_llm_response(resp_data: Any, provider: str) -> None:
    """Validiert eine LLM-Response auf strukturelle Integrität (#785).

    Prüft dass tool_calls gültige name/arguments haben BEVOR sie
    weiterverarbeitet werden. Bei Fehlern: klare RuntimeError statt
    malformed data weitergeben.

    Wird gerufen:
      - Auf dem SimpleNamespace-Ergebnis von _parse_anthropic_response_to_simplenamespace
      - Auf dem SimpleNamespace-Ergebnis von _parse_codex_response_to_simplenamespace
      - Auf rohen dicts von LiteLLM/native SDK Calls (optional, via direct SDK parse)
    """
    try:
        # SimpleNamespace (post-conversion): check choices[].message.tool_calls
        if hasattr(resp_data, "choices") and hasattr(resp_data, "model"):
            for choice in resp_data.choices:
                msg = choice.message if hasattr(choice, "message") else choice
                tc_list = getattr(msg, "tool_calls", None)
                if tc_list:
                    for tc in tc_list:
                        fn = tc.function if hasattr(tc, "function") else tc
                        name = getattr(fn, "name", None) if hasattr(fn, "name") else None
                        args = getattr(fn, "arguments", None) if hasattr(fn, "arguments") else None
                        if not isinstance(name, str) or not name:
                            raise ValueError(f"#785 [{provider}] Invalid tool_call: name={name}")
                        if args is not None and not isinstance(args, (str, dict)):
                            raise ValueError(
                                f"#785 [{provider}] Invalid tool_call.arguments "
                                f"type={type(args).__name__}"
                            )
            return

        # Raw dict: validate via Pydantic
        if isinstance(resp_data, dict):
            LLMResponse.model_validate(resp_data)

    except (ValueError, AttributeError, TypeError, ValidationError) as exc:
        logger.error(
            "#785 [%s] Response-Validierung fehlgeschlagen: %s — "
            "Response wird zurückgewiesen, kein Tool-Call wird ausgeführt.",
            provider, exc,
        )
        raise RuntimeError(f"#785 [{provider}] Malformed LLM-Response: {exc}") from exc


# =============================================================================

def _parse_anthropic_response_to_simplenamespace(resp, model: str):
    """Wandelt eine Anthropic-Message-Response in ein litellm-kompatibles
    SimpleNamespace-Objekt (``.choices[0].message.content`` / ``.tool_calls``).

    Nutzt der Rest der Codebase (orchestrator_tools, session_manager etc.)
    einheitlich für Anthropic- und litellm-Pfade.

    #785: Nach der Konvertierung wird das Ergebnis gegen LLMResponse-Schema
    validiert. Bei Validierungsfehler: klare Fehlermeldung statt malformed
    data weitergeben.
    """
    import json as _json_local
    from types import SimpleNamespace

    text = ""
    tool_calls_out = []
    for block in resp.content:
        if block.type == "text":
            text = block.text
        elif block.type == "tool_use":
            tool_calls_out.append(SimpleNamespace(
                id=block.id,
                type="function",
                function=SimpleNamespace(
                    name=block.name,
                    arguments=_json_local.dumps(block.input),
                ),
            ))

    message = SimpleNamespace(
        role="assistant",
        content=text,
        tool_calls=tool_calls_out if tool_calls_out else None,
    )
    choice = SimpleNamespace(message=message, finish_reason=resp.stop_reason)

    usage_ns = None
    if hasattr(resp, "usage") and resp.usage:
        u = resp.usage
        usage_ns = SimpleNamespace(
            input_tokens                = getattr(u, "input_tokens", 0) or 0,
            output_tokens               = getattr(u, "output_tokens", 0) or 0,
            prompt_tokens               = getattr(u, "input_tokens", 0) or 0,
            completion_tokens           = getattr(u, "output_tokens", 0) or 0,
            cache_creation_input_tokens = getattr(u, "cache_creation_input_tokens", 0) or 0,
            cache_read_input_tokens     = getattr(u, "cache_read_input_tokens", 0) or 0,
        )

    result = SimpleNamespace(choices=[choice], model=model, usage=usage_ns)

    # #785: Validate converted output before returning
    _validate_llm_response(result, provider="anthropic")
    return result


async def _anthropic_oauth_call(
    agent_cfg,
    messages:       list[dict],
    tools:          list[dict] | None,
    token:          str,
    model_override: str | None = None,
):
    """
    Direkter Anthropic SDK Call mit OAuth-Token (Claude Max Subscription).
    Setzt anthropic-beta: oauth-2025-04-20 Header wie OpenClaw.
    Gibt ein litellm-kompatibles Response-Objekt zurück.
    """
    import anthropic as _anthropic

    # #628: Message-Normalisierung VOR Format-Konvertierung
    from .message_normalization import normalize_messages_for_call
    messages = normalize_messages_for_call(messages)

    # api_key="" verhindert dass der SDK ANTHROPIC_API_KEY aus env liest
    from .provider_config import ANTHROPIC_OAUTH_HEADERS
    client = _anthropic.AsyncAnthropic(
        api_key="",
        auth_token=token,
        timeout=300.0,
        default_headers=ANTHROPIC_OAUTH_HEADERS,
    )

    # #637-Followup: gemeinsamer Helper, dedupliziert mit _stream_anthropic_oauth.
    from .message_normalization import to_anthropic_format
    system_msg, filtered = to_anthropic_format(messages)

    # Modell-Name normalisieren (openai/claude-... → claude-...)
    model = model_override or agent_cfg.llm.model
    for prefix in ("openai/", "anthropic/", "claude/"):
        if model.startswith(prefix):
            model = model[len(prefix):]
            break
    if not model.startswith("claude-"):
        model = "claude-haiku-4-5"

    # OAuth erfordert Identity-Block als erstes System-Element
    from .provider_config import get_oauth_system_blocks
    oauth_system = get_oauth_system_blocks(system_msg)

    # #865: geteilter Helper — setzt ephemeral cache_control auf bis zu
    # 3 ältere Messages (Anthropic-Limit: 1 System + 3 History = 4 Breakpoints).
    filtered = _apply_anthropic_history_cache_breakpoints(filtered, max_breakpoints=3)

    # #515: Model-spezifische max_tokens — neuere Modelle können mehr Output
    _configured_max = agent_cfg.llm.max_tokens
    if any(x in model for x in ("claude-opus-4", "claude-sonnet-4")):
        _configured_max = max(_configured_max, 16384)  # Claude 4: mindestens 16k Output
    elif "claude-3-7" in model or "claude-3-5" in model:
        _configured_max = max(_configured_max, 8192)

    # Safety: Anthropic braucht mindestens eine Message
    if not filtered:
        filtered = [{"role": "user", "content": "(leere Nachricht)"}]
        logger.warning("OAuth-Call: messages war leer — Dummy eingefügt")

    kwargs: dict = {
        "model":       model,
        "max_tokens":  _configured_max,
        "messages":    filtered,
        "temperature": agent_cfg.llm.temperature,
        "system":      oauth_system,
    }

    # Extended Thinking (#477): wenn thinking_budget > 0 und Modell es unterstützt
    _thinking_budget = getattr(agent_cfg.llm, "thinking_budget", 0) or 0
    if _thinking_budget > 0 and any(x in model for x in ("claude-3-7", "claude-sonnet-4", "claude-opus-4")):
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": _thinking_budget}
        # Anthropic: max_tokens muss > thinking budget sein
        kwargs["max_tokens"] = max(kwargs["max_tokens"], _thinking_budget + 4096)
        # Temperature muss 1 sein bei Extended Thinking
        kwargs["temperature"] = 1
        logger.info("Extended Thinking aktiviert: budget=%d model=%s", _thinking_budget, model)

    # #865: geteilter Tool-Konverter.
    _anth_tools = _convert_openai_tools_to_anthropic(tools)
    if _anth_tools:
        kwargs["tools"] = _anth_tools

    raw_resp = await _llm_with_retry(lambda: client.messages.with_raw_response.create(**kwargs))
    resp = raw_resp.parse()

    # OAuth Rate-Limit Headers auslesen → globaler State
    _extract_rate_limit_headers(raw_resp.headers)

    # #865: Cache-Break-Detection + Usage loggen via geteiltem Helper.
    _track_anthropic_cache_usage(resp, model, agent_cfg)

    # #865: Response → litellm-kompatibles SimpleNamespace via geteiltem Helper.
    return _parse_anthropic_response_to_simplenamespace(resp, model)


# ───────────────────────────────── MiniMax via direkten Anthropic-SDK (#864/#866)

def _minimax_anthropic_sdk_enabled() -> bool:
    """#864/#870: Default-ON-Switch für den MiniMax-Anthropic-SDK-Pfad.

    Nach erfolgreichem Live-Verify auf .177 (#869, 2026-04-23) ist der
    direkte Anthropic-SDK-Pfad der Standard für alle MiniMax-Modelle —
    löst die Halluzinations-Familie (#792/#856/#862) an der Wurzel.

    Opt-Out via ``HYDRAHIVE_MINIMAX_ANTHROPIC_SDK=0`` (oder
    ``false``/``no``/``off``). Nur für Debugging/Rollback — nicht
    empfohlen.
    """
    return os.environ.get("HYDRAHIVE_MINIMAX_ANTHROPIC_SDK", "1").strip().lower() not in (
        "0", "false", "no", "off"
    )


async def _minimax_anthropic_call(
    agent_cfg,
    messages:   list[dict],
    tools:      list[dict] | None,
    api_key:    str,
    model_name: str,
):
    """Direkter Anthropic-SDK-Call gegen den MiniMax-/anthropic-Endpoint.

    #864: Löst die Halluzinations-Familie (#792 `<invoke>`, #856 `[TOOL_CALL]`,
    #862 `<minimax:tool_call>`) durch native ``type=tool_use``-Blöcke. Der
    litellm-Pfad konvertiert OpenAI↔Anthropic-Wire-Formate mehrmals hin und
    her — MiniMax halluziniert dann gern Tool-Calls als Plaintext. Der
    direkte SDK-Call spricht das Anthropic-Protokoll nativ.

    Unterschiede zu :func:`_anthropic_oauth_call`:

    - ``base_url`` = :func:`_minimax_base_url` (kein Anthropic-Default)
    - ``api_key`` direkt (kein OAuth-Token)
    - ``Authorization: Bearer``-Header manuell (MiniMax-Konvention)
    - System-Prompt via :func:`get_plain_system_blocks` (kein Identity-Wrap)
    - keine Claude-spezifischen max_tokens-/Extended-Thinking-Bumps
    - keine anthropic-ratelimit-*-Headers (MiniMax setzt sie nicht)
    """
    import anthropic as _anthropic

    # #628: Message-Normalisierung vor Format-Konvertierung.
    from .message_normalization import normalize_messages_for_call
    messages = normalize_messages_for_call(messages)

    client = _anthropic.AsyncAnthropic(
        base_url=_minimax_base_url(),
        api_key=api_key,
        timeout=300.0,
        default_headers={"Authorization": f"Bearer {api_key}"},
    )

    # Invariant 7b: to_anthropic_format direkt am Caller, nicht im Helper.
    from .message_normalization import to_anthropic_format
    system_msg, filtered = to_anthropic_format(messages)

    # Modell-ID: MiniMax erwartet den nackten Namen (z.B. "MiniMax-M2.7"),
    # unser _resolve_model prefixed mit "anthropic/" — hier rückwärts strippen.
    model = model_name
    for prefix in ("anthropic/", "minimax/"):
        if model.startswith(prefix):
            model = model[len(prefix):]
            break

    from .provider_config import get_plain_system_blocks
    system_blocks = get_plain_system_blocks(system_msg)

    # #865-Helper: ephemeral cache_control auf ältere History-Messages.
    # MiniMax akzeptiert cache_control genauso wie Claude (Anthropic-Protokoll).
    filtered = _apply_anthropic_history_cache_breakpoints(filtered, max_breakpoints=3)

    # Safety: mindestens eine Message.
    if not filtered:
        filtered = [{"role": "user", "content": "(leere Nachricht)"}]
        logger.warning("MiniMax-Anthropic-Call: messages war leer — Dummy eingefügt")

    kwargs: dict = {
        "model":       model,
        "max_tokens":  agent_cfg.llm.max_tokens,
        "messages":    filtered,
        "temperature": agent_cfg.llm.temperature,
    }
    if system_blocks:
        kwargs["system"] = system_blocks

    # #865-Helper: OpenAI-Schema → Anthropic input_schema.
    _anth_tools = _convert_openai_tools_to_anthropic(tools)
    if _anth_tools:
        kwargs["tools"] = _anth_tools

    raw_resp = await _llm_with_retry(
        lambda: client.messages.with_raw_response.create(**kwargs)
    )
    resp = raw_resp.parse()

    # Rate-Limit-Header sind bei MiniMax üblicherweise leer — der Extractor
    # guardet intern (``if len(data) > 1``), also safe no-op. Kein globaler
    # State-Overwrite des OAuth-Zustands.
    _extract_rate_limit_headers(raw_resp.headers)

    _track_anthropic_cache_usage(resp, model, agent_cfg)

    return _parse_anthropic_response_to_simplenamespace(resp, model)


# ─────────────────────────────────────────────────── Codex Usage-Parser (#700)

def _parse_codex_usage(usage: dict | None):
    """Pure Parser für das `usage`-Dict aus Codex `response.completed`-Events.

    Das ChatGPT-Codex-Backend ist nicht offiziell dokumentiert. Vermutete
    Shape nach OpenAI Responses-API:

        {
          "input_tokens": 1200,
          "output_tokens": 50,
          "total_tokens": 1250,
          "input_tokens_details": {"cached_tokens": 800},
          "output_tokens_details": {"reasoning_tokens": 0}
        }

    Defensive Reihenfolge beim Cache-Read-Lookup:
    1. ``usage.input_tokens_details.cached_tokens`` (Standard-Responses-API)
    2. ``usage.cached_tokens`` (Fallback falls top-level)

    Cache-Writes reportet die Responses-API nicht explizit (Prefix-Caching ist
    automatisch). Bleibt deshalb 0.

    Liefert immer ein SimpleNamespace mit allen vier Feldern — nie None,
    nie fehlende Attribute. Bei ``usage is None`` alle Werte auf 0.
    """
    from types import SimpleNamespace

    if not usage:
        return SimpleNamespace(
            prompt_tokens=0,
            completion_tokens=0,
            cache_read_input_tokens=0,
            cache_creation_input_tokens=0,
        )

    def _int(v) -> int:
        try:
            return int(v) if v is not None else 0
        except (TypeError, ValueError):
            return 0

    cache_read = 0
    details = usage.get("input_tokens_details")
    if isinstance(details, dict):
        cache_read = _int(details.get("cached_tokens"))
    if not cache_read:
        cache_read = _int(usage.get("cached_tokens"))

    return SimpleNamespace(
        prompt_tokens=_int(usage.get("input_tokens")),
        completion_tokens=_int(usage.get("output_tokens")),
        cache_read_input_tokens=cache_read,
        cache_creation_input_tokens=0,
    )


def _accumulate_codex_usage(_usage: dict, resp_usage) -> None:
    """Akkumuliert eine Codex-Response `usage` in den Session-Counter.

    Idempotent gegen None/missing-attrs, damit Call-Sites keine Guards brauchen.
    Wird in orchestrator_stream._stream_codex() nach jeder Codex-Antwort
    (initial + Folge-Call nach Tool-Runde) gerufen.
    """
    if not resp_usage:
        return
    _usage["input"]       = _usage.get("input", 0)       + int(getattr(resp_usage, "prompt_tokens", 0) or 0)
    _usage["output"]      = _usage.get("output", 0)      + int(getattr(resp_usage, "completion_tokens", 0) or 0)
    _usage["cache_read"]  = _usage.get("cache_read", 0)  + int(getattr(resp_usage, "cache_read_input_tokens", 0) or 0)
    _usage["cache_write"] = _usage.get("cache_write", 0) + int(getattr(resp_usage, "cache_creation_input_tokens", 0) or 0)


async def _openai_codex_call(
    agent_cfg,
    messages:    list[dict],
    tools:       list[dict] | None,
    token_data:  dict,
    model_name:  str,
    force_tools: bool = True,
):
    """
    ChatGPT Plus/Pro via Codex OAuth.
    Endpoint: chatgpt.com/backend-api/codex/responses (OpenAI Responses API).
    Die Codex API erfordert stream=true — wir sammeln alle Chunks zu einem Response.
    """
    import json as _json
    import httpx as _httpx
    from types import SimpleNamespace

    model_id = model_name
    if model_id.startswith("openai-codex/"):
        model_id = model_id[len("openai-codex/"):]

    access_token = token_data["access_token"]
    account_id   = token_data["account_id"]

    def _codex_item_id(tool_call: dict) -> str:
        item_id = str(tool_call.get("item_id") or "").strip()
        if item_id.startswith("fc_"):
            return item_id
        call_id = str(tool_call.get("id") or "").strip()
        if call_id.startswith("fc_"):
            return call_id
        if call_id.startswith("call_"):
            return "fc_" + call_id[len("call_"):]
        if call_id:
            return "fc_" + call_id.replace(" ", "_")
        return "fc_unknown"

    system_prompt = ""
    input_items: list = []
    for m in messages:
        role    = m.get("role", "")
        content = m.get("content", "") or ""
        if role == "system":
            system_prompt = content
            continue
        if role == "tool":
            input_items.append({
                "type":    "function_call_output",
                "call_id": m.get("tool_call_id", ""),
                "output":  content,
            })
            continue
        tc_list = m.get("tool_calls")
        if role == "assistant" and tc_list:
            if content:
                input_items.append({
                    "role":    "assistant",
                    "content": [{"type": "output_text", "text": content}],
                })
            for tc in tc_list:
                fn = tc.get("function", {})
                input_items.append({
                    "type":      "function_call",
                    "id":        _codex_item_id(tc),
                    "call_id":   tc.get("id", ""),
                    "name":      fn.get("name", ""),
                    "arguments": fn.get("arguments", "{}"),
                })
            continue
        input_items.append({
            "role":    role,
            "content": [{"type": "input_text" if role == "user" else "output_text", "text": content}],
        })

    resp_tools = None
    if tools:
        resp_tools = [
            {
                "type":        "function",
                "name":        t["function"]["name"],
                "description": t["function"].get("description", ""),
                "parameters":  t["function"].get("parameters", {"type": "object", "properties": {}}),
                "strict":      None,
            }
            for t in tools
        ]

    payload: dict = {
        "model":               model_id,
        "input":               input_items,
        "store":               False,
        "stream":              True,
        "text":                {"verbosity": "medium"},
        "include":             ["reasoning.encrypted_content"],
        "parallel_tool_calls": True,
    }
    instructions = system_prompt
    if resp_tools:
        tool_names = ", ".join(t["name"] for t in resp_tools)
        tool_hint  = (
            f"\n\nDu hast {len(resp_tools)} Tools zur Verfügung: {tool_names}. "
            "Nutze sie aktiv und direkt — führe Befehle aus statt sie zu erklären. "
            "Frage nicht nach Erlaubnis, handle autonom."
        )
        instructions = (instructions + tool_hint) if instructions else tool_hint
    if instructions:
        payload["instructions"] = instructions
    if resp_tools:
        payload["tools"]       = resp_tools
        payload["tool_choice"] = "required" if force_tools else "auto"

    headers = {
        "Authorization":      f"Bearer {access_token}",
        "chatgpt-account-id": account_id,
        "OpenAI-Beta":        "responses=experimental",
        "originator":         "pi",
        "Content-Type":       "application/json",
    }

    text = ""
    accumulated_fn: dict[str, dict] = {}  # item_id → {id, call_id, name, arguments}
    # #700: cache_read aus usage.input_tokens_details.cached_tokens; cache_write
    # bleibt 0 (Responses-API macht implizites Prefix-Caching, kein Write-Event).
    _codex_usage: dict[str, int] = {"input": 0, "output": 0, "cache_read": 0, "cache_write": 0}

    async with _httpx.AsyncClient(timeout=120) as client:
        async with client.stream(
            "POST", "https://chatgpt.com/backend-api/codex/responses",
            headers=headers, json=payload,
        ) as resp:
            if resp.status_code != 200:
                body = await resp.aread()
                raise RuntimeError(f"Codex API {resp.status_code}: {body.decode()[:300]}")

            # Codex Rate-Limit Headers auslesen und persistieren
            _codex_rate_headers = {
                k: v for k, v in resp.headers.items()
                if k.lower().startswith("x-codex")
            }
            if _codex_rate_headers:
                try:
                    from .settings import settings as _settings
                    _rl_path = _settings.etc_dir / "codex_ratelimits.json"
                    import time as _time
                    _rl_data = {**_codex_rate_headers, "_updated_at": _time.time(), "_model": model_id}
                    _rl_path.write_text(_json.dumps(_rl_data, indent=2))
                except Exception:
                    pass

            async for line in resp.aiter_lines():
                if not line.startswith("data: "):
                    continue
                data_str = line[6:].strip()
                if not data_str or data_str == "[DONE]":
                    continue
                try:
                    ev = _json.loads(data_str)
                except Exception:
                    continue
                ev_type = ev.get("type", "")

                if ev_type == "response.output_text.delta":
                    text += ev.get("delta", "")

                elif ev_type == "response.output_item.added":
                    item = ev.get("item", {})
                    if item.get("type") == "function_call":
                        item_id  = item.get("id", "")
                        call_id  = item.get("call_id", "")
                        accumulated_fn[item_id] = {
                            "id":        item_id,
                            "call_id":   call_id,
                            "name":      item.get("name", ""),
                            "arguments": "",
                        }

                elif ev_type == "response.function_call_arguments.delta":
                    cid = ev.get("item_id", ev.get("call_id", ""))
                    if cid in accumulated_fn:
                        accumulated_fn[cid]["arguments"] += ev.get("delta", "")

                elif ev_type == "response.function_call_arguments.done":
                    cid = ev.get("item_id", ev.get("call_id", ""))
                    if cid in accumulated_fn:
                        accumulated_fn[cid]["arguments"] = ev.get("arguments", accumulated_fn[cid]["arguments"])

                elif ev_type == "response.completed":
                    # #700: Token-Usage aus Codex response.completed Event.
                    # Pure Parser + defensiver Lookup auf input_tokens_details.cached_tokens.
                    usage = ev.get("response", {}).get("usage", {}) or {}
                    if usage and os.environ.get("HYDRAHIVE_CODEX_USAGE_SNIFF") == "1":
                        # Nur Token-Zähler loggen — keine prompts, keine outputs,
                        # keine tool_calls, keine secrets. Default aus.
                        logger.info("codex_usage_sniff model=%s usage=%s", model_id, _json.dumps(usage))
                    parsed = _parse_codex_usage(usage)
                    _codex_usage["input"] = parsed.prompt_tokens
                    _codex_usage["output"] = parsed.completion_tokens
                    _codex_usage["cache_read"] = parsed.cache_read_input_tokens
                    _codex_usage["cache_write"] = parsed.cache_creation_input_tokens

    tool_calls_out = [
        SimpleNamespace(
            id=fn["call_id"],
            item_id=fn["id"],
            type="function",
            function=SimpleNamespace(
                name=fn["name"],
                arguments=fn["arguments"] or "{}",
            ),
        )
        for fn in accumulated_fn.values()
    ]

    message = SimpleNamespace(
        role="assistant",
        content=text,
        tool_calls=tool_calls_out if tool_calls_out else None,
    )
    choice = SimpleNamespace(message=message, finish_reason="stop")
    usage = SimpleNamespace(
        prompt_tokens=_codex_usage["input"],
        completion_tokens=_codex_usage["output"],
        # #700: Interface-Parität zum Anthropic-Usage — Aufrufer
        # (orchestrator_stream._stream_codex) kann dieselben Attribute lesen.
        cache_read_input_tokens=_codex_usage["cache_read"],
        cache_creation_input_tokens=_codex_usage["cache_write"],
    )
    return SimpleNamespace(choices=[choice], model=model_id, usage=usage)


# ---------------------------------------------------------------- LLM Call Chain

def check_model_access(model_name: str, agent_cfg=None) -> str | None:
    """#444: Prüft ob das Modell laut LLM-Config erlaubt ist. Gibt Fehlermeldung oder None zurück."""
    from .router_llm import _cached_json_load
    cfg = _cached_json_load(str(settings.llm_config), {"providers": {}})
    blocked = cfg.get("blocked_models", [])
    if blocked:
        model_lower = model_name.lower()
        for pattern in blocked:
            if pattern.lower() in model_lower:
                return f"Modell '{model_name}' ist blockiert (Config: blocked_models)"
    return None


async def _llm_call_single(
    model_name: str,
    agent_cfg,
    messages:   list[dict],
    tools:      list[dict] | None,
):
    """Ein einzelnes Modell aufrufen — ohne Failover-Logik."""
    # #444: Model-Gating Check
    gate_err = check_model_access(model_name, agent_cfg)
    if gate_err:
        raise Exception(gate_err)
    # OpenAI Codex (ChatGPT Plus OAuth)
    if model_name.startswith("openai-codex/"):
        codex_token = _load_openai_codex_token()
        if codex_token:
            return await _openai_codex_call(agent_cfg, messages, tools, codex_token, model_name)

    # Claude: Terminal-Token oder Console-OAuth → immer über OAuth-Pfad (für Rate-Limit Headers)
    is_claude = model_name.startswith(("claude-", "anthropic/"))
    if is_claude:
        env_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if env_key and env_key.startswith("sk-ant-oat01-"):
            # Langlebiger Terminal-Token → über OAuth-Pfad (nicht litellm) für Rate-Limit Headers
            return await _anthropic_oauth_call(agent_cfg, messages, tools, env_key, model_name)
        else:
            oauth_token = _load_claude_oauth_token()
            if oauth_token:
                return await _anthropic_oauth_call(agent_cfg, messages, tools, oauth_token, model_name)

    model, api_base = _resolve_model(model_name, agent_cfg.llm.ollama_base_url)

    # #864/#866: MiniMax via direkten Anthropic-SDK-Pfad. Feature-Flag-gated
    # (HYDRAHIVE_MINIMAX_ANTHROPIC_SDK=1), Default OFF → bestehender litellm-
    # Pfad bleibt aktiv bis Live-Verify (#869) grün ist. Greift nur wenn ein
    # MiniMax-Key resolvebar ist; sonst Fall-Through zu litellm.
    if _is_direct_minimax_model(model_name, model) and _minimax_anthropic_sdk_enabled():
        _mm_prov_kw = _provider_call_kwargs(model_name, agent_cfg)
        _mm_key = _mm_prov_kw.get("api_key", "")
        if _mm_key:
            logger.info("MiniMax-Anthropic-SDK-Pfad aktiv (non-streaming) für %s", model_name)
            return await _minimax_anthropic_call(agent_cfg, messages, tools, _mm_key, model_name)
        logger.warning(
            "MiniMax-Anthropic-SDK-Flag gesetzt aber kein Key gefunden für %s — "
            "Fall-Through zu litellm",
            model_name,
        )
    is_anthropic = model.startswith(("anthropic/", "claude-"))
    use_anthropic_wire_format = is_anthropic and not _is_direct_minimax_model(model_name, model)
    # #628: Message-Normalisierung vor Cache-Control + LLM-Call (kanonische Form)
    from .message_normalization import normalize_messages_for_call
    messages = normalize_messages_for_call(messages)
    # #P3-FIX: _apply_cache_control braucht is_anthropic=True für MiniMax
    # (MiniMax's Anthropic-Endpoint akzeptiert cache_control: ephemeral).
    # use_anthropic_wire_format=False → to_anthropic_format wird nicht
    # aufgerufen → MiniMax erhält OpenAI-Chat-Messages mit cache_control-
    # Markern, LiteLLM reicht sie durch. Siehe test_minimax_provider.py:
    # test_minimax_litellm_call_behaelt_openai_tool_messages.
    _use_cache_control = use_anthropic_wire_format or _is_direct_minimax_model(model_name, model)
    cached_messages = _apply_cache_control(messages, _use_cache_control)
    # BL-16: MiniMax via LiteLLM nutzt OpenAI-Chat-Completions und validiert
    # User-Content-Bloecke gegen OpenAI's ValidUserMessageContentTypes.
    # Anthropic-Image-Bloecke {"type":"image", "source":{...}} werden rejectet.
    # Konvertierung zu {"type":"image_url", "image_url":{"url":"data:..."}}
    # passiert NACH _apply_cache_control, damit cache_control auf Text-
    # Bloecken erhalten bleibt. Nur MiniMax-Pfad — Claude nutzt separaten
    # Anthropic-SDK-Transport (to_anthropic_format) und braucht es nicht.
    if _is_direct_minimax_model(model_name, model):
        from .message_normalization import convert_anthropic_images_to_openai
        cached_messages = convert_anthropic_images_to_openai(cached_messages)
    # #637-Followup: echte Anthropic-API-Key-Calls ohne OAuth brauchen die
    # OpenAI→Anthropic-Konvertierung vor litellm. MiniMax ist eine Ausnahme:
    # Token-Plan läuft über /anthropic, aber LiteLLM validiert OpenAI-Chat-
    # Messages und darf keine `tool_result`-Content-Blöcke sehen.
    _system_for_anthropic = ""
    if use_anthropic_wire_format:
        from .message_normalization import to_anthropic_format
        _system_for_anthropic, cached_messages = to_anthropic_format(cached_messages)

    # #515: Model-spezifische max_tokens auch im litellm-Pfad
    _lm_max = agent_cfg.llm.max_tokens
    if is_anthropic:
        if any(x in model for x in ("claude-opus-4", "claude-sonnet-4")):
            _lm_max = max(_lm_max, 16384)
        elif any(x in model for x in ("claude-3-7", "claude-3-5")):
            _lm_max = max(_lm_max, 8192)

    # Safety: mindestens eine Message
    if not cached_messages:
        cached_messages = [{"role": "user", "content": "(leere Nachricht)"}]
        logger.warning("litellm-Call: messages war leer — Dummy eingefügt")

    kwargs: dict = {
        "model":       model,
        "messages":    cached_messages,
        "temperature": agent_cfg.llm.temperature,
        "max_tokens":  _lm_max,
    }
    if is_anthropic and _system_for_anthropic:
        kwargs["system"] = _system_for_anthropic
    if api_base:
        kwargs["api_base"] = api_base

    # v2: Projekt-spezifischer API-Key über api_key_env (z.B. "OPENAI_KEY")
    # Wird zur Runtime aus dem Environment aufgelöst, nie in Config gespeichert.
    _api_key_env = getattr(agent_cfg.llm, "api_key_env", "")
    if _api_key_env:
        _resolved_key = os.environ.get(_api_key_env, "")
        if _resolved_key:
            kwargs["api_key"] = _resolved_key

    # #616: Provider-spezifische kwargs (aktuell MiniMax: OpenAI-kompatibler
    # Endpoint + eigener Key). api_key_env hat Vorrang — der Helper respektiert
    # das intern, aber wir schreiben nur Felder, die noch nicht gesetzt sind.
    _prov_kw = _provider_call_kwargs(model_name, agent_cfg)
    if "api_base" in _prov_kw:
        kwargs["api_base"] = _prov_kw["api_base"]
    if "api_key" in _prov_kw and "api_key" not in kwargs:
        kwargs["api_key"] = _prov_kw["api_key"]

    # Extended Thinking via litellm (für API-Key-basierte Calls)
    _thinking_budget = getattr(agent_cfg.llm, "thinking_budget", 0) or 0
    if _thinking_budget > 0 and is_anthropic:
        kwargs["thinking"] = {"type": "enabled", "budget_tokens": _thinking_budget}
        kwargs["max_tokens"] = max(kwargs["max_tokens"], _thinking_budget + 4096)
        kwargs["temperature"] = 1

    if tools:
        kwargs["tools"]       = tools
        kwargs["tool_choice"] = "auto"

    resp = await _llm_with_retry(lambda: litellm.acompletion(**kwargs, drop_params=True, timeout=300))

    # Cache-Usage loggen (#351: Anthropic + OpenAI Prompt Caching)
    try:
        u = getattr(resp, "usage", None)
        if u:
            # Anthropic: cache_creation_input_tokens / cache_read_input_tokens
            cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
            cache_read  = getattr(u, "cache_read_input_tokens", 0) or 0
            input_tok   = getattr(u, "prompt_tokens", 0) or 0
            # OpenAI: prompt_tokens_details.cached_tokens
            details = getattr(u, "prompt_tokens_details", None)
            if details and not cache_read:
                cache_read = getattr(details, "cached_tokens", 0) or 0
            if cache_write or cache_read:
                logger.info(
                    "cache [%s] input=%d cache_write=%d cache_read=%d (≈%.0f%% gecacht)",
                    model, input_tok, cache_write, cache_read,
                    100 * cache_read / max(input_tok, 1),
                )
    except Exception:
        pass

    return resp


async def _llm_call(
    agent_cfg,
    messages: list[dict],
    tools:    list[dict] | None,
):
    """
    LLM-Call mit Failover: versucht primary model, dann fallback_models.
    Bei Quota/Overload-Fehler wird automatisch zum nächsten Modell gewechselt.
    """
    models = [agent_cfg.llm.model] + agent_cfg.llm.fallback_models
    last_exc: Exception = RuntimeError("Kein Modell konfiguriert")
    for i, model_name in enumerate(models):
        try:
            return await _llm_call_single(model_name, agent_cfg, messages, tools)
        except Exception as e:
            last_exc = e
            if i < len(models) - 1 and _should_failover(e):
                logger.warning(
                    "Modell '%s' nicht verfügbar (%s) — Failover auf '%s'",
                    model_name, str(e)[:80], models[i + 1],
                )
                continue
            raise
    raise last_exc
