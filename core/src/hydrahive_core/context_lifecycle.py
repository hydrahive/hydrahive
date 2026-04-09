"""
context_lifecycle.py — Tool-Result-Budgeting (#516)

Tool-Typ-aware Budgeting: Mutations länger behalten, Reads aggressiv kürzen.
Wird in session_manager.llm_context() genutzt für differenziertes Pruning.
"""
from __future__ import annotations

import logging
from enum import Enum

logger = logging.getLogger(__name__)


class ToolOpType(str, Enum):
    """Klassifikation von Tools nach Seiteneffekt-Typ."""
    MUTATION = "mutation"    # file_write, shell_exec, git_commit — Ergebnis lange behalten
    READ = "read"            # file_read, list_directory — aggressiv kürzen
    SEARCH = "search"        # web_search, git_grep, git_diff — mittel kürzen
    META = "meta"            # request_tools, dispatch_task — sofort kürzen


# Tool-Name → ToolOpType Mapping
# Tools die hier nicht stehen: Default = READ (konservativ kürzen)
_TOOL_OP_TYPES: dict[str, ToolOpType] = {
    # Mutations — Ergebnis ist wichtig für Kontext (was wurde geändert?)
    "file_write":           ToolOpType.MUTATION,
    "file_patch":           ToolOpType.MUTATION,
    "file_undo":            ToolOpType.MUTATION,
    "write_system_file":    ToolOpType.MUTATION,
    "shell_exec":           ToolOpType.MUTATION,
    "project_shell":        ToolOpType.MUTATION,
    "server_shell":         ToolOpType.MUTATION,
    "wks_shell_exec":       ToolOpType.MUTATION,
    "git_commit":           ToolOpType.MUTATION,
    "git_push":             ToolOpType.MUTATION,
    "git_reset":            ToolOpType.MUTATION,
    "git_checkout":         ToolOpType.MUTATION,
    "write_memory":         ToolOpType.MUTATION,
    "shared_memory_write":  ToolOpType.MUTATION,
    "user_memory_write":    ToolOpType.MUTATION,
    "server_file_write":    ToolOpType.MUTATION,
    "server_file_patch":    ToolOpType.MUTATION,
    "send_mail":            ToolOpType.MUTATION,

    # Reads — Inhalt kann aggressiv gekürzt werden (Agent hat ihn schon verarbeitet)
    "file_read":            ToolOpType.READ,
    "list_directory":       ToolOpType.READ,
    "read_system_file":     ToolOpType.READ,
    "read_memory":          ToolOpType.READ,
    "shared_memory_read":   ToolOpType.READ,
    "user_memory_read":     ToolOpType.READ,
    "server_file_read":     ToolOpType.READ,
    "server_file_list":     ToolOpType.READ,
    "receive_mail":         ToolOpType.READ,
    "analyze_image":        ToolOpType.READ,
    "git_log":              ToolOpType.READ,

    # Search — Ergebnis mittelfristig relevant
    "web_search":           ToolOpType.SEARCH,
    "http_request":         ToolOpType.SEARCH,
    "git_diff":             ToolOpType.SEARCH,
    "git_status":           ToolOpType.SEARCH,
    "git_grep":             ToolOpType.SEARCH,

    # Meta — minimaler Informationsgehalt nach Verarbeitung
    "request_tools":        ToolOpType.META,
    "dispatch_task":        ToolOpType.META,
    "get_final_message":    ToolOpType.META,
}


def get_tool_op_type(tool_name: str) -> ToolOpType:
    """Gibt den ToolOpType für einen Tool-Namen zurück. Default: READ."""
    return _TOOL_OP_TYPES.get(tool_name, ToolOpType.READ)


# Budget-Konfiguration pro ToolOpType
# max_chars: Maximale Zeichenzahl für Tool-Result im Context
# keep_full_count: Letzte N Results dieses Typs bleiben ungekürzt
# aged_chars: Zeichenlimit wenn Result älter als age_threshold_min ist
# age_threshold_min: Ab wann time-decay greift (Minuten)
_BUDGETS: dict[ToolOpType, dict] = {
    ToolOpType.MUTATION: {
        "max_chars": 8000,       # Mutations brauchen mehr Kontext
        "keep_full_count": 8,    # Letzte 8 Mutation-Results voll behalten
        "aged_chars": 2000,      # Nach Alter immer noch 2k behalten
        "age_threshold_min": 60, # Erst nach 60min kürzen
    },
    ToolOpType.READ: {
        "max_chars": 3000,       # Reads schnell kürzen
        "keep_full_count": 3,    # Nur letzte 3 voll
        "aged_chars": 150,       # Alter Read → 150 Chars
        "age_threshold_min": 15, # Nach 15min schon kürzen
    },
    ToolOpType.SEARCH: {
        "max_chars": 4000,       # Suchergebnisse mittel
        "keep_full_count": 4,    # Letzte 4 voll
        "aged_chars": 500,       # Alter Search → 500 Chars
        "age_threshold_min": 30, # Nach 30min kürzen
    },
    ToolOpType.META: {
        "max_chars": 500,        # Meta-Tools minimal
        "keep_full_count": 1,    # Nur letztes voll
        "aged_chars": 100,       # Alter Meta → 100 Chars
        "age_threshold_min": 5,  # Nach 5min schon kürzen
    },
}


def get_budget(tool_name: str) -> dict:
    """Budget-Config für einen Tool-Namen."""
    return _BUDGETS[get_tool_op_type(tool_name)]


# ── #498: Selektive microCompact — strukturierte Summaries statt Head-Truncation ──

def _micro_compact(content: str, tool_name: str) -> str:
    """
    Erzeugt eine kompakte, strukturierte Summary eines Tool-Results.
    Kein LLM-Call — rein pattern-basiert, schnell.
    """
    lines = content.split("\n")
    total_lines = len(lines)
    total_chars = len(content)

    if tool_name in ("shell_exec", "project_shell", "server_shell", "wks_shell_exec"):
        # Shell-Output: Exit-Code extrahieren, erste + letzte Zeilen behalten
        # Typisch: JSON-Result mit "exit_code", "stdout", "stderr"
        exit_code = ""
        if '"exit_code":' in content[:200]:
            import re
            m = re.search(r'"exit_code"\s*:\s*(\d+)', content[:200])
            if m:
                exit_code = f" (exit {m.group(1)})"
        first_lines = "\n".join(lines[:3])
        last_lines = "\n".join(lines[-2:]) if total_lines > 5 else ""
        return (
            f"[shell{exit_code}, {total_lines} Zeilen, {total_chars} Zeichen]\n"
            f"{first_lines}\n"
            + (f"…\n{last_lines}" if last_lines else "")
        )

    if tool_name in ("file_read", "read_system_file", "server_file_read"):
        # File-Content: Pfad extrahieren, Zeilen/Größe angeben
        path_info = ""
        if '"path":' in content[:300]:
            import re
            m = re.search(r'"path"\s*:\s*"([^"]+)"', content[:300])
            if m:
                path_info = f" {m.group(1)}"
        return f"[file_read{path_info}, {total_lines} Zeilen, {total_chars} Zeichen — Inhalt verarbeitet]"

    if tool_name in ("web_search", "http_request"):
        # Such-Ergebnisse: Erste Zeile + Anzahl Results
        first = lines[0][:120] if lines else ""
        return f"[{tool_name}, {total_lines} Zeilen]\n{first}\n…[{total_chars} Zeichen verarbeitet]"

    if tool_name in ("git_diff", "git_log", "git_grep"):
        # Git-Output: Stats behalten
        stat_lines = [l for l in lines[-5:] if "file" in l.lower() or "insertion" in l.lower() or "deletion" in l.lower()]
        first = lines[0][:100] if lines else ""
        stats = "\n".join(stat_lines) if stat_lines else ""
        return (
            f"[{tool_name}, {total_lines} Zeilen]\n{first}\n"
            + (f"…\n{stats}" if stats else f"…[{total_chars} Zeichen]")
        )

    # Default: Head-Truncation mit Info
    return f"[{tool_name}, {total_chars} Zeichen]\n" + "\n".join(lines[:3]) + "\n…"


def budget_tool_result(
    content: str,
    tool_name: str,
    position_from_end: int,
    age_minutes: float = 0,
) -> str:
    """
    Kürzt ein Tool-Result basierend auf Tool-Typ, Position und Alter.

    Args:
        content: Originaler Tool-Result-String
        tool_name: Name des Tools das das Result produziert hat
        position_from_end: 0 = neuestes, 1 = zweitneustes, etc.
        age_minutes: Alter der Message in Minuten

    Returns:
        Gekürzter (oder unveränderter) String
    """
    if not content or len(content) < 100:
        return content

    budget = get_budget(tool_name)
    op_type = get_tool_op_type(tool_name)

    # Innerhalb keep_full_count: nur max_chars Cap anwenden
    if position_from_end < budget["keep_full_count"]:
        if len(content) > budget["max_chars"]:
            return (
                content[:budget["max_chars"]]
                + f"\n…[{op_type.value}-budgeted: {len(content)} → {budget['max_chars']} Zeichen]"
            )
        return content

    # #498: Time-Decay mit microCompact — strukturierte Summary statt Head-Truncation
    if age_minutes > budget["age_threshold_min"]:
        limit = budget["aged_chars"]
        if len(content) > limit:
            # Für READ/SEARCH: microCompact liefert bessere Summaries als Head-Truncation
            if op_type in (ToolOpType.READ, ToolOpType.SEARCH):
                return _micro_compact(content, tool_name)
            return (
                content[:limit]
                + f"\n…[{op_type.value}-aged: {len(content)} Zeichen, {int(age_minutes)}min alt]"
            )
        return content

    # #498: Außerhalb keep_full_count — microCompact für große Results
    half_budget = budget["max_chars"] // 2
    if len(content) > half_budget:
        if op_type in (ToolOpType.READ, ToolOpType.SEARCH) and len(content) > budget["max_chars"]:
            # Sehr große READ/SEARCH Results: microCompact statt halbes Budget
            return _micro_compact(content, tool_name)
        return (
            content[:half_budget]
            + f"\n…[{op_type.value}-compacted: {len(content)} → {half_budget} Zeichen]"
        )
    return content
