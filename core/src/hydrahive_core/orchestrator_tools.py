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
        # #620 Phase 4: Deferred-Guard — MCP-Tool muss via tool_search geladen sein
        from .tool_registry import is_tool_loaded as _is_loaded, session_key as _skey
        if not _is_loaded(_skey(project_id, boss_cfg.id), tool_name):
            try:
                from .session_metrics import metrics as _metrics
                _metrics.record_deferred_hallucination(project_id)
            except Exception:
                pass
            return {
                "error": (
                    f"MCP-Tool '{tool_name}' wurde in dieser Session noch "
                    "nicht via tool_search geladen."
                ),
                "hint": (
                    f"Rufe zuerst: tool_search(query=\"select:{tool_name}\") "
                    "— danach ist das Tool im nächsten Turn aufrufbar."
                ),
            }, True
        try:
            result = await orch._execute_mcp_tool(boss_cfg, tool_name, tool_input)
            return result, False
        except Exception as te:
            return {"error": str(te)}, True

    # Reguläres Tool
    tool = orch._resolve_allowed_tool(
        boss_cfg, tool_name, execution_mode, user_text=user_text, project_id=project_id,
    )
    if tool is None:
        # #620: Deferred-Tool, das noch nicht via tool_search geladen wurde?
        from .tool_registry import registry as _reg
        _candidate = _reg.get(tool_name)
        if _candidate is not None and not _candidate.always_loaded:
            try:
                from .session_metrics import metrics as _metrics
                _metrics.record_deferred_hallucination(project_id)
            except Exception:
                pass
            return {
                "error": (
                    f"Tool '{tool_name}' ist deferred und wurde in dieser "
                    "Session noch nicht via tool_search geladen."
                ),
                "hint": (
                    f"Rufe zuerst: tool_search(query=\"select:{tool_name}\") "
                    "— danach ist das Tool im nächsten Turn direkt aufrufbar."
                ),
            }, True
        # Kein bekanntes Tool → Denial tracken (#465)
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


# #618: Fuzzy-Loop-Detection über Sliding-Window
#
# Motivation: check_repeated_signature greift nur bei EXAKT identischen Aufrufen
# in Folge. Wenn der Agent `cat /x/a`, `cat /x/b`, `cat /x/c`, `cat /x/d` …
# macht (20+ mal nur mit variierendem Dateinamen), erkennt das alte System
# keinen Loop, weil args unterschiedlich sind.
#
# Dieser Detector faltet den Fingerprint auf (tool_name, args-prefix) und
# zählt Vorkommen im letzten Fenster. Ab N Treffern → Abbruch.

_FUZZY_ARG_PREFIX_CHARS = 50
_FUZZY_WINDOW = 8
_FUZZY_THRESHOLD = 5


def _fuzzy_fingerprint(tool_name: str, args_json: str) -> str:
    """Tool-Name + diskriminierender Argument-Anteil als Loop-Fingerprint.

    Für shell_exec/file_* wird gezielt das `command`/`path`-Feld extrahiert
    statt den rohen JSON-Prefix zu nehmen, weil sonst Calls mit gleichem
    cwd-Prefix (`cd /projects/<id> && …`) alle das gleiche Fingerprint
    bekommen — Recovery-Sequenzen wie `git rebase --abort` → `git cherry-pick`
    → `git pull` würden fälschlich als Loop erkannt.
    """
    raw = args_json or ""
    payload = raw

    # JSON-Args parsen und das diskriminierende Feld pro Tool extrahieren.
    if raw.startswith("{"):
        try:
            import json as _json
            data = _json.loads(raw)
            if isinstance(data, dict):
                if tool_name == "shell_exec":
                    cmd = str(data.get("command", "")).strip()
                    # cwd-Prefix abschneiden — der wechselt selten und ist
                    # für die Operation selbst nicht aussagekräftig
                    if cmd.startswith("cd "):
                        amp = cmd.find("&&")
                        if amp != -1:
                            cmd = cmd[amp + 2:].strip()
                    payload = cmd
                elif tool_name in ("file_read", "file_write", "file_edit"):
                    payload = f"{data.get('path','')}|{str(data.get('content',''))[:40]}"
                else:
                    payload = raw
        except Exception:
            payload = raw

    # Diskriminierender Prefix: lang genug, dass git-Subcommands etc. drin sind
    return f"{tool_name}::{payload[:200]}"


def check_fuzzy_loop(
    history: list[str],
    window: int = _FUZZY_WINDOW,
    threshold: int = _FUZZY_THRESHOLD,
) -> tuple[bool, str | None]:
    """
    Prüft ob im Sliding-Window der gleiche fuzzy-Fingerprint ≥ threshold-mal
    vorkommt. Returns (should_abort, dominant_fingerprint).

    Beispiel: 5× "shell_exec::cat /projects/homepage-sicherheitstest/cms_backup"
    in den letzten 8 Calls → Abbruch mit Hinweis auf dominantes Pattern.
    """
    if not history or window <= 0:
        return False, None
    recent = history[-window:]
    if len(recent) < threshold:
        return False, None
    counts: dict[str, int] = {}
    for fp in recent:
        counts[fp] = counts.get(fp, 0) + 1
    dominant = max(counts.items(), key=lambda x: x[1])
    if dominant[1] >= threshold:
        return True, dominant[0]
    return False, None



# v2: handle_request_tools entfernt — alle 9 Core-Tools sind immer geladen.
