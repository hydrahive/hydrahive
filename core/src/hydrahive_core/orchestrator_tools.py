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


# ---------------------------------------------------------------------------
# #431: Tool-Timeouts
# ---------------------------------------------------------------------------
_TOOL_TIMEOUT_DEFAULT = 120  # Default: 2 Minuten
# Per-Tool-Overrides. Tools die länger brauchen (sync-APIs mit großen Payloads).
_TOOL_TIMEOUTS: dict[str, int] = {
    # music_generate: removed in #801 — tool returns immediately,
}


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


def _canonicalize(obj: object) -> object:
    """Macht ein Python-Objekt hashable und kanonisch fuer Signature-Vergleich.

    #844 Gate 8: ersetzt String-basierte Normalisierung durch echtes
    Struktur-Matching:
    - dict → tuple of sorted (key, canonical(value)) — key-order-invariant
    - list/tuple → tuple of canonical(items)
    - int/float gleichwertig wenn numerisch gleich (5 == 5.0)
    - str → NFC-normalisiert (Unicode-Composition-Form)
    - bool/None → unveraendert
    """
    import unicodedata
    if obj is None or isinstance(obj, bool):
        return obj
    if isinstance(obj, (int, float)):
        # 5 und 5.0 gleich behandeln — wenn float ohne Nachkomma → int
        if isinstance(obj, float) and obj.is_integer():
            return int(obj)
        return obj
    if isinstance(obj, str):
        return unicodedata.normalize("NFC", obj)
    if isinstance(obj, dict):
        return tuple(sorted(
            (str(k), _canonicalize(v)) for k, v in obj.items()
        ))
    if isinstance(obj, (list, tuple)):
        return tuple(_canonicalize(x) for x in obj)
    # Fallback: stringify
    return str(obj)


def _tool_call_signature(tool_calls: list) -> tuple[str, ...]:
    """Fingerprint eines Tool-Call-Sets für Endlosschleifen-Erkennung.

    file_write wird komplett ausgeschlossen: chunked-writes (overwrite + mehrfach append)
    zum selben Pfad sind legitim und kein Loop. max_rounds fängt echte Endlosschleifen ab.
    shell_exec: nur name, keine args (Output-abhängige Folgebefehle sind kein Loop).

    #844 Gate 8: Args werden via _canonicalize() in eine kanonische Python-
    Struktur umgewandelt. Robust gegen JSON-Whitespace, key-order, int/float-
    Equivalenz (5 == 5.0), Unicode-Form-Variationen.
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
        # Args parsen + kanonisieren
        try:
            _parsed = _j.loads(args_raw) if isinstance(args_raw, str) else args_raw
            canonical = _canonicalize(_parsed)
            args_repr = repr(canonical)
        except Exception:
            args_repr = repr(args_raw)
        signature.append(f"{name}:{args_repr}")
    return tuple(signature)


# #638: Hinweise auf existierende Mechanismen — kein Verweis auf
# nicht-existente "Permission XYZ hinzufügen"-Konzepte mehr.
_TOOL_SUGGESTIONS = {
    "shell_exec":        "Falls bwrap-Sandbox blockiert: execution_mode='unrestricted' setzen (Admin-only).",
    "file_read":         "Tool ist nicht in der aktiven Tool-Whitelist verfügbar.",
    "file_write":        "Tool ist nicht in der aktiven Tool-Whitelist verfügbar.",
    "git_push":          "Lade Git-Tools via tool_search(query='git ...') in dieser Session.",
    "git_commit":        "Lade Git-Tools via tool_search(query='git ...') in dieser Session.",
    "git_clone":         "Lade Git-Tools via tool_search(query='git ...') in dieser Session.",
    "git_status":        "Lade Git-Tools via tool_search(query='git ...') in dieser Session.",
    "write_system_file": "execution_mode='unrestricted' setzen (Admin-only).",
    "read_system_file":  "execution_mode='unrestricted' setzen (Admin-only).",
}

def _tool_denied_error(tool_name: str, *, denial_count: int = 0) -> dict:
    suggestion = _TOOL_SUGGESTIONS.get(
        tool_name,
        f"Tool '{tool_name}' ist nicht in der aktiven Tool-Whitelist. "
        "Falls deferred: via tool_search laden.",
    )
    msg = f"Tool '{tool_name}' ist in der aktiven Tool-Whitelist nicht verfügbar"
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
    request_user: str | None = None,
):
    """Führt ein einzelnes Tool aus. Gibt Fehler-Dict zurück wenn tool=None."""
    args = dict(tool_input or {})
    # #584-C Security: Runtime-project_id IMMER Vorrang für Target-Tools — sonst
    # könnte ein Agent in Projekt A via Tool-Input `{"project_id": "projectB"}`
    # gegen Projekt Bs Target-Auth resolven (Auth-Bypass).
    # Wir ziehen project_id immer aus args heraus (sonst doppeltes kwarg beim
    # tool.execute()-Aufruf). Für ask_agent (#669) wird der gepoppte Wert als
    # _requested_project_id zurückgelegt, damit AskAgentTool die Ziel-Session
    # explizit setzen kann — ohne das Security-Modell der Target-Tools zu berühren.
    _TARGET_TOOLS_NO_OVERRIDE = frozenset({
        "server_shell", "server_file_read", "server_file_write", "wks_shell_exec",
    })
    _maybe_injected_pid = args.pop("project_id", None)
    if _maybe_injected_pid and tool_name == "ask_agent":
        # ask_agent: expliziter Tool-Input-project_id immer als _requested_project_id
        # weiterreichen — auch wenn gleich dem Runtime-Wert. Sonst würde z.B.
        # "personal_till" == Runtime-Wert nicht weitergereicht und AskAgentTool
        # fiele in den UUID-Session-Fallback (personal_*-Branch).
        args["_requested_project_id"] = _maybe_injected_pid
    elif _maybe_injected_pid and tool_name in _TARGET_TOOLS_NO_OVERRIDE:
        # Target-Tools: Wert immer verwerfen; Warning nur bei Mismatch.
        if _maybe_injected_pid != project_id:
            logger.warning(
                "Tool '%s' [agent=%s]: project_id im Tool-Input (%r) ignoriert — Runtime-Wert (%r) bleibt autoritativ.",
                tool_name, getattr(boss_cfg, "id", "?"), _maybe_injected_pid, project_id,
            )
    elif _maybe_injected_pid:
        logger.debug(
            "Tool '%s' [agent=%s]: project_id im Tool-Input (%r) ignoriert.",
            tool_name, getattr(boss_cfg, "id", "?"), _maybe_injected_pid,
        )
    effective_pid = project_id
    if tool is None:
        return _tool_denied_error(tool_name)

    # #364: Tool-Result Cache für idempotente Read-Tools
    cache_result = _tool_cache_get(tool_name, args)
    if cache_result is not None:
        logger.debug("Tool cache hit: %s", tool_name)
        return cache_result

    # #638: Permissions-Layer entfernt — autoritativ ist `_allowed_tool_map`
    # (Whitelist) + permission_classifier (Risiko) + execution_mode (nur Shell).
    # Nur _execution_mode wird an Tools weitergereicht, das aktuell ausschliesslich
    # ShellExecTool semantisch konsumiert.
    import inspect
    sig = inspect.signature(tool.execute)
    has_kwargs = any(p.kind == p.VAR_KEYWORD for p in sig.parameters.values())
    extra = {}
    if has_kwargs:
        extra["_execution_mode"] = execution_mode or boss_cfg.effective_execution_mode(execution_mode)
        if request_user and request_user != "internal":
            extra["_request_user"] = request_user
    # #431: Tool-Timeout (Default 120s, per-Tool overrides in _TOOL_TIMEOUTS).
    # music_generate: MiniMax music-2.6 Sync-API + hex-Decode kann 2–4 min dauern (10x
    # Starter Plan live gemessen 21.04.2026). Bis music_generate async wird (wie
    # video_generate), braucht es 300 s.
    import asyncio as _aio
    _timeout = _TOOL_TIMEOUTS.get(tool_name, _TOOL_TIMEOUT_DEFAULT)
    try:
        result = await _aio.wait_for(
            tool.execute(
                agent_id=boss_cfg.id,
                project_id=effective_pid,
                **extra,
                **args,
            ),
            timeout=_timeout,
        )
    except _aio.TimeoutError:
        logger.error("Tool '%s' Timeout nach %ds", tool_name, _timeout)
        return {"error": f"Tool '{tool_name}' hat nach {_timeout}s nicht geantwortet (Timeout)"}

    # Cache nur idempotente Read-Tools
    _tool_cache_put(tool_name, args, result)
    return result


# ---- Tool-Result Cache (#364) ----
import time as _time
import hashlib as _hashlib

_CACHEABLE_TOOLS = frozenset({"read_system_file", "list_directory", "git_status", "git_diff"})  # file_read removed: offset makes caching unsafe
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
import unicodedata as _unicodedata

# #453/#775: Prompt-Injection-Muster die in Tool-Outputs neutralisiert werden.
# Defense-in-Depth: Pattern-Filter greift NACH Unicode-Cleaning in
# _sanitize_tool_output. Wichtige Grenze: prompt-injection ist systemisch
# nicht mit Regex lösbar — dies ist nur eine Haertungs-Schicht, nicht Schutz.
_INJECTION_PATTERNS = [
    (_re.compile(r'ignore\s+(?:all\s+)?previous\s+instructions?', _re.I), '[FILTERED]'),
    (_re.compile(r'forget\s+everything\s+(?:above|before)', _re.I), '[FILTERED]'),
    (_re.compile(r'you\s+are\s+now\s+(?:a|an|in\s+role\s+of)\s+', _re.I), '[FILTERED]'),
    (_re.compile(r'new\s+instructions?:\s*', _re.I), '[FILTERED]'),
    (_re.compile(r'system\s*:\s*you\s+(?:are|must|should)', _re.I), '[FILTERED]'),
    (_re.compile(r'<\s*(?:system|instructions?|prompt)\s*>', _re.I), '[FILTERED-TAG]'),
    # #775: Erweiterte Muster — ChatML-Markup, role-play, overrides.
    (_re.compile(r'act\s+as\s+(?:a|an)\s+\w+', _re.I), '[FILTERED]'),
    (_re.compile(r'remember\s+this\s+from\s+now\s+on', _re.I), '[FILTERED]'),
    (_re.compile(r'<\|(?:system|user|assistant|model|im_start|im_end)\|>', _re.I), '[FILTERED-TAG]'),
    (_re.compile(r'disregard\s+(?:all\s+)?(?:previous|above|earlier)\s+(?:instructions?|rules?|system)', _re.I), '[FILTERED]'),
    (_re.compile(r'override\s+(?:your\s+)?(?:system\s+)?(?:instructions?|prompts?|rules?)', _re.I), '[FILTERED]'),
    (_re.compile(r'supersede\s+(?:your\s+)?(?:previous|original)\s+(?:instructions?|rules?)', _re.I), '[FILTERED]'),
    (_re.compile(r'jailbreak', _re.I), '[FILTERED-TAG]'),
    (_re.compile(r'\bdan\s+mode\b', _re.I), '[FILTERED-TAG]'),
]

# #775: Unsichtbare Unicode-Zeichen die vor der Regex-Pruefung entfernt werden.
# Bi-Directional-Overrides koennen Pattern visuell aufspalten; Zero-Width-
# Chars erlauben "i<ZWS>gnore" als Bypass. NFKC-Normalisierung (getrennt)
# loest Ligaturen auf (z.B. "ﬁgnore" → "fignore").
_INVISIBLE_STRIP = [
    "\u200b", "\u200c", "\u200d", "\ufeff",                     # zero-width
    "\u202a", "\u202b", "\u202c", "\u202d", "\u202e",            # bidi embedding/override
    "\u2066", "\u2067", "\u2068", "\u2069",                     # bidi isolates
    "\x00",                                                      # null-byte
]

# #775: Wrapping-Konstanten. LLM wird im System-Prompt informiert dass
# <tool_output>-Inhalte Daten und keine Instruktionen sind.
_TOOL_OUTPUT_OPEN          = "<tool_output>"
_TOOL_OUTPUT_CLOSE         = "</tool_output>"
_TOOL_OUTPUT_CLOSE_ESCAPED = "<TOOL_OUTPUT_CLOSE_ESCAPED>"


def _sanitize_tool_output(text: str) -> str:
    """#453/#775: Neutralisiert Prompt-Injection-Muster + Unicode-Tricks.

    Reihenfolge bewusst:
    1. Unsichtbare Chars entfernen (Zero-Width, Bidi-Overrides, Null-Byte)
    2. NFKC-Normalisierung (Ligaturen, lookalikes auf kanonische Form)
    3. Regex-Pattern-Ersetzung
    """
    if not isinstance(text, str):
        return text
    for _ch in _INVISIBLE_STRIP:
        if _ch in text:
            text = text.replace(_ch, "")
    text = _unicodedata.normalize("NFKC", text)
    for pattern, replacement in _INJECTION_PATTERNS:
        text = pattern.sub(replacement, text)
    return text


def format_tool_result(result, *, ensure_str: bool = True) -> str:
    """Ergebnis eines Tool-Calls einheitlich als truncated + sanitized String formatieren.

    #775: Zusaetzlich zum Sanitize wird das Ergebnis in
    <tool_output>...</tool_output> eingewrappt. Das LLM wird im System-Prompt
    (orchestrator_context._TOOL_OUTPUT_POLICY) instruiert, den Inhalt der
    Tags als Daten zu behandeln — nicht als Instruktionen. Enthaltene
    </tool_output>-Sequenzen werden escaped (Schutz gegen Tag-Closing-
    Injection).
    """
    if isinstance(result, str):
        sanitized = _sanitize_tool_output(_truncate_tool_result(result))
    elif isinstance(result, dict) and "image_base64" in result:
        # #414: image_base64 nicht in die LLM-History serialisieren (spart Tokens)
        summary = {k: v for k, v in result.items() if k != "image_base64"}
        summary["image"] = f"[screenshot {result.get('format', 'png')}, {result.get('size_bytes', '?')} bytes — an Frontend gestreamt]"
        sanitized = _sanitize_tool_output(_json.dumps(summary, ensure_ascii=False))
    else:
        sanitized = _sanitize_tool_output(_truncate_tool_result(_json.dumps(result, ensure_ascii=False)))

    # #775: Closing-Tag-Escape — verhindert dass Content den Wrapper
    # vorzeitig schliessen und Instruktionen einschmuggeln kann.
    sanitized = sanitized.replace(_TOOL_OUTPUT_CLOSE, _TOOL_OUTPUT_CLOSE_ESCAPED)
    # #818: Indirect Prompt-Injection-Filter — nach Sanitize, vor Wrap.
    # Erkannte Injection-Trigger werden mit Warning-Banner umrahmt.
    from .prompt_injection_filter import filter_injection
    wrapped = f"{_TOOL_OUTPUT_OPEN}{sanitized}{_TOOL_OUTPUT_CLOSE}"
    return filter_injection(wrapped)


async def execute_tool_call(
    orch,
    *,
    boss_cfg,
    project_id: str,
    tool_name: str,
    tool_input: dict,
    execution_mode: str | None = None,
    user_text: str = "",
    request_user: str | None = None,
    file_read_cache: dict[str, str] | None = None,
    tool_call_id: str = "",
    confirm_signal=None,
    # #820: Sequence-Guard overrides — von orchestrator_dispatch injiziert
    force_sequence_confirm: bool = False,
    sequence_confirm_info: tuple | None = None,  # (SequenceMatch, tool_list) — nur wenn force_sequence_confirm=True
) -> tuple[object, bool]:
    """
    Einheitlicher Tool-Execution-Pfad für alle 4 Loops.
    Handles: MCP-Tools, reguläre Tools, file_read-Dedup.
    Returns: (result, is_error)

    #641: bei `RiskLevel.CONFIRM` wird der Call pausiert und auf eine
    User-Antwort über den /tool-confirm-Endpoint gewartet. `tool_call_id`
    muss eindeutig pro Call sein (vom jeweiligen Tool-Loop generiert),
    `confirm_signal` ist ein optionaler Sync-Callback (yield-fähig in
    async-Generator-Loops), der das `tool_confirm_required`-Event ans
    Frontend signalisiert, BEVOR der Wait beginnt.
    """
    # #717: ToolGuard — harte erste Stufe vor jeder anderen Logik.
    # Verhindert Writes in stale HydraHive-Checkouts (z.B. ältere Clones
    # unter /projects/hydrahivedev/repo). Diagnose bleibt erlaubt.
    # Niemals raise. Für mutierende Tools fail-closed, damit ein Guard-Bug
    # keine Schreibaktion unkontrolliert durchlässt.
    _tool_guard_mutating_tools = {
        "shell_exec", "project_shell", "server_shell", "wks_shell_exec",
        "file_write", "file_patch", "server_file_write", "server_file_patch",
    }
    try:
        from .tool_guard import check_tool_guard as _check_tool_guard
        _guard_decision = _check_tool_guard(tool_name, tool_input, project_id=project_id)
    except Exception as _guard_err:  # noqa: BLE001
        logger.error(
            "tool_guard unexpected failure: tool=%s err=%s",
            tool_name, _guard_err,
            exc_info=True,
        )
        if tool_name in _tool_guard_mutating_tools:
            return {
                "error": f"Tool '{tool_name}' blockiert — ToolGuard-Fehler.",
                "risk": "tool_guard_error",
                "code": "tool_guard_exception",
                "hint": "Globaler ToolGuard ist fehlgeschlagen; mutierende Tools werden fail-closed blockiert.",
                "tool_name": tool_name,
            }, True
        _guard_decision = None
    if _guard_decision is not None and not _guard_decision.allowed:
        logger.info(
            "tool_guard blocked tool=%s code=%s detected=%s",
            tool_name, _guard_decision.code, _guard_decision.detected_path,
        )
        return {
            "error": _guard_decision.message,
            "risk": "tool_guard_block",
            "code": _guard_decision.code,
            "hint": _guard_decision.hint,
            "detected_path": _guard_decision.detected_path,
            "canonical_path": _guard_decision.canonical_path,
            "tool_name": tool_name,
        }, True

    # #664: IsolationMode-Enforcement (built-in hard policy).
    # Läuft VOR MCP-Branch, Permission-Classifier und PreToolUse-Hook:
    # verbotene Calls sollen keine Admin-Hooks erreichen. Nur aktiv wenn
    # ein Sub-Agent-Worktree-Kontext mit isolation_mode gesetzt ist;
    # normale Agents ohne Kontext sind nicht betroffen.
    try:
        from .tool_registry import current_workspace_context as _cur_ctx
        _iso_ctx = _cur_ctx()
    except Exception:
        _iso_ctx = None
    if _iso_ctx is not None and _iso_ctx.isolation_mode:
        from .subagent_isolation import IsolationError as _IsoErr, allow_tool as _allow_tool
        try:
            _iso_decision = _allow_tool(_iso_ctx.isolation_mode, tool_name)
        except _IsoErr as _iso_err:
            logger.warning(
                "IsolationMode validation failed: mode=%s tool=%s err=%s",
                _iso_ctx.isolation_mode, tool_name, _iso_err,
            )
            return {
                "error": f"Tool '{tool_name}' blockiert — IsolationMode ungültig.",
                "risk": "isolation_error",
                "tool_name": tool_name,
                "isolation_mode": _iso_ctx.isolation_mode,
            }, True
        if not _iso_decision.allowed:
            logger.info(
                "IsolationMode blocked tool=%s mode=%s: %s",
                tool_name, _iso_ctx.isolation_mode, _iso_decision.reason,
            )
            return {
                "error": (
                    f"Tool '{tool_name}' ist in IsolationMode "
                    f"'{_iso_ctx.isolation_mode}' nicht erlaubt."
                ),
                "risk":           "isolation_block",
                "isolation_mode": _iso_ctx.isolation_mode,
                "tool_name":      tool_name,
                "reason":         _iso_decision.reason,
                "hint":           "Sub-Agent läuft in isolierter Worktree-Runtime.",
            }, True

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
    # #641: CONFIRM-Round-Trip — pausiert auf User-Antwort statt zu loggen
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
        if risk == RiskLevel.CONFIRM:
            # #820: ToolSword-Sequenz erzwingt CONFIRM — auch trusted-Agents
            # müssen bei erkannten Sequenzen bestätigen. risk_policy gilt nur
            # für normale CONFIRM-Patterns, nicht für Cross-Tool-Sequenzen.
            if force_sequence_confirm and sequence_confirm_info:
                _seq_match, _seq_tc_list = sequence_confirm_info
                logger.warning(
                    "ToolSword sequence guard: agent=%s tool=%s — forcing confirm for sequence '%s'",
                    boss_cfg.id, tool_name, _seq_match.sequence_name or "unknown",
                )
                _force_for_seq = True
            else:
                _force_for_seq = False

            # Trusted-Agent: CONFIRM wird automatisch genehmigt — kein
            # Banner, keine Pause, kein SSE-Event. DENY (oben) ist davon
            # nicht betroffen. Bewusste Admin-Entscheidung pro Agent.
            # #820: Bei erkannter ToolSword-Sequenz gilt risk_policy nicht —
            # auch trusted-Agents müssen bestätigen.
            if (
                not _force_for_seq
                and getattr(boss_cfg, "risk_policy", "interactive") == "trusted"
            ):
                _tcid_auto = tool_call_id or f"auto-{tool_name}-{int(_time.time() * 1000)}"
                logger.info(
                    "tool-confirm auto-approve (trusted): agent=%s tool=%s tcid=%s",
                    boss_cfg.id, tool_name, _tcid_auto,
                )
                # Fall-through zur regulären Tool-Ausführung unten.
            else:
                from .tool_confirmation import (
                    request_confirmation, wait_for_confirmation,
                )
                # session_id aus aktiver Session ableiten — kein Zwang
                # für Caller, ihn extra durchzureichen.
                _active = orch._sessions.get_active(project_id) if hasattr(orch, "_sessions") else None
                _session_id = _active.id if _active else ""
                _tcid = tool_call_id or f"auto-{tool_name}-{int(_time.time() * 1000)}"
                request_confirmation(_session_id, _tcid, tool_name, tool_input)
                # Stream-Pfade können hier ein SSE-Event yielden, bevor
                # die Pause beginnt — Frontend rendert den Bestätigungs-Dialog.
                if confirm_signal is not None:
                    try:
                        confirm_signal({
                            "type":         "tool_confirm_required",
                            "session_id":   _session_id,
                            "tool_call_id": _tcid,
                            "tool_name":    tool_name,
                            "tool_input":   tool_input,
                            "risk":         "confirm",
                        })
                    except Exception as _sig_err:
                        logger.debug("confirm_signal callback failed: %s", _sig_err)
                decision = await wait_for_confirmation(_session_id, _tcid)
                if decision == "approve":
                    logger.info("tool-confirm approve: %s tcid=%s", tool_name, _tcid)
                    # Fällt durch zur normalen Ausführung
                elif decision == "deny":
                    logger.info("tool-confirm deny: %s tcid=%s", tool_name, _tcid)
                    return {
                        "error": f"Tool '{tool_name}' wurde vom User abgelehnt.",
                        "risk":  "confirm_denied",
                        "hint":  "Der User hat die Bestätigungs-Anfrage abgewiesen.",
                    }, True
                else:  # timeout
                    logger.warning("tool-confirm timeout: %s tcid=%s", tool_name, _tcid)
                    return {
                        "error": f"Tool '{tool_name}' nicht innerhalb 5 Minuten bestätigt.",
                        "risk":  "confirm_timeout",
                        "hint":  "Bestätigung nicht rechtzeitig — Aktion verworfen.",
                    }, True
    except Exception as _cls_err:
        logger.debug("Permission classifier / confirm error: %s", _cls_err)

    # #655: PreToolUse Hook-Runtime — kann Tool-Call blockieren.
    # No-op wenn settings.json fehlt oder kein Matcher trifft.
    # SICHERHEITSMODELL: Runtime-Fehler in der Hook-Kette sind fail-closed —
    # ein defekter Guard darf nicht still allowen.
    _pre_decision = None
    _pre_error: Exception | None = None
    try:
        from .hook_runtime import run_pretool_hooks as _run_pre
        _hook_ctx = {
            "project_id": project_id,
            "agent_id": getattr(boss_cfg, "id", ""),
        }
        _pre_decision = await _run_pre(tool_name, tool_input, context=_hook_ctx)
    except Exception as _hook_err:
        _pre_error = _hook_err
        logger.error(
            "PreToolUse hook runtime crashed (fail-closed): tool=%s err=%s",
            tool_name, _hook_err, exc_info=True,
        )

    if _pre_error is not None:
        try:
            from .hook_runtime import _redact_str as _red
            _hint = _red(str(_pre_error))[:200]
        except Exception:
            _hint = "hook runtime error"
        return {
            "error": f"Tool '{tool_name}' blockiert — PreToolUse-Hook-Runtime-Fehler.",
            "risk": "hook_error",
            "hint": _hint,
        }, True

    if _pre_decision is not None and _pre_decision.action == "block":
        logger.warning("PreToolUse hook blocked tool=%s: %s", tool_name, _pre_decision.message)
        return {
            "error": f"Tool '{tool_name}' wurde von einem PreToolUse-Hook blockiert.",
            "risk": "hook_block",
            "hint": _pre_decision.message or "Siehe Hook-Logs.",
        }, True

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
            request_user=request_user,
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

        # #655: PostToolUse Hook-Runtime — non-blocking, nur Warnings.
        try:
            from .hook_runtime import run_posttool_hooks as _run_post
            _post_ctx = {
                "project_id": project_id,
                "agent_id": getattr(boss_cfg, "id", ""),
            }
            _post_report = await _run_post(
                tool_name, tool_input, result, is_error=False, context=_post_ctx,
            )
            for _w in _post_report.warnings:
                logger.warning("PostToolUse warning tool=%s: %s", tool_name, _w)
        except Exception as _hook_err:
            logger.debug("PostToolUse hook runtime error: %s", _hook_err)

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

    #623: Pro Tool wird die zentrale Identifier-Achse extrahiert (Pfad,
    Command, Query, URL, Issue), damit legitime Batch-Arbeit auf vielen
    unterschiedlichen Objekten NICHT als Loop fehlklassifiziert wird:
        - file_patch auf 5 verschiedene Dateien → 5 unterschiedliche Fingerprints
        - file_patch auf dieselbe Datei 5× → identische Fingerprints, Abort greift
    Dieselbe Logik gilt für file_read/file_write/file_search/web_search/http_request
    sowie gitea_*-Tools (Issue-/Repo-basiert).
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
                elif tool_name == "file_read":
                    # #845: file_read inkludiert offset, damit Pagination
                    # (gleiche Datei, aufeinanderfolgende 4000-Zeichen-Fenster
                    # wegen has_more:true) nicht fälschlich als Loop gewertet
                    # wird. Nur gleiche Datei + gleicher Offset = echter Loop.
                    payload = f"{data.get('path','')}@{data.get('offset',0)}"
                elif tool_name == "file_write":
                    # #623: Pfad als alleinige Identifier-Achse — Batch-Edits auf
                    # unterschiedliche Dateien sollen durchlaufen, gleiche Datei
                    # 5× hintereinander bleibt Loop.
                    payload = str(data.get("path", ""))
                elif tool_name in ("file_patch", "file_edit"):
                    # #853: search/old_string dazunehmen, damit Multi-Patch-
                    # Refactor auf derselben Datei verschiedene Stellen ohne
                    # künstlichen Loop-Abruch editieren kann. Lediglich identische
                    # (path + search) Kombination → Loop.
                    payload = f"{data.get('path','')}|{str(data.get('search',''))[:80]}"
                elif tool_name == "file_search":
                    payload = f"{data.get('path','')}|{data.get('query','')}"
                elif tool_name == "web_search":
                    payload = str(data.get("query", ""))
                elif tool_name == "http_request":
                    payload = str(data.get("url", "")) or raw
                elif tool_name.startswith("gitea_"):
                    issue = data.get("issue_number") or data.get("number") or ""
                    repo = data.get("repo") or ""
                    if repo or issue:
                        payload = f"{repo}#{issue}"
                    else:
                        payload = raw
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



# =============================================================================
# #856 — Halluzinated Tool-Call Detector
# =============================================================================
#
# MiniMax (und gelegentlich andere Provider) geben manchmal ein halb-
# strukturiertes Tool-Call-Format als Plaintext im assistant-content aus:
#
#     [TOOL_CALL]
#     {tool => "shell_exec", args => {
#       --command "..."
#     }}
#     [/TOOL_CALL]
#
# Statt einen echten tool_use/tool_call-Block zu emittieren. Der Orchestrator
# sieht keine Tool-Calls, gibt den Text als finale Antwort zurueck — Reibung
# fuer den User.
#
# Dieser Detector erkennt das Muster, damit der Orchestrator einen
# automatischen Retry-Prompt anhaengen und den Turn wiederholen kann.

import re as _re

_HALLUCINATION_PATTERN = _re.compile(
    r"\[TOOL_CALL\]"                       # [TOOL_CALL]-Marker
    r"|\[/TOOL_CALL\]"                     # geschlossener Marker
    r"|^\s*\{\s*[\"']?tool[\"']?\s*=>\s*"  # {tool => ... am Zeilenanfang
    r"|^\s*\{\s*[\"']?tool[\"']?\s*:\s*[\"']\w+[\"']\s*,\s*[\"']?args[\"']?\s*:"
    r"|</?\w+:tool_call\b"                 # #862: <minimax:tool_call> / </minimax:tool_call>
    r"|<\w+:tool_call>",                   # #862: <ns:tool_call> ohne Attribute
    _re.IGNORECASE | _re.MULTILINE,
)


def is_hallucinated_tool_call(content: str) -> bool:
    """#856: True wenn der assistant-content wie ein in Text ausgegebener
    Tool-Call aussieht (z.B. [TOOL_CALL]{tool => ...}). Nur anwenden wenn
    KEIN echter tool_use-Block im Turn war — sonst Falschmeldung bei Boss-
    Reports die das Muster zitieren.
    """
    if not content or not isinstance(content, str):
        return False
    # Mindestlaenge um reine "tool" Erwaehnungen in Prosa auszuschliessen
    if len(content) < 15:
        return False
    return bool(_HALLUCINATION_PATTERN.search(content))


# v2: handle_request_tools entfernt — alle 9 Core-Tools sind immer geladen.
