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

    # Time-Decay: alte Results aggressiver kürzen
    if age_minutes > budget["age_threshold_min"]:
        limit = budget["aged_chars"]
        if len(content) > limit:
            return (
                content[:limit]
                + f"\n…[{op_type.value}-aged: {len(content)} Zeichen, {int(age_minutes)}min alt]"
            )
        return content

    # Außerhalb keep_full_count aber noch nicht alt: halbes Budget
    half_budget = budget["max_chars"] // 2
    if len(content) > half_budget:
        return (
            content[:half_budget]
            + f"\n…[{op_type.value}-compacted: {len(content)} → {half_budget} Zeichen]"
        )
    return content
