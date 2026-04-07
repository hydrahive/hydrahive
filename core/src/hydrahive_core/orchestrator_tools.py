"""
orchestrator_tools.py — Tool-Dispatch-Utilities

Standalone-Hilfsfunktionen für Tool-Ausführung:
- DispatchResult: Ergebnis eines Worker-Dispatch
- _truncate_tool_result: Tool-Output kürzen (typ-abhängig)
- _tool_call_signature: Fingerprint eines Tool-Call-Sets (Loop-Erkennung)
- _execute_tool: Einzelnes Tool ausführen
- Shared helpers: format_tool_detail, execute_tool_call, handle_request_tools
"""
from __future__ import annotations

import json as _json
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
    task_id:   str | None = None  # #415: DAG Task Scheduler


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

    # #364: Tool-Result Cache für idempotente Read-Tools
    cache_result = _tool_cache_get(tool_name, args)
    if cache_result is not None:
        logger.debug("Tool cache hit: %s", tool_name)
        return cache_result

    agent_permissions = list(boss_cfg.effective_permissions(execution_mode) or [])
    # Only pass _agent_permissions and _execution_mode if the tool's execute method accepts **kwargs
    import inspect
    sig = inspect.signature(tool.execute)
    has_kwargs = any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())
    extra = {}
    if has_kwargs:
        extra["_agent_permissions"] = agent_permissions
        extra["_execution_mode"] = execution_mode or boss_cfg.effective_execution_mode(execution_mode)
    result = await tool.execute(
        agent_id=boss_cfg.id,
        project_id=effective_pid,
        **extra,
        **args,
    )

    # Cache nur idempotente Read-Tools
    _tool_cache_put(tool_name, args, result)
    return result


# ---- Tool-Result Cache (#364) ----
import time as _time
import hashlib as _hashlib

_CACHEABLE_TOOLS = frozenset({"file_read", "read_system_file", "list_directory", "git_status", "git_diff"})
_tool_result_cache: dict[str, tuple[float, dict]] = {}
_TOOL_CACHE_TTL = 30  # 30 Sekunden — kurz genug dass Änderungen sichtbar werden
_TOOL_CACHE_MAX = 200


def _tool_cache_key(tool_name: str, args: dict) -> str:
    import json
    raw = f"{tool_name}:{json.dumps(args, sort_keys=True)}"
    return _hashlib.md5(raw.encode()).hexdigest()[:16]


def _tool_cache_get(tool_name: str, args: dict):
    if tool_name not in _CACHEABLE_TOOLS:
        return None
    key = _tool_cache_key(tool_name, args)
    entry = _tool_result_cache.get(key)
    if entry and (_time.time() - entry[0]) < _TOOL_CACHE_TTL:
        return entry[1]
    return None


def _tool_cache_put(tool_name: str, args: dict, result: dict):
    if tool_name not in _CACHEABLE_TOOLS:
        return
    if isinstance(result, dict) and result.get("error"):
        return  # Fehler nicht cachen
    key = _tool_cache_key(tool_name, args)
    _tool_result_cache[key] = (_time.time(), result)
    # Eviction
    if len(_tool_result_cache) > _TOOL_CACHE_MAX:
        oldest = min(_tool_result_cache, key=lambda k: _tool_result_cache[k][0])
        del _tool_result_cache[oldest]


# ---- Shared Tool-Loop Helpers (#389) ----

def format_tool_detail(tool_name: str, tool_input: dict) -> str:
    """Einheitliche Formatierung eines Tool-Aufrufs für Display/Logging."""
    if tool_input:
        args_str = ", ".join(f"{k}={repr(v)[:50]}" for k, v in tool_input.items())
        return f"{tool_name}({args_str})"
    return tool_name


def format_tool_result(result, *, ensure_str: bool = True) -> str:
    """Ergebnis eines Tool-Calls einheitlich als truncated String formatieren."""
    if isinstance(result, str):
        return _truncate_tool_result(result)
    return _truncate_tool_result(_json.dumps(result, ensure_ascii=False))


async def execute_tool_call(
    orch,
    *,
    boss_cfg,
    project_id: str,
    tool_name: str,
    tool_input: dict,
    execution_mode: str | None = None,
    user_text: str = "",
    file_read_cache: dict[str, str] | None = None,
) -> tuple[object, bool]:
    """
    Einheitlicher Tool-Execution-Pfad für alle 4 Loops.
    Handles: MCP-Tools, reguläre Tools, file_read-Dedup.
    Returns: (result, is_error)
    """
    # MCP-Tool?
    if tool_name.startswith("mcp_") and boss_cfg.mcp_servers:
        try:
            result = await orch._execute_mcp_tool(boss_cfg, tool_name, tool_input)
            return result, False
        except Exception as te:
            return {"error": str(te)}, True

    # Reguläres Tool
    tool = orch._resolve_allowed_tool(boss_cfg, tool_name, execution_mode, user_text=user_text)
    if tool is None:
        return {"error": f"Tool '{tool_name}' ist in diesem Modus nicht erlaubt"}, True

    # file_read Deduplication
    if file_read_cache is not None and tool_name == "file_read":
        _rpath = tool_input.get("path", "")
        if _rpath and _rpath in file_read_cache:
            logger.debug("file_read dedup: '%s'", _rpath)
            return file_read_cache[_rpath], False

    try:
        result = await orch._execute_tool(
            tool,
            boss_cfg=boss_cfg,
            project_id=project_id,
            tool_name=tool_name,
            tool_input=tool_input,
            execution_mode=execution_mode,
        )
        # Cache befüllen
        if file_read_cache is not None and tool_name == "file_read":
            _rpath = tool_input.get("path", "")
            if _rpath:
                file_read_cache[_rpath] = result
        return result, False
    except Exception as te:
        return {"error": str(te)}, True


def check_repeated_signature(
    signature: tuple[str, ...],
    last_signature: tuple[str, ...] | None,
    repeated_count: int,
    threshold: int = 4,
) -> tuple[tuple[str, ...] | None, int, bool]:
    """
    Prüft ob eine Tool-Signatur wiederholt wurde.
    Returns: (new_last_signature, new_count, should_abort)
    """
    if signature and signature == last_signature:
        repeated_count += 1
    else:
        repeated_count = 0
    return signature, repeated_count, repeated_count >= threshold


def handle_request_tools(
    orch,
    boss_cfg,
    execution_mode: str | None,
    categories: list[str],
    loaded_categories: set[str],
    current_tools: list[dict],
) -> tuple[int, dict]:
    """
    On-Demand Tool-Kategorien nachladen.
    Returns: (added_count, result_dict)
    """
    new_cats = [c for c in categories if c not in loaded_categories]
    added_count = 0
    if new_cats:
        new_schemas = orch._category_tools_schema(boss_cfg, execution_mode, new_cats)
        existing = {t.get("function", {}).get("name") or t.get("name", "") for t in current_tools}
        added = [s for s in new_schemas if s["function"]["name"] not in existing]
        current_tools.extend(added)
        added_count = len(added)
        loaded_categories.update(new_cats)
        logger.info(
            "request_tools: +%d Tools (Kategorien: %s, Agent: %s)",
            added_count, new_cats, boss_cfg.id,
        )
    return added_count, {
        "ok": True,
        "categories": categories,
        "tools_added": added_count,
        "note": "Tools geladen — direkt verwendbar.",
    }
