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
    Große Repos können 100k+ Zeichen pro Tool-Call produzieren → hier deckeln.
    """
    low = result_str.lstrip()
    # Diffs/Patches: Zeilenstruktur ist wichtig → großzügiger
    if low.startswith(("diff --git", "---", "@@")) or "\n@@" in result_str[:200]:
        limit = 12000
    # Repo-Tree / Verzeichnislisten (JSON-Array mit Pfaden)
    elif low.startswith("[") and '"path"' in result_str[:300]:
        limit = 8000
    # Datei-Inhalt (beginnt oft mit Zeilentext, Code etc.)
    elif len(result_str) > 10000:
        limit = 10000
    # JSON-Blobs und Log-Ausgaben
    else:
        limit = 8000
    if len(result_str) > limit:
        # Zeige Anfang + Ende für besseren Kontext
        head = limit * 3 // 4
        tail = limit // 4
        return (
            result_str[:head]
            + f"\n\n...[gekürzt: {len(result_str)} Zeichen total, zeige Anfang + Ende]\n\n"
            + result_str[-tail:]
        )
    return result_str


def _tool_call_signature(tool_calls: list) -> tuple[str, ...]:
    """Fingerprint eines Tool-Call-Sets für Endlosschleifen-Erkennung.

    file_write wird komplett ausgeschlossen: chunked-writes (overwrite + mehrfach append)
    zum selben Pfad sind legitim und kein Loop. max_rounds fängt echte Endlosschleifen ab.
    shell_exec: nur name, keine args (Output-abhängige Folgebefehle sind kein Loop).
    """
    import json as _j
    # Tools die vom Loop-Fingerprint ausgeschlossen werden (max_rounds schützt trotzdem)
    _LOOP_EXCLUDE = {"file_write"}
    signature: list[str] = []
    for tc in tool_calls:
        fn      = getattr(tc, "function", None)
        name    = getattr(fn, "name", "") or ""
        if name in _LOOP_EXCLUDE:
            continue
        args_raw = getattr(fn, "arguments", "") or ""
        signature.append(f"{name}:{args_raw}")
    return tuple(signature)


async def _execute_tool(
    tool,
    *,
    boss_cfg,
    project_id: str,
    tool_name:  str,
    tool_input: dict | None = None,
    execution_mode: str | None = None,
):
    """Führt ein einzelnes Tool aus. Gibt Fehler-Dict zurück wenn tool=None."""
    args = dict(tool_input or {})
    effective_pid = args.pop("project_id", None) or project_id
    if tool is None:
        return {"error": f"Tool '{tool_name}' ist in diesem Modus nicht erlaubt"}
    agent_permissions = list(boss_cfg.effective_permissions(execution_mode) or [])
    # Only pass _agent_permissions if the tool's execute method accepts **kwargs
    import inspect
    sig = inspect.signature(tool.execute)
    has_kwargs = any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())
    extra = {"_agent_permissions": agent_permissions} if has_kwargs else {}
    return await tool.execute(
        agent_id=boss_cfg.id,
        project_id=effective_pid,
        **extra,
        **args,
    )
