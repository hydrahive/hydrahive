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
    Safety-Cap für Tool-Ergebnisse bei Speicherung in der Session (#352).

    Eigentliche Kürzung passiert erst in session_manager.llm_context() beim
    Zusammenbauen der History — dort wird nach Alter differenziert (letzte 5
    Tool-Results: 4k chars, ältere: 300 chars Preview).

    Hier nur ein großzügiger Safety-Cap (32k) gegen Out-of-Memory bei extrem
    großen Tool-Outputs (z.B. `find /` oder riesige Repo-Trees).
    """
    limit = 32000
    if len(result_str) > limit:
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
    # Only pass _agent_permissions and _execution_mode if the tool's execute method accepts **kwargs
    import inspect
    sig = inspect.signature(tool.execute)
    has_kwargs = any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())
    extra = {}
    if has_kwargs:
        extra["_agent_permissions"] = agent_permissions
        extra["_execution_mode"] = execution_mode or boss_cfg.effective_execution_mode(execution_mode)
    return await tool.execute(
        agent_id=boss_cfg.id,
        project_id=effective_pid,
        **extra,
        **args,
    )
