"""
tool_sequence_guard.py — #820: ToolSword Sequence-Pattern-Detection

erkennt gefährliche Sequenzen aus mehreren Tool-Calls innerhalb einer Round:
- Sensitive-Read-Tool gefolgt von einem External-Write-Tool
- Jeder Call für sich ist erlaubt, die Kombo nicht.

Kernlogik:
1. Sammle alle Tool-Calls einer Round (übergeben vom Dispatcher)
2. Prüfe auf sensitive Read patterns (path-basiert)
3. Prüfe auf external Write patterns (http_request POST/PUT zu non-whitelist domain)
4. Bei Match → RiskLevel.CONFIRM (oder höhere Stufe)
5. Auch trusted-Agents müssen bei erkannten Sequenzen bestätigen.

Referenz: ToolSword (ACL'24), H-CoT (2025.02)
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

logger = __import__("logging").getLogger(__name__)

# ── Sensitive Pfade (Read = potenzielle Exfiltrationsquelle) ───────────────
_SENSITIVE_PATH_PATTERNS: list[re.Pattern] = [
    re.compile(r"(?:^|/|\\)\.ssh(?:/|\\|$)"),          # ~/.ssh/
    re.compile(r"(?:^|/|\\)\.aws(?:/|\\|$)"),          # ~/.aws/
    re.compile(r"(?:^|/|\\)\.kube(?:/|\\|$)"),         # ~/.kube/
    re.compile(r"(?:^|/|\\)\.gitconfig(?:|/|$)"),      # ~/.gitconfig
    re.compile(r"/etc/shadow"),                          # /etc/shadow
    re.compile(r"/etc/sudoers"),                         # /etc/sudoers
    re.compile(r"(?:^|[/\\])\.(?:pem|key)$", re.IGNORECASE),  # .pem, .key as extension
    re.compile(r"(?:^|/|\\)\.p12$", re.IGNORECASE),    # *.p12 (PKCS#12)
    re.compile(r"(?:^|/|\\)id_rsa(?:|/|$)"),           # id_rsa, id_ed25519 etc.
    re.compile(r"(?:^|/|\\)known_hosts(?:|/|$)"),       # ssh known_hosts
    re.compile(r"(?:^|/|\\)authorized_keys(?:|/|$)"),  # ssh authorized_keys
    re.compile(r"/etc/hydrahive/"),                      # HydraHive internals
    re.compile(r"(?:^|/|\\)\.env$", re.IGNORECASE),     # .env files
    re.compile(r"(?:^|/|\\)\.netrc(?:|/|$)"),           # .netrc (passwords)
    re.compile(r"(?:^|/|\\)cookie[s]?(?:/|\\|$)", re.IGNORECASE),  # cookies
    re.compile(r"(?:^|/|\\)\.npmrc(?:|/|$)"),           # npm credentials
    re.compile(r"(?:^|/|\\)\.pypirc(?:|/|$)"),          # PyPI credentials
]

# ── External-Write-Detection ───────────────────────────────────────────────
# Whitelisted Domains für http_request (keine Exfiltrationsgefahr)
_EXTERNAL_WRITE_WHITELIST: frozenset[str] = frozenset({
    "github.com", "api.github.com",
    "gitlab.com", "api.gitlab.com",
    "gitea.com", "codeberg.org",
    "pastebin.com", "dpaste.com",
    "0x0.st", "catbox.moe",
    "localhost", "127.0.0.1", "0.0.0.0",
    "::1",
})

# Tools die als "external write" gelten
_EXTERNAL_WRITE_TOOLS: frozenset[str] = {
    "http_request", "http_fetch", "fetch_url",
}

# Schädliche http_request patterns: POST/PUT/PATCH mit payload zu non-whitelist
_HTTP_EXFIL_PATTERNS: list[re.Pattern] = [
    # POST/PUT/PATCH mit url zu non-whitelist
    re.compile(r"\b(POST|PUT|PATCH)\b", re.IGNORECASE),
]


@dataclass
class SequenceMatch:
    """Ergebnis einer Sequenz-Prüfung."""
    detected: bool
    risk_level: Literal["allow", "confirm", "deny"]
    reason: str
    matched_tools: list[str]  # z.B. ["file_read:~/.ssh/id_rsa", "http_request:evil.com"]
    sequence_name: str | None = None  # z.B. "ToolSword:SSH-Key-Exfil"


def _is_sensitive_path(path: str) -> bool:
    """Prüft ob ein Pfad als sensitive Read gilt."""
    if not path:
        return False
    for pat in _SENSITIVE_PATH_PATTERNS:
        if pat.search(path):
            return True
    return False


def _is_external_write(tool_name: str, tool_input: dict) -> tuple[bool, str]:
    """
    Prüft ob ein Tool-Call ein External Write darstellt.
    Returns (is_external_write, reason).
    """
    if tool_name not in _EXTERNAL_WRITE_TOOLS:
        return False, ""

    url = tool_input.get("url", "") or tool_input.get("URL", "") or ""
    method = str(tool_input.get("method", "") or tool_input.get("type", "") or "").upper()

    # Nur POST/PUT/PATCH als Exfiltrationspfad
    if method not in ("POST", "PUT", "PATCH"):
        return False, ""

    # Domain extrahieren
    domain_match = re.search(
        r"(?:https?://)?([^/]+)",
        url,
        re.IGNORECASE,
    )
    if not domain_match:
        return False, ""
    domain = domain_match.group(1).lower().split(":")[0]  # strip port

    # Whitelisted Domains erlauben
    if domain in _EXTERNAL_WRITE_WHITELIST:
        return False, ""

    return True, f"external write to non-whitelist domain '{domain}'"


def _check_sequence_patterns(
    tool_calls: list[dict],
) -> SequenceMatch:
    """
    Prüft eine Liste von Tool-Calls auf kritische Sequenzen.

    Sequenz-Typen:
    1. ToolSword: sensitive Read → external Write
    2. Credential-Combine: multi-file sensitive read → external write
    3. Config-Exfil: sensitive config read → external write
    """
    if len(tool_calls) < 2:
        return SequenceMatch(
            detected=False,
            risk_level="allow",
            reason="weniger als 2 Tool-Calls",
            matched_tools=[],
        )

    sensitive_reads: list[str] = []    # tool_call dicts die sensitive lesen
    external_writes: list[dict] = []   # tool_call dicts die extern schreiben

    for tc in tool_calls:
        tool_name = tc.get("name", "")
        tool_input = tc.get("input", {})

        # Sensitive Read?
        if tool_name in ("file_read", "server_file_read", "read_system_file"):
            path = tool_input.get("path", "")
            if _is_sensitive_path(path):
                sensitive_reads.append(tool_name)

        # External Write?
        is_ext, _ = _is_external_write(tool_name, tool_input)
        if is_ext:
            external_writes.append(tc)

    # ToolSword: sensitive Read followed by external Write
    if sensitive_reads and external_writes:
        # Baue lesbare Reason
        read_tools = ", ".join(set(sensitive_reads))
        write_tools = ", ".join(set(tc["name"] for tc in external_writes))
        return SequenceMatch(
            detected=True,
            risk_level="confirm",
            reason=(
                f"ToolSword-Sequenz erkannt: {read_tools} (sensitive Read) → "
                f"{write_tools} (external Write). "
                f"Jeder Call für sich erlaubt — Kombination = potenzielle Exfiltration."
            ),
            matched_tools=[tc["name"] for tc in tool_calls],
            sequence_name="ToolSword:SensitiveRead+ExternalWrite",
        )

    # Pattern 2: Credential-Combine — mehrere sensitive Reads (>=3)
    # in einer Round, auch wenn noch kein external Write (noch nicht晚了)
    sensitive_read_paths: list[str] = []
    for tc in tool_calls:
        tool_name = tc.get("name", "")
        tool_input = tc.get("input", {})
        if tool_name in ("file_read", "server_file_read", "read_system_file"):
            path = tool_input.get("path", "")
            if _is_sensitive_path(path):
                sensitive_read_paths.append(path)

    if len(sensitive_read_paths) >= 3:
        return SequenceMatch(
            detected=True,
            risk_level="confirm",
            reason=(
                f"Credential-Combine-Warnung: {len(sensitive_read_paths)} sensitive "
                f"Files gelesen in einer Round. Werden diese in einem Folgetool "
                f"exfiltriert, entsteht eine ToolSword-Sequenz."
            ),
            matched_tools=[tc["name"] for tc in tool_calls],
            sequence_name="Credential-Combine:MultiSensitiveRead",
        )

    return SequenceMatch(
        detected=False,
        risk_level="allow",
        reason="keine kritische Sequenz erkannt",
        matched_tools=[],
    )


# ── Haupt-API ───────────────────────────────────────────────────────────────

def check_sequence_guard(
    tool_calls: list[dict],
) -> SequenceMatch:
    """
    Hauptfunktion. Wird pro Round einmal aufgerufen (nach dem Sammeln aller
    Calls, vor der Ausführung).

    Args:
        tool_calls: Liste von dicts mit {"name": str, "input": dict}
                   (wie von execute_tool_call erwartet)

    Returns:
        SequenceMatch mit detected=True wenn eine kritische Sequenz erkannt wurde.
    """
    # Defensive: bei leerem Input keine Blockade
    if not tool_calls:
        return SequenceMatch(
            detected=False,
            risk_level="allow",
            reason="keine Tool-Calls",
            matched_tools=[],
        )

    try:
        return _check_sequence_patterns(tool_calls)
    except Exception as exc:
        logger.error("Sequence guard error: %s — safe fallback: allow", exc)
        return SequenceMatch(
            detected=False,
            risk_level="allow",
            reason=f"sequence-guard error: {exc}",
            matched_tools=[],
        )
