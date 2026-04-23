"""
tool_sequence_guard.py — #820: ToolSword Sequence-Pattern-Detection

Erkennt gefährliche Sequenzen aus mehreren Tool-Calls innerhalb einer Round:
- Sensitive-Read-Tool gefolgt von einem External-Write-Tool
- Config-Exfiltration gefolgt von External-Write
- Shell-Command followed by File-Write / Network call

Sequence-Match ist ein Simple-Namespace-artiges Objekt mit:
  .detected       bool — True wenn eine gefährliche Sequenz erkannt wurde
  .sequence_name  str  — z.B. "read-then-write", "config-exfil", "shell-then-write"
  .reason         str  — Mensch-lesbare Erklärung der erkannten Gefahr
  .tool_list      list — Die erkannte Sequenz als Tool-Dicts
"""

from types import SimpleNamespace
from typing import Any

# Sensible Lesetools — extrahieren potentiell vertrauliche Daten
_READ_TOOLS = frozenset({
    "file_search", "file_read", "read_memory", "server_file_read",
})

# Externe Schreibtools — können Daten exfiltrieren oder System verändern
_WRITE_TOOLS = frozenset({
    "http_request", "server_shell", "shell_exec",  # Network/Shell = pot. Exfil
})

# Config-Exfiltration: Admin-Tools die Credential-Dumps erzeugen
_CONFIG_READ_TOOLS = frozenset({
    "read_memory", "server_file_read", "file_read",
})

_CONFIG_WRITE_TOOLS = frozenset({
    "server_shell", "shell_exec", "http_request",
})


def check_sequence_guard(tool_calls: list[dict[str, Any]]) -> SimpleNamespace:
    """
    Prüft eine Liste von Tool-Calls (Format: [{"name": str, "input": dict}, ...])
    auf gefährliche Sequenzen.

    Rückgabe: SimpleNamespace mit
        .detected      bool
        .sequence_name str
        .reason        str
        .tool_list     list[dict]   — die erkannte Sequenz
    """
    names = [tc.get("name", "") for tc in tool_calls]

    # --- Pattern 1: Sensitive-Read → External-Write ---
    for i, call in enumerate(tool_calls):
        if call.get("name") in _READ_TOOLS:
            remaining = tool_calls[i + 1:]
            for next_call in remaining:
                nxt = next_call.get("name", "")
                if nxt in _WRITE_TOOLS:
                    return SimpleNamespace(
                        detected=True,
                        sequence_name="sensitive-read-then-external-write",
                        reason=(
                            f"'{call['name']}' gefolgt von '{nxt}' "
                            "(potentielle Daten-Exfiltration)"
                        ),
                        tool_list=[call, next_call],
                    )
            # Nur der erste Read pro Sequenz zählt (nicht jedes Read)
            break

    # --- Pattern 2: Config-Tools (memory/files) → Shell/Network ---
    for i, call in enumerate(tool_calls):
        if call.get("name") in _CONFIG_READ_TOOLS:
            for next_call in tool_calls[i + 1:]:
                nxt = next_call.get("name", "")
                if nxt in _CONFIG_WRITE_TOOLS:
                    return SimpleNamespace(
                        detected=True,
                        sequence_name="config-read-then-shell-or-network",
                        reason=(
                            f"'{call['name']}' (pot. Credentials/Config) gefolgt von "
                            f"'{nxt}' (Shell/Network = Exfiltrationsweg)"
                        ),
                        tool_list=[call, next_call],
                    )
            break

    # --- Pattern 3: Shell → FileWrite / HTTP (Lateral Movement) ---
    shell_idx = None
    for i, call in enumerate(tool_calls):
        if call.get("name") in ("shell_exec", "server_shell"):
            shell_idx = i
            break

    if shell_idx is not None:
        for next_call in tool_calls[shell_idx + 1:]:
            nxt = next_call.get("name", "")
            if nxt in ("file_write", "server_file_write", "http_request"):
                return SimpleNamespace(
                    detected=True,
                    sequence_name="shell-then-write-or-network",
                    reason=(
                        f"'{tool_calls[shell_idx]['name']}' gefolgt von "
                        f"'{nxt}' (Lateral Movement / Data Exfiltration)"
                    ),
                    tool_list=[tool_calls[shell_idx], next_call],
                )

    return SimpleNamespace(
        detected=False,
        sequence_name="",
        reason="",
        tool_list=[],
    )