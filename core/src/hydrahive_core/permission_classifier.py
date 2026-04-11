"""
permission_classifier.py — Auto-Mode Permission Classifier (#466)

LLM-basierte + regelbasierte Risikobewertung von Tool-Aktionen.
Drei Stufen: allow / confirm / deny.

- Statische Regeln (schnell, kein LLM-Call) für bekannte Patterns
- LLM-Classifier als Fallback für unbekannte Aktionen
- Result-Cache um wiederholte LLM-Calls zu vermeiden
"""
from __future__ import annotations

import logging
import re
import time
from enum import Enum

logger = logging.getLogger(__name__)


class RiskLevel(str, Enum):
    ALLOW = "allow"       # Sicher, automatisch erlauben
    CONFIRM = "confirm"   # Riskant, User-Bestätigung empfohlen
    DENY = "deny"         # Gefährlich, blockieren


# ── Statische Regeln (kein LLM-Call nötig) ────────────────────────────────────

# Tools die immer sicher sind (read-only, keine Seiteneffekte)
_ALWAYS_ALLOW = {
    "file_read", "file_search", "read_memory",
    "web_search",
    "get_final_message",
}

# Tools die immer Bestätigung brauchen (destructive)
_ALWAYS_CONFIRM = {
    "git_push", "git_reset", "send_mail",
}

# Shell-Befehle die immer blockiert werden
_SHELL_DENY_PATTERNS = [
    r'\brm\s+-rf\s+/',         # rm -rf /
    r'\bmkfs\b',               # Dateisystem formatieren
    r'\bdd\s+.*of=/dev/',      # Block-Device überschreiben
    r'>\s*/dev/sd[a-z]',       # Direkt auf Disk schreiben
    r'\bshutdown\b',           # System herunterfahren
    r'\breboot\b',             # System neustarten
    r'\binit\s+0\b',           # Runlevel 0
]

# Shell-Befehle die Bestätigung brauchen
_SHELL_CONFIRM_PATTERNS = [
    r'\brm\s+-r',              # Rekursives Löschen
    r'\bchmod\s+777',          # Unsichere Permissions
    r'\bcurl\s+.*\|\s*bash',   # Remote-Execution
    r'\bwget\s+.*\|\s*bash',   # Remote-Execution
    r'pip\s+install',          # Package-Installation
    r'npm\s+install\s+-g',     # Globale NPM-Installation
    r'\bapt\s+(install|remove|purge)',  # System-Packages
    r'\bsudo\b',               # Privilegierte Befehle
    r'\bgit\s+push\b',        # Code pushen
    r'\bgit\s+force',         # Force-Push
]

# File-Pfade die Bestätigung brauchen
_SENSITIVE_PATHS = [
    r'/etc/',                  # System-Konfiguration
    r'/root/',                 # Root-Home
    r'\.env',                  # Environment-Secrets
    r'\.ssh/',                 # SSH-Keys
    r'credentials',            # Credentials
    r'\.git/config',           # Git-Konfiguration
]


def classify_static(tool_name: str, tool_input: dict) -> RiskLevel | None:
    """
    Statische Risikobewertung basierend auf Tool-Name und Argumenten.
    Returns None wenn keine Regel greift (→ LLM-Classifier oder Default).
    """
    # Immer erlaubt
    if tool_name in _ALWAYS_ALLOW:
        return RiskLevel.ALLOW

    # Immer Bestätigung
    if tool_name in _ALWAYS_CONFIRM:
        return RiskLevel.CONFIRM

    # Shell-Befehle prüfen
    if tool_name in ("shell_exec", "project_shell", "server_shell", "wks_shell_exec"):
        cmd = tool_input.get("command", "")
        for pattern in _SHELL_DENY_PATTERNS:
            if re.search(pattern, cmd, re.IGNORECASE):
                logger.warning("Permission DENY: %s → %s", tool_name, cmd[:80])
                return RiskLevel.DENY
        for pattern in _SHELL_CONFIRM_PATTERNS:
            if re.search(pattern, cmd, re.IGNORECASE):
                return RiskLevel.CONFIRM
        return None  # Unbekannter Shell-Befehl → LLM oder Default

    # File-Write auf sensitive Pfade
    if tool_name in ("file_write", "file_patch", "write_system_file", "server_file_write"):
        path = tool_input.get("path", "")
        for pattern in _SENSITIVE_PATHS:
            if re.search(pattern, path, re.IGNORECASE):
                return RiskLevel.CONFIRM
        return None

    # Memory-Writes sind OK
    if tool_name in ("write_memory", "shared_memory_write", "user_memory_write"):
        return RiskLevel.ALLOW

    return None  # Unbekanntes Tool → Default


# ── LLM-Classifier (Fallback für unbekannte Aktionen) ────────────────────────

_CLASSIFIER_CACHE: dict[str, tuple[RiskLevel, float]] = {}  # key → (level, timestamp)
_CACHE_TTL = 300  # 5 Minuten Cache

_CLASSIFIER_PROMPT = """Du bist ein Sicherheits-Classifier für ein Multi-Agent-System.
Bewerte die folgende Tool-Aktion auf einer Risiko-Skala:

- "allow": Sicher, keine Seiteneffekte oder nur lesend
- "confirm": Potenziell riskant, User sollte bestätigen (Dateien ändern, Netzwerk, Packages)
- "deny": Gefährlich, sollte blockiert werden (Daten löschen, System beschädigen, Secrets leaken)

Antworte NUR mit einem Wort: allow, confirm oder deny."""


async def classify_llm(tool_name: str, tool_input: dict) -> RiskLevel:
    """LLM-basierte Klassifizierung. Cached für 5 Minuten."""
    import json
    cache_key = f"{tool_name}:{json.dumps(tool_input, sort_keys=True)[:200]}"

    # Cache prüfen
    cached = _CLASSIFIER_CACHE.get(cache_key)
    if cached and time.time() - cached[1] < _CACHE_TTL:
        return cached[0]

    try:
        import litellm
        resp = await litellm.acompletion(
            model="claude-haiku-4-5-20251001",
            messages=[
                {"role": "system", "content": _CLASSIFIER_PROMPT},
                {"role": "user", "content": f"Tool: {tool_name}\nArgumente: {json.dumps(tool_input)[:500]}"},
            ],
            max_tokens=5,
            temperature=0,
            drop_params=True,
        )
        answer = (resp.choices[0].message.content or "").strip().lower()
        if answer in ("allow", "confirm", "deny"):
            level = RiskLevel(answer)
        else:
            level = RiskLevel.CONFIRM  # Bei Unsicherheit: bestätigen
        _CLASSIFIER_CACHE[cache_key] = (level, time.time())
        logger.debug("LLM-Classifier: %s(%s) → %s", tool_name, str(tool_input)[:60], level.value)
        return level
    except Exception as e:
        logger.warning("LLM-Classifier failed: %s — Fallback auf CONFIRM", e)
        return RiskLevel.CONFIRM


# ── Haupt-API ─────────────────────────────────────────────────────────────────

async def classify_action(
    tool_name: str,
    tool_input: dict,
    use_llm: bool = False,
) -> RiskLevel:
    """
    Klassifiziert eine Tool-Aktion.

    Args:
        tool_name: Name des Tools
        tool_input: Argumente des Tool-Calls
        use_llm: Wenn True, wird bei unbekannten Aktionen ein LLM-Call gemacht

    Returns:
        RiskLevel (allow/confirm/deny)
    """
    # 1. Statische Regeln (schnell)
    static = classify_static(tool_name, tool_input)
    if static is not None:
        return static

    # 2. LLM-Classifier (wenn aktiviert)
    if use_llm:
        return await classify_llm(tool_name, tool_input)

    # 3. Default: file_write/shell = confirm, Rest = allow
    if tool_name in ("file_write", "file_patch", "shell_exec", "project_shell"):
        return RiskLevel.CONFIRM
    return RiskLevel.ALLOW
