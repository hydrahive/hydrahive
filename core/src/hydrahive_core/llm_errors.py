"""
llm_errors.py — User-facing Fehlermeldungen für LLM-Call-Exceptions.

Vorher gab der Orchestrator bei jeder LLM-Exception nur das generische
"LLM nicht erreichbar — bitte später erneut versuchen." zurück. Für den
User bedeutete das: jeder neue Fehler = neue Log-Session beim Admin.

Dieses Modul klassifiziert die häufigsten litellm/httpx/anthropic-
Exceptions und liefert eine kurze, user-freundliche Meldung mit
konkretem Next-Step. Secrets (Bearer-Token, API-Keys, Cookies) werden
aus dem Exception-Text rausgefiltert, bevor er an den User geht.
"""
from __future__ import annotations

import re
from typing import Any

# Bekannte Secret-Patterns, die niemals im User-facing Text landen dürfen.
# Bewusst konservativ: alles rausnehmen was "Bearer ...", "Authorization: ...",
# "sk-...", "Api-Key: ..." sieht.
_SECRET_PATTERNS = (
    re.compile(r"(?i)bearer\s+[a-z0-9._\-+/=]+", re.IGNORECASE),
    re.compile(r"(?i)authorization\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"sk-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"nvapi-[A-Za-z0-9_\-]{10,}"),
    re.compile(r"(?i)api[_\-]?key\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"(?i)cookie\s*[:=]\s*\S+", re.IGNORECASE),
    re.compile(r"ghp_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
)

_MAX_MESSAGE_LEN = 240


def _scrub_secrets(text: str) -> str:
    """Entfernt typische Secret-Patterns aus Exception-Text."""
    for pat in _SECRET_PATTERNS:
        text = pat.sub("[REDACTED]", text)
    return text


def _short(text: str, limit: int = _MAX_MESSAGE_LEN) -> str:
    """Kürzt auf ``limit`` Zeichen; newlines zu Leerzeichen; mehrfach-Spaces weg."""
    text = " ".join(text.split())
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    return text


def format_llm_error(exc: BaseException) -> str:
    """
    Baut eine user-facing Fehlermeldung aus einer LLM-Call-Exception.

    Strategie:
      1. Exception-Typ auf bekannte Kategorien mappen (Auth / BadRequest /
         Timeout / Connection / RateLimit).
      2. Exception-Message scrubben (Secrets raus) und auf 240 Zeichen
         kürzen.
      3. Bei unbekannten Typen: Typname + gekürzte Message durchreichen
         — besser verständlicher Fehler als stilles "nicht erreichbar".

    Returns: String für Chat/Stream (beginnt mit "[Fehler] ...").
    """
    type_name = type(exc).__name__
    msg = _short(_scrub_secrets(str(exc) or ""))

    lower_type = type_name.lower()
    lower_msg = msg.lower()

    # Auth / API-Key
    if "authentication" in lower_type or "unauthorized" in lower_msg or \
       "401" in msg or "invalid api key" in lower_msg or \
       "invalid_api_key" in lower_msg:
        return (
            "[Fehler] LLM-Authentifizierung fehlgeschlagen. API-Key prüfen "
            "(Einstellungen → LLM). Detail: "
            f"{type_name}: {msg or 'unauthorized'}"
        )

    # RateLimit
    if "ratelimit" in lower_type or "rate_limit" in lower_msg or \
       "429" in msg or "too many requests" in lower_msg or \
       "quota" in lower_msg:
        return (
            "[Fehler] LLM-Rate-Limit erreicht. Kurz warten oder anderen "
            f"Provider/Modell wählen. Detail: {type_name}: {msg}"
        )

    # BadRequest / Schema / Model-not-found
    if "badrequest" in lower_type or "invalidrequest" in lower_type or \
       "400" in msg or "404" in msg or "model_not_found" in lower_msg or \
       "model not found" in lower_msg or "does not exist" in lower_msg:
        return (
            "[Fehler] LLM-Request ungültig (Modell nicht freigeschaltet oder "
            f"Schema-Fehler). Detail: {type_name}: {msg}"
        )

    # Timeout / Connection
    if "timeout" in lower_type or "timeout" in lower_msg or \
       "connection" in lower_type or "unreachable" in lower_msg or \
       "name or service not known" in lower_msg or \
       "failed to establish" in lower_msg:
        return (
            "[Fehler] LLM-Endpoint nicht erreichbar (Timeout/DNS/Netzwerk). "
            f"base_url + VPN/Firewall prüfen. Detail: {type_name}: {msg}"
        )

    # Fallback: Typ + Message — besser als stilles "nicht erreichbar"
    if msg:
        return f"[Fehler] LLM-Aufruf gescheitert: {type_name}: {msg}"
    return f"[Fehler] LLM-Aufruf gescheitert: {type_name} (keine Message)"
