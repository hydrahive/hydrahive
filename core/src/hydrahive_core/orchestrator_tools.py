"""
orchestrator_tools.py — Tool-Dispatch-Utilities

Standalone-Hilfsfunktionen für Tool-Ausführung:
- DispatchResult: Ergebnis eines Worker-Dispatch
- _truncate_tool_result: Tool-Output kürzen (typ-abhängig)
- _tool_call_signature: Fingerprint eines Tool-Call-Sets (Loop-Erkennung)
- _execute_tool: Einzelnes Tool ausführen
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class DispatchResult:
    worker_id: str
    task:      str
    result:    str
    success:   bool = True
    error:     str | None = None


def _truncate_tool_result(result_str: str) -> str:
    """
    Kürzt Tool-Ergebnisse typ-abhängig — spart Input-Tokens ohne nützliche Struktur zu verlieren.
    Diffs/Patches bekommen mehr Platz als reine Logs oder JSON-Blobs.
    """
    low = result_str.lstrip()
    # Diffs/Patches: Zeilenstruktur ist wichtig → großzügiger
    if low.startswith(("diff --git", "---", "@@")) or "\n@@" in result_str[:200]:
        limit = 6000
    # Repo-Tree (typischerweise JSON-Array mit Pfaden)
    elif low.startswith("[") and '"path"' in result_str[:300]:
        limit = 3000
    # JSON-Blobs und Log-Ausgaben → knapper
    else:
        limit = 4000
    if len(result_str) > limit:
        return result_str[:limit] + f"\n...[gekürzt bei {limit} Zeichen]"
    return result_str


def _tool_call_signature(tool_calls: list) -> tuple[str, ...]:
    """Fingerprint eines Tool-Call-Sets für Endlosschleifen-Erkennung."""
    signature: list[str] = []
    for tc in tool_calls:
        fn   = getattr(tc, "function", None)
        name = getattr(fn, "name", "") or ""
        args = getattr(fn, "arguments", "") or ""
        signature.append(f"{name}:{args}")
    return tuple(signature)


async def _execute_tool(
    tool,
    *,
    boss_cfg,
    project_id: str,
    tool_name:  str,
    tool_input: dict | None = None,
):
    """Führt ein einzelnes Tool aus. Gibt Fehler-Dict zurück wenn tool=None."""
    args = dict(tool_input or {})
    effective_pid = args.pop("project_id", None) or project_id
    if tool is None:
        return {"error": f"Tool '{tool_name}' ist in diesem Modus nicht erlaubt"}
    return await tool.execute(
        agent_id=boss_cfg.id,
        project_id=effective_pid,
        **args,
    )
