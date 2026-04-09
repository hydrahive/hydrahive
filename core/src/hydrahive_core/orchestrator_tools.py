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
import time
from collections import defaultdict
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# #465: Denial Tracking — verhindert Agent-Endlosschleifen bei geblockten Tools
# ---------------------------------------------------------------------------
_DENIAL_THRESHOLD = 3          # Nach N Denials → härtere Fehlermeldung
_DENIAL_WINDOW    = 300        # Nur Denials der letzten 5 Minuten zählen
_denial_state: dict[str, list[float]] = defaultdict(list)  # agent_id:tool → timestamps


def _record_denial(agent_id: str, tool_name: str) -> int:
    """Denial tracken, Returns: Anzahl Denials im Window."""
    key = f"{agent_id}:{tool_name}"
    now = time.monotonic()
    _denial_state[key] = [t for t in _denial_state[key] if now - t < _DENIAL_WINDOW]
    _denial_state[key].append(now)
    count = len(_denial_state[key])
    if count >= _DENIAL_THRESHOLD:
        logger.warning("Denial threshold reached: agent=%s tool=%s count=%d", agent_id, tool_name, count)
    return count


def _record_success(agent_id: str, tool_name: str) -> None:
    """Bei Erfolg Denial-Counter für dieses Tool resetten."""
    key = f"{agent_id}:{tool_name}"
    _denial_state.pop(key, None)


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
    Safety-Cap für Tool-Ergebnisse bei Speicherung in der Session (#352, #470).

    Eigentliche Kürzung passiert erst in session_manager.llm_context() beim
    Zusammenbauen der History — dort wird nach Alter differenziert (letzte 5
    Tool-Results: 4k chars, ältere: 300 chars Preview).

    Ab 16k: Volltext auf Disk speichern, Model bekommt Preview + Pfad (#470).
    Ab 32k: zusätzlich Head+Tail-Abschnitt.
    """
    disk_threshold = 16000
    limit = 32000

    # #470: Große Outputs auf Disk persistieren
    if len(result_str) > disk_threshold:
        import uuid
        from pathlib import Path
        storage_dir = Path("/tmp/hydrahive-tool-results")
        storage_dir.mkdir(parents=True, exist_ok=True)
        result_id = uuid.uuid4().hex[:12]
        result_path = storage_dir / f"{result_id}.txt"
        try:
            result_path.write_text(result_str, encoding="utf-8")
            logger.debug("Tool result persisted: %s (%d chars)", result_path, len(result_str))
        except Exception as e:
            logger.warning("Tool result persist failed: %s", e)
            result_path = None

        if len(result_str) > limit:
            preview = result_str[:4000]
            tail_preview = result_str[-1000:]
            disk_note = f"\n[Volltext auf Disk: {result_path}]" if result_path else ""
            return (
                preview
                + f"\n\n...[gekürzt: {len(result_str)} Zeichen total]{disk_note}\n\n...Ende:\n"
                + tail_preview
            )
        # 16k-32k: vollständig behalten aber Disk-Backup haben
        return result_str

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


# #440: Permission Suggestions bei Tool-Denial
_TOOL_SUGGESTIONS = {
    "shell_exec":        "Execution-Mode auf 'elevated' oder 'root' setzen",
    "file_write":        "Permission 'filesystem.write' hinzufügen",
    "file_read":         "Permission 'filesystem.read' hinzufügen",
    "git_push":          "Permission 'git.write' hinzufügen",
    "git_commit":        "Permission 'git.write' hinzufügen",
    "write_system_file": "Execution-Mode auf 'root' oder 'unrestricted' setzen",
    "read_system_file":  "Permission 'system.read' hinzufügen",
}

def _tool_denied_error(tool_name: str, *, denial_count: int = 0) -> dict:
    suggestion = _TOOL_SUGGESTIONS.get(tool_name, f"Tool '{tool_name}' in Agent-Config erlauben")
    msg = f"Tool '{tool_name}' ist in diesem Modus nicht erlaubt"
    if denial_count >= _DENIAL_THRESHOLD:
        msg += (
            f" (bereits {denial_count}x verweigert). "
            "STOPP: Dieses Tool ist für dich nicht verfügbar. "
            "Verwende ein anderes Tool oder teile dem User mit, dass du diese Aktion nicht ausführen kannst."
        )
    return {"error": msg, "suggestion": suggestion}


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
        return _tool_denied_error(tool_name)

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
    # #431: Tool-Timeout (Default 120s, konfigurierbar)
    import asyncio as _aio
    _TOOL_TIMEOUT = 120  # Default: 2 Minuten
    try:
        result = await _aio.wait_for(
            tool.execute(
                agent_id=boss_cfg.id,
                project_id=effective_pid,
                **extra,
                **args,
            ),
            timeout=_TOOL_TIMEOUT,
        )
    except _aio.TimeoutError:
        logger.error("Tool '%s' Timeout nach %ds", tool_name, _TOOL_TIMEOUT)
        return {"error": f"Tool '{tool_name}' hat nach {_TOOL_TIMEOUT}s nicht geantwortet (Timeout)"}

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


import re as _re

# #453: Prompt-Injection-Muster die in Tool-Outputs neutralisiert werden
_INJECTION_PATTERNS = [
    (_re.compile(r'ignore\s+all\s+previous\s+instructions?', _re.I), '[FILTERED]'),
    (_re.compile(r'forget\s+everything\s+(?:above|before)', _re.I), '[FILTERED]'),
    (_re.compile(r'you\s+are\s+now\s+(?:a|an)\s+', _re.I), '[FILTERED]'),
    (_re.compile(r'new\s+instructions?:\s*', _re.I), '[FILTERED]'),
    (_re.compile(r'system\s*:\s*you\s+(?:are|must|should)', _re.I), '[FILTERED]'),
    (_re.compile(r'<\s*(?:system|instructions?|prompt)\s*>', _re.I), '[FILTERED-TAG]'),
]


def _sanitize_tool_output(text: str) -> str:
    """#453: Neutralisiert bekannte Prompt-Injection-Muster in Tool-Outputs."""
    for pattern, replacement in _INJECTION_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def format_tool_result(result, *, ensure_str: bool = True) -> str:
    """Ergebnis eines Tool-Calls einheitlich als truncated + sanitized String formatieren."""
    if isinstance(result, str):
        return _sanitize_tool_output(_truncate_tool_result(result))
    # #414: image_base64 nicht in die LLM-History serialisieren (spart Tokens)
    if isinstance(result, dict) and "image_base64" in result:
        summary = {k: v for k, v in result.items() if k != "image_base64"}
        summary["image"] = f"[screenshot {result.get('format', 'png')}, {result.get('size_bytes', '?')} bytes — an Frontend gestreamt]"
        return _sanitize_tool_output(_json.dumps(summary, ensure_ascii=False))
    return _sanitize_tool_output(_truncate_tool_result(_json.dumps(result, ensure_ascii=False)))


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
        # #465: Denial tracken
        count = _record_denial(boss_cfg.id, tool_name)
        return _tool_denied_error(tool_name, denial_count=count), True

    # file_read Deduplication
    if file_read_cache is not None and tool_name == "file_read":
        _rpath = tool_input.get("path", "")
        if _rpath and _rpath in file_read_cache:
            logger.debug("file_read dedup: '%s'", _rpath)
            return file_read_cache[_rpath], False

    # #466: Permission Classifier — Risikobewertung vor Ausführung
    try:
        from .permission_classifier import classify_action, RiskLevel
        risk = await classify_action(tool_name, tool_input, use_llm=False)
        if risk == RiskLevel.DENY:
            logger.warning("Permission DENY: %s(%s) — Tool blockiert", tool_name, str(tool_input)[:80])
            return {
                "error": f"Aktion blockiert: '{tool_name}' wurde als gefährlich eingestuft.",
                "risk": "deny",
                "hint": "Diese Aktion wurde aus Sicherheitsgründen verhindert.",
            }, True
    except Exception as _cls_err:
        logger.debug("Permission classifier error: %s", _cls_err)

    # #523: Turn Journal — TOOL_USE Event
    try:
        from .turn_journal import journal as _tj, EventType as _JE
        from .session_manager import SessionManager
        _active_session = orch._sessions.get_active(project_id) if hasattr(orch, "_sessions") else None
        _session_id = _active_session.id if _active_session else ""
        _tj.append(_session_id, project_id, _JE.TOOL_USE, {"tool": tool_name}, tool_name=tool_name)
    except Exception:
        pass

    try:
        result = await orch._execute_tool(
            tool,
            boss_cfg=boss_cfg,
            project_id=project_id,
            tool_name=tool_name,
            tool_input=tool_input,
            execution_mode=execution_mode,
        )
        # #523: Turn Journal — TOOL_RESULT Event
        try:
            _tj.append(_session_id, project_id, _JE.TOOL_RESULT, {"tool": tool_name, "success": True}, tool_name=tool_name)
        except Exception:
            pass
        # #465: Erfolg → Denial-Counter resetten
        _record_success(boss_cfg.id, tool_name)
        # Cache befüllen
        if file_read_cache is not None and tool_name == "file_read":
            _rpath = tool_input.get("path", "")
            if _rpath:
                file_read_cache[_rpath] = result

        # #520: Boss Policy — Mutation tracken + ggf. Verification triggern
        try:
            from .settings import settings as _settings
            if _settings.boss_policy_enabled and boss_cfg:
                from .boss_policy import boss_policy as _bp
                _bp.record_mutation(project_id, tool_name, tool_input)
                if _bp.should_verify(project_id, tool_name):
                    _affected = _bp.get_pending_files(project_id)
                    _v_result = await _bp.trigger_verification(orch, project_id, boss_cfg, _affected)
                    _action = _bp.handle_result(_v_result)
                    if isinstance(result, dict):
                        result["_verification"] = _v_result.to_dict()
                        result["_verification_action"] = _action
                    logger.info("Boss Policy: Verification %s (%s) → %s",
                                _v_result.status.value, project_id, _action)
        except Exception as _bp_err:
            logger.debug("Boss Policy error: %s", _bp_err)

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
