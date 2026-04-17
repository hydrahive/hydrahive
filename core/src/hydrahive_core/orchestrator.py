"""
orchestrator.py — v2 Projekt-Agent Orchestrator (#8, AG2, AG5)

v2: Projekt = Agent. Kein Worker-Dispatch mehr. Projekt empfängt User-
Nachricht, baut LLM-Kontext auf, führt Tool-Loop mit den 9 Core-Tools
durch, gibt Final-Antwort zurück.

Ablauf:
1. User-Nachricht an Session anhängen
2. AGENT.md + Memory laden → System-Prompt
3. Session-History + System-Prompt → LLM (litellm)
4. LLM ruft Core-Tools auf → Tool-Loop executed → wiederholt
5. Final-Antwort in Session speichern

Teilmodule:
- orchestrator_llm.py      — LLM-Call-Maschinerie (Failover, OAuth, Retry)
- orchestrator_context.py  — System-Prompt, Memory-Budget, Compaction
- orchestrator_tools.py    — Tool-Utilities (Truncate, Signature, Execute)
- orchestrator_mcp.py      — MCP + Plugin Tool-Integration
- orchestrator_stream.py   — SSE-Streaming Response
- orchestrator_dispatch.py — Tool-Loop, Worker-Dispatch, Synthese
"""

import asyncio
import json
import logging

from .agent_config import AgentConfig
from .agent_discovery import AgentDiscovery
from .agent_runtime import AgentRuntime
from .project_config import ProjectConfig
from .session_manager import MessageRole, SessionManager
from .settings import settings
from .tool_registry import ToolRegistry, registry as default_registry
from . import tool_registry as _tool_reg

# Sub-Module importieren und für Backward-Compat re-exportieren
from .orchestrator_llm import (
    _should_failover,
    _llm_with_retry,
    _load_claude_oauth_token,
    _load_openai_codex_token,
    _resolve_model as _resolve_model_fn,
    _anthropic_oauth_call,
    _openai_codex_call,
    _llm_call_single as _llm_call_single_fn,
    _llm_call as _llm_call_fn,
    _apply_cache_control,
    check_llm_provider_available,
    _current_project_id,
)
from .orchestrator_context import (
    _context_mode,
    build_system_prompt,
    _repo_review_guidance,
    _compact_if_needed as _compact_if_needed_fn,
    _history_token_budget,
    _estimate_tokens,
)
from .orchestrator_tools import (
    DispatchResult,
    _truncate_tool_result,
    _tool_call_signature as _tool_call_signature_fn,
    _execute_tool as _execute_tool_fn,
)
from .orchestrator_mcp import (
    _load_mcp_server_map as _load_mcp_server_map_fn,
    _mcp_schemas_for_agent as _mcp_schemas_for_agent_fn,
    _execute_mcp_tool as _execute_mcp_tool_fn,
)
from .orchestrator_stream import handle_message_stream as _handle_message_stream_fn
from .orchestrator_dispatch import _tool_loop as _tool_loop_fn
from .session_metrics import metrics as _metrics

logger = logging.getLogger(__name__)


def _dedup_tools(tools: list[dict]) -> list[dict]:
    """Entfernt doppelte Tool-Namen (letzter Eintrag gewinnt nicht — erster bleibt)."""
    seen: set[str] = set()
    result = []
    for t in tools:
        name = t.get("function", {}).get("name", "")
        if name not in seen:
            seen.add(name)
            result.append(t)
    return result


def _load_workflow_prompt(project_dir) -> str:
    """
    Liest workflow.json aus dem Projektverzeichnis und serialisiert es in
    einen strukturierten Arbeitsanweisungs-Block für den System-Prompt.
    Gibt leeren String zurück wenn kein Workflow vorhanden oder leer.
    """
    import json as _json
    from pathlib import Path as _Path

    wf_path = _Path(project_dir) / "workflow.json"
    if not wf_path.exists():
        return ""
    try:
        wf = _json.loads(wf_path.read_text(encoding="utf-8"))
    except Exception as e:
        logger.debug("Failed to parse workflow file %s: %s", wf_path, e)
        return ""

    nodes: list[dict] = wf.get("nodes", [])
    edges: list[dict] = wf.get("edges", [])
    if not nodes:
        return ""

    # Topologische Reihenfolge via BFS ab Start-Node
    targets = {e["target"] for e in edges}
    start_ids = [n["id"] for n in nodes if n["id"] not in targets]
    if not start_ids:
        start_ids = [nodes[0]["id"]]

    ordered: list[dict] = []
    visited: set[str] = set()
    queue = list(start_ids)
    node_map = {n["id"]: n for n in nodes}
    edge_map: dict[str, list[dict]] = {}
    for e in edges:
        edge_map.setdefault(e["source"], []).append(e)

    while queue:
        nid = queue.pop(0)
        if nid in visited or nid not in node_map:
            continue
        visited.add(nid)
        ordered.append(node_map[nid])
        for e in edge_map.get(nid, []):
            if e["target"] not in visited:
                queue.append(e["target"])

    # Serialisieren
    NODE_TYPE_LABELS = {
        "step":       "Schritt",
        "datasource": "Datenquelle",
        "branch":     "Entscheidung",
        "end":        "Ende",
    }
    lines = [
        "## Arbeitsanweisung — Pflicht-Workflow",
        "Bearbeite diese Aufgabe IMMER nach folgendem Workflow. Arbeite jeden Schritt der Reihe nach ab:",
        "",
    ]
    step_num = 1
    for node in ordered:
        ntype = node.get("type", "step")
        data  = node.get("data", {})
        label = data.get("label", "")
        desc  = data.get("description", "")
        config = data.get("config", {})

        if ntype == "end":
            lines.append(f"{step_num}. **[Ende]** {label or 'Workflow abgeschlossen — gib deine Antwort aus.'}")
        elif ntype == "datasource":
            src_type = config.get("type", "")
            src_url  = config.get("url", "") or config.get("path", "")
            lines.append(f"{step_num}. **[Datenquelle: {src_type or 'extern'}]** {label}")
            if src_url:
                lines.append(f"   → URL/Pfad: `{src_url}`")
            if desc:
                lines.append(f"   → {desc}")
        elif ntype == "branch":
            condition = config.get("condition", label or "Bedingung prüfen")
            yes_label = config.get("yes_label", "Ja")
            no_label  = config.get("no_label", "Nein")
            lines.append(f"{step_num}. **[Entscheidung]** {condition}")
            out_edges = edge_map.get(node["id"], [])
            for oe in out_edges:
                el = oe.get("data", {}).get("label", "")
                target_node = node_map.get(oe["target"])
                target_label = target_node.get("data", {}).get("label", oe["target"]) if target_node else oe["target"]
                lines.append(f"   → {el or yes_label}: {target_label}")
        else:  # step
            lines.append(f"{step_num}. **[Schritt]** {label}")
            if desc:
                lines.append(f"   → {desc}")

        step_num += 1

    lines += [
        "",
        "Beginne NICHT mit der eigentlichen Antwort bevor du alle relevanten Schritte abgearbeitet hast.",
    ]
    return "\n".join(lines)


class Orchestrator:
    """
    Einer pro Core-Instanz.
    handle_message() ist der Haupt-Einstiegspunkt für eingehende Nachrichten.
    """

    def __init__(
        self,
        discovery:        AgentDiscovery,
        runtime:          AgentRuntime,
        sessions:         SessionManager,
        tool_reg:         ToolRegistry | None = None,
        mcp_servers_file: str = "",
    ) -> None:
        self._discovery         = discovery
        self._runtime           = runtime
        self._sessions          = sessions
        self._reg               = tool_reg or default_registry
        self._mcp_servers_file  = mcp_servers_file or str(settings.mcp_servers_config)
        self._project_queues: dict[str, asyncio.Queue]  = {}
        self._queue_tasks:    dict[str, asyncio.Task]   = {}
        self._queue_last_used: dict[str, float]         = {}
        self._queue_idle_timeout_s: float               = 600.0
        # #394: Queue Pool Limits — max gleichzeitige Queues
        self._max_queues: int = 50  # Bei >50 wird die älteste idle Queue entfernt

    # ---------------------------------------------------------------- Tool Resolution

    def _execution_mode_for_request(
        self,
        agent_cfg: AgentConfig,
        execution_mode: str | None = None,
    ) -> str | None:
        return agent_cfg.effective_execution_mode(execution_mode)  # type: ignore[arg-type]

    # v2: Die 9 Core-Tool-IDs + tool_search (Meta-Tool für Deferred, #620).
    # Plugins registrieren sich auch in der Registry,
    # deshalb können wir nicht blind all_tools() nehmen.
    _V2_CORE_TOOL_IDS = frozenset({
        "shell_exec", "file_read", "file_write", "file_patch",
        "file_search", "web_search", "read_memory", "write_memory",
        "ask_agent", "tool_search",
        # #584-C: Projekt-Target-Tools. Immer verfügbar — Tool liefert bei
        # fehlender Zuweisung einen klaren Fehler, kein Side-Effect.
        "server_shell", "server_file_read", "server_file_write", "wks_shell_exec",
    })

    def _allowed_tools(
        self,
        agent_cfg: AgentConfig,
        execution_mode: str | None = None,
        user_text: str = "",
    ) -> list:
        """v2: Gibt nur die 9 Core-Tools zurück — Plugins werden separat geladen."""
        return [t for t in self._reg.all_tools() if t.id in self._V2_CORE_TOOL_IDS]

    def _category_tools_schema(
        self,
        agent_cfg: AgentConfig,
        execution_mode: str | None,
        categories: list[str],
    ) -> list[dict]:
        """v2: Stub — Kategorien gibt es nicht mehr, alle Tools sind immer geladen."""
        return []

    def _allowed_tool_map(
        self,
        agent_cfg: AgentConfig,
        execution_mode: str | None = None,
        user_text: str = "",
        project_id: str = "",
    ) -> dict[str, object]:
        """
        v2: Core-Tools + bereits in dieser Session geladene deferred Tools (#620).
        """
        from .tool_registry import loaded_deferred_ids, session_key
        result: dict[str, object] = {
            tool.id: tool
            for tool in self._reg.all_tools()
            if tool.id in self._V2_CORE_TOOL_IDS
        }
        # Deferred Tools, die via ToolSearch in dieser Session freigegeben wurden
        if project_id:
            skey = session_key(project_id, agent_cfg.id)
            for tid in loaded_deferred_ids(skey):
                t = self._reg.get(tid)
                if t is not None:
                    result[tid] = t
        return result

    def _resolve_allowed_tool(
        self,
        agent_cfg: AgentConfig,
        tool_name: str,
        execution_mode: str | None = None,
        user_text: str = "",
        project_id: str = "",
    ):
        allowed = self._allowed_tool_map(
            agent_cfg, execution_mode, user_text=user_text, project_id=project_id,
        )
        return allowed.get(tool_name)

    async def _execute_tool(
        self, tool, *, boss_cfg, project_id, tool_name, tool_input=None,
        execution_mode=None, request_user: str | None = None,
    ):
        from .hooks import parse_hooks_config, run_hooks
        self._runtime.set_activity(boss_cfg.id, f"Tool: {tool_name}")

        # #472: Agent-YAML Hook-System (before_tool)
        hook_context = {
            "tool_name": tool_name,
            "tool_input": tool_input or {},
            "agent_id": boss_cfg.id,
            "project_id": project_id,
        }
        parsed_hooks = parse_hooks_config(getattr(boss_cfg, "hooks", None))
        before_hooks = parsed_hooks.get("before_tool", [])
        if before_hooks:
            allowed = await run_hooks("before_tool", hook_context, before_hooks)
            if not allowed:
                self._runtime.set_activity(boss_cfg.id, "Denkt…")
                return {"error": f"Tool '{tool_name}' blockiert durch Hook (before_tool)", "blocked": True}

        try:
            result = await _execute_tool_fn(
                tool, boss_cfg=boss_cfg, project_id=project_id,
                tool_name=tool_name, tool_input=tool_input,
                execution_mode=execution_mode,
                request_user=request_user,
            )

            # #472: Agent-YAML Hook-System (after_tool)
            after_hooks = parsed_hooks.get("after_tool", [])
            if after_hooks:
                hook_context["result"] = result
                await run_hooks("after_tool", hook_context, after_hooks)

            return result
        finally:
            self._runtime.set_activity(boss_cfg.id, "Denkt…")

    @staticmethod
    def _tool_call_signature(tool_calls: list) -> tuple[str, ...]:
        return _tool_call_signature_fn(tool_calls)

    # ---------------------------------------------------------------- MCP (delegiert)

    def _load_mcp_server_map(self) -> dict[str, dict]:
        return _load_mcp_server_map_fn(self._mcp_servers_file)

    async def _mcp_schemas_for_agent(self, agent_cfg: AgentConfig) -> list[dict]:
        return await _mcp_schemas_for_agent_fn(agent_cfg, self._mcp_servers_file)

    def _plugin_schemas_for_agent(self, agent_cfg: AgentConfig) -> list[dict]:
        """v2: Plugin-System entfernt."""
        return []

    async def _execute_mcp_tool(self, boss_cfg, prefixed_name, args):
        return await _execute_mcp_tool_fn(
            boss_cfg, self._mcp_servers_file, prefixed_name, args,
            runtime=self._runtime,
        )

    async def _write_forced_abort_handoff(
        self,
        boss_cfg: AgentConfig,
        current_messages: list[dict],
        *,
        reason: str,
        execution_mode: str | None = None,
    ) -> bool:
        """#624: Persistiert vor Forced-Aborts einen kompakten Resume-Handoff.

        Der Handoff ist Komfort-Schicht, kein kritischer Pfad: Fehler werden
        geloggt und der Tool-Loop finalisiert trotzdem normal weiter.
        """
        agent_dir = getattr(boss_cfg, "agent_dir", None)
        if not agent_dir:
            return False

        from datetime import datetime, timezone
        from pathlib import Path

        memory_dir = Path(agent_dir) / "memory"
        handoff_path = memory_dir / "_last_handoff.md"
        summary = ""

        prompt = (
            "[System: Tool-Loop wurde erzwungen beendet. Erstelle ein Resume-Handoff "
            "in maximal 300 Woertern. Ziel: Bei der naechsten User-Nachricht wie "
            "'mach weiter' soll der Agent ohne Chat-Kontext sicher fortsetzen koennen. "
            "Nenne konkret: Workspace-Pfad, Repo/Issue/Task falls bekannt, erledigte "
            "Schritte, offene TODOs, zuletzt beruehrte Dateien, naechste sichere "
            "Schritte. Wichtig: keine Tools aufrufen, nur Text. "
            f"Abort-Grund: {reason}.]"
        )
        handoff_messages = list(current_messages[-14:])
        handoff_messages.append({"role": "user", "content": prompt})

        try:
            resp = await self._llm_call(boss_cfg, handoff_messages, tools=None)
            summary = (resp.choices[0].message.content or "").strip()
        except Exception as e:
            logger.warning("Forced-Abort-Handoff LLM-Zusammenfassung fehlgeschlagen: %s", e)

        if not summary:
            summary = self._fallback_forced_abort_handoff(current_messages, reason=reason)

        try:
            memory_dir.mkdir(parents=True, exist_ok=True)
            created = datetime.now(timezone.utc).isoformat(timespec="seconds")
            handoff_path.write_text(
                (
                    f"# Auto-Handoff nach Forced-Abort\n\n"
                    f"- created_at: {created}\n"
                    f"- reason: {reason}\n"
                    f"- execution_mode: {execution_mode or 'default'}\n\n"
                    "Hinweis: Dieser Stand kann veraltet sein. Vor mutierenden Aktionen "
                    "Workspace, Repo, Issue und offene Dateien verifizieren.\n\n"
                    f"{summary.strip()}\n"
                ),
                encoding="utf-8",
            )
            logger.info(
                "Forced-Abort-Handoff geschrieben: agent=%s reason=%s path=%s",
                getattr(boss_cfg, "id", "?"), reason, handoff_path,
            )
            return True
        except Exception as e:
            logger.warning("Forced-Abort-Handoff konnte nicht geschrieben werden: %s", e)
            return False

    @staticmethod
    def _fallback_forced_abort_handoff(current_messages: list[dict], *, reason: str) -> str:
        """Best-effort-Handoff falls der zusaetzliche LLM-Call scheitert."""
        lines = [f"Abort-Grund: {reason}", "", "Letzter bekannter Verlauf:"]
        for msg in current_messages[-8:]:
            role = msg.get("role", "?")
            if msg.get("tool_calls"):
                tools = []
                for tc in msg.get("tool_calls", [])[:5]:
                    fn = tc.get("function", {}) if isinstance(tc, dict) else {}
                    tools.append(fn.get("name", "?"))
                lines.append(f"- {role}: Tool-Calls: {', '.join(tools)}")
                continue
            content = msg.get("content", "")
            if isinstance(content, list):
                content = " ".join(
                    str(part.get("text", part)) if isinstance(part, dict) else str(part)
                    for part in content
                )
            content = str(content).strip().replace("\n", " ")
            if content:
                lines.append(f"- {role}: {content[:300]}")
        lines.append("")
        lines.append("Naechster Schritt: Stand verifizieren, dann die letzte Aufgabe fortsetzen.")
        return "\n".join(lines)

    @staticmethod
    def _forced_abort_handoff_mtime(boss_cfg: AgentConfig) -> float | None:
        agent_dir = getattr(boss_cfg, "agent_dir", None)
        if not agent_dir:
            return None
        try:
            from pathlib import Path
            handoff_path = Path(agent_dir) / "memory" / "_last_handoff.md"
            return handoff_path.stat().st_mtime if handoff_path.exists() else None
        except Exception:
            return None

    @staticmethod
    def _clear_forced_abort_handoff_if_unchanged(
        boss_cfg: AgentConfig,
        expected_mtime: float | None,
    ) -> bool:
        """Loescht ein injiziertes Handoff nach erfolgreichem Folge-Turn.

        Nur loeschen, wenn die Datei seit Prompt-Aufbau nicht neu geschrieben
        wurde. So bleibt ein frischer Handoff erhalten, falls die Fortsetzung
        erneut in einen Abort laeuft.
        """
        if expected_mtime is None:
            return False
        agent_dir = getattr(boss_cfg, "agent_dir", None)
        if not agent_dir:
            return False
        try:
            from pathlib import Path
            handoff_path = Path(agent_dir) / "memory" / "_last_handoff.md"
            if not handoff_path.exists():
                return False
            if handoff_path.stat().st_mtime != expected_mtime:
                return False
            handoff_path.unlink()
            logger.info(
                "Forced-Abort-Handoff nach Resume geloescht: agent=%s path=%s",
                getattr(boss_cfg, "id", "?"), handoff_path,
            )
            return True
        except Exception as e:
            logger.debug("Forced-Abort-Handoff Cleanup uebersprungen: %s", e)
            return False

    async def _finalize_tool_loop_response(
        self,
        boss_cfg: AgentConfig,
        current_messages: list[dict],
        *,
        reason: str,
        execution_mode: str | None = None,
    ):
        summary_messages = list(current_messages)
        summary_messages.append(
            {
                "role": "user",
                "content": (
                    f"[System: Tool-Loop wird beendet ({reason}). "
                    "Berichte jetzt kurz und konkret: "
                    "1) Was wurde erfolgreich abgeschlossen? "
                    "2) Was konnte NICHT abgeschlossen werden — und warum? (Fehlermeldung, fehlendes Tool, Permission-Problem etc.) "
                    "Rufe keine weiteren Tools auf.]"
                ),
            }
        )
        return await self._llm_call(boss_cfg, summary_messages, tools=None)

    # ------------------------------------------------------------------ Queue

    def _get_queue(self, project_id: str) -> asyncio.Queue:
        if project_id not in self._project_queues:
            # #394: Queue Pool — älteste idle Queue evicten wenn Limit erreicht
            if len(self._project_queues) >= self._max_queues:
                oldest_id = min(
                    (pid for pid in self._project_queues if self._project_queues[pid].empty()),
                    key=lambda pid: self._queue_last_used.get(pid, 0),
                    default=None,
                )
                if oldest_id:
                    self._project_queues.pop(oldest_id, None)
                    task = self._queue_tasks.pop(oldest_id, None)
                    if task and not task.done():
                        task.cancel()
                    self._queue_last_used.pop(oldest_id, None)
                    logger.info("Queue Pool: evicted idle queue '%s' (limit %d)", oldest_id, self._max_queues)
            self._project_queues[project_id] = asyncio.Queue()
        return self._project_queues[project_id]

    async def _ensure_worker(self, project_id: str) -> None:
        task = self._queue_tasks.get(project_id)
        if task is None or task.done():
            self._queue_tasks[project_id] = asyncio.create_task(
                self._queue_worker(project_id),
                name=f"queue-{project_id}"
            )

    async def _queue_worker(self, project_id: str) -> None:
        """Verarbeitet Nachrichten sequenziell — kein paralleler Orchestrator-Zustand.

        Beendet sich bei Inaktivitaet automatisch und raeumt Queue/Task-Metadaten auf.
        """
        queue = self._get_queue(project_id)
        self._queue_last_used[project_id] = asyncio.get_event_loop().time()
        try:
            while True:
                try:
                    future, project_cfg, content, sender, execution_mode, request_user = await asyncio.wait_for(
                        queue.get(), timeout=self._queue_idle_timeout_s
                    )
                except asyncio.TimeoutError:
                    if queue.empty():
                        break
                    continue

                self._queue_last_used[project_id] = asyncio.get_event_loop().time()
                try:
                    result = await self._handle_message_impl(
                        project_id, project_cfg, content, sender, execution_mode,
                        request_user=request_user,
                    )
                    if not future.done():
                        future.set_result(result)
                except Exception as e:
                    if not future.done():
                        future.set_exception(e)
                finally:
                    queue.task_done()
        except asyncio.CancelledError:
            pass
        finally:
            current = asyncio.current_task()
            task = self._queue_tasks.get(project_id)
            if task is current or task is None or task.done():
                self._queue_tasks.pop(project_id, None)
                q = self._project_queues.get(project_id)
                if q is not None and q.empty():
                    self._project_queues.pop(project_id, None)
                self._queue_last_used.pop(project_id, None)

    # ------------------------------------------------------------------ Public API

    async def handle_message(
        self,
        project_id:  str,
        project_cfg: "ProjectConfig",
        content:     str,
        sender:      str = "user",
        execution_mode: str | None = None,
        request_user: str | None = None,
    ) -> tuple[str, list[str]]:
        """
        Oeffentlicher Einstiegspunkt — serialisiert ueber asyncio.Queue.
        Mehrere parallele Aufrufe ans gleiche Projekt werden sequenziell abgearbeitet.

        #668: `request_user` trägt den authentifizierten Auth-User für die
        User-Skill-Layer-Resolution. `None` (z.B. internal/ask_agent) →
        User-Skills werden nicht geladen.
        """
        await self._ensure_worker(project_id)
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        await self._get_queue(project_id).put((future, project_cfg, content, sender, execution_mode, request_user))
        self._queue_last_used[project_id] = asyncio.get_event_loop().time()
        try:
            return await asyncio.wait_for(asyncio.shield(future), timeout=300.0)
        except asyncio.TimeoutError:
            future.cancel()
            raise RuntimeError(
                f"Agent-Queue Timeout: Projekt '{project_id}' hat nach 300s nicht geantwortet. "
                "Worker möglicherweise abgestürzt oder überlastet."
            )

    async def _handle_message_impl(
        self,
        project_id: str,
        project_cfg,
        content: str,
        sender: str,
        execution_mode: str | None = None,
        *,
        request_user: str | None = None,
    ):
        """
        Hauptpfad: User-Nachricht → Boss-Agent → Antwort.
        Gibt (finaler Text, beteiligte Worker-IDs) zurück.
        """
        import time as _perf_time
        _request_start = _perf_time.monotonic()
        workers_used: list[str] = []

        # #656: OnTaskStart Hook-Runtime — fail-closed.
        # V1 GILT NUR FÜR DEN NON-STREAMING-PFAD (_handle_message_impl).
        # Streaming-Integration (orchestrator_stream._handle_message_stream_fn)
        # folgt in separatem Issue.
        # No-op wenn settings.json fehlt oder kein Matcher trifft.
        import datetime as _dt
        _started_at = _dt.datetime.now(_dt.timezone.utc).isoformat()
        _task_start_decision = None
        _task_start_error: Exception | None = None
        _task_dict = {
            "kind":            "agent_turn",
            "project_id":      project_id,
            "agent_id":        getattr(getattr(project_cfg, "agents", None), "boss", "") or project_id,
            "user":            sender,
            "session_id":      "",
            "message_preview": content,
            "started_at":      _started_at,
        }
        try:
            from .hook_runtime import run_task_start_hooks as _run_ts
            _task_start_decision = await _run_ts(_task_dict, context={})
        except Exception as _hook_err:
            _task_start_error = _hook_err
            logger.error(
                "OnTaskStart hook runtime crashed (fail-closed): project=%s err=%s",
                project_id, _hook_err, exc_info=True,
            )
        if _task_start_error is not None:
            try:
                from .hook_runtime import _redact_str as _red
                _hint = _red(str(_task_start_error))[:200]
            except Exception:
                _hint = "hook runtime error"
            return f"[Blockiert] OnTaskStart-Hook-Runtime-Fehler: {_hint}", []
        if _task_start_decision is not None and _task_start_decision.action == "block":
            logger.warning(
                "OnTaskStart hook blocked task project=%s: %s",
                project_id, _task_start_decision.message,
            )
            return f"[Blockiert] {_task_start_decision.message or 'Task von OnTaskStart-Hook blockiert.'}", []

        # 1. Nachricht in Session speichern
        await self._sessions.append(project_id, MessageRole.USER, content)

        # 2. Boss-Agent-Config holen — v2: direkt aus Projekt, v1: aus Discovery
        if getattr(project_cfg, "is_v2", False):
            from .agent_config import agent_config_from_project
            boss_cfg = agent_config_from_project(project_cfg)
        else:
            boss_cfg = self._discovery.get(project_cfg.agents.boss)
        if not boss_cfg:
            return f"[Fehler] Boss-Agent '{getattr(project_cfg.agents, 'boss', project_cfg.id)}' nicht gefunden.", []
        _handoff_mtime_at_prompt = self._forced_abort_handoff_mtime(boss_cfg)

        # 3. System-Prompt aufbauen (Soul + A-MEM + Skills) — !refresh invalidiert Cache.
        # #636: einheitlicher Builder, Tuple-Return, mit aktiver Session für working_state.
        _refresh = content.strip().startswith("!refresh")
        if _refresh:
            content = content.strip()[8:].strip()
        _active_session = self._sessions.get_active(project_id)
        _static_p, _dynamic_p = await build_system_prompt(
            boss_cfg, content, invalidate=_refresh, session=_active_session,
            request_user=request_user,
        )
        system_prompt = (_static_p + "\n\n" + _dynamic_p).strip() if _dynamic_p else _static_p

        # 3b. Projekt-Workflow injizieren falls vorhanden
        if project_cfg.project_dir:
            wf_text = _load_workflow_prompt(project_cfg.project_dir)
            if wf_text:
                system_prompt = system_prompt + "\n\n" + wf_text

        # 3c. Plan Mode Injection — v2: is_plan_mode/get_plan_file existieren nicht
        # (v1-Reste, bereits im Tool-Registry-Cleanup entfernt). Plan-Mode
        # laeuft jetzt via enter_plan_mode Tool ohne separaten Modus-State.
        try:
            from .tool_registry import is_plan_mode as _is_plan_mode, get_plan_file as _get_plan_file
        except ImportError:
            _is_plan_mode = lambda _pid: False
            _get_plan_file = lambda _pid: None
        if _is_plan_mode(project_id):
            _pf = _get_plan_file(project_id) or "plan.md"
            system_prompt += (
                "\n\n## PLAN MODE AKTIV\n\n"
                "**Du bist im Plan Mode.** Das bedeutet:\n"
                "- Du darfst NUR lesen: file_read, list_directory, shell_exec (nur lesende Befehle), "
                "web_search, gitea_repo_tree, gitea_repo_file etc.\n"
                "- Du darfst KEINE Dateien ändern, keinen Code schreiben, keine Commits machen.\n"
                "- EINZIGE Ausnahme: Du darfst den Plan schreiben/aktualisieren mit file_write "
                f"auf den Pfad `{_pf}`.\n"
                "- Analysiere den Code gründlich, verstehe die Architektur, identifiziere Risiken.\n"
                "- Schreibe einen strukturierten Plan mit: Analyse, Schritte, Risiken.\n"
                "- Wenn der Plan fertig ist, rufe `exit_plan_mode` auf.\n"
                "- Diese Instruktion überschreibt alle anderen Regeln bezüglich Code-Änderungen."
            )

        # v2 (#589): Worker-Kontext entfernt — kein Dispatch-Modell mehr

        # 4. Context kompaktieren wenn nötig (#74), dann LLM-Context holen
        await self._compact_if_needed(project_id, boss_cfg)
        messages = [{"role": "system", "content": system_prompt}]
        _sys_prompt_tokens = _estimate_tokens(system_prompt)
        _hist_budget = _history_token_budget(boss_cfg.llm.model, system_prompt_tokens=_sys_prompt_tokens)
        _raw_history = self._sessions.get_context(
            project_id,
            max_history_tokens=_hist_budget,
        )
        # #637: role:"tool" bleibt strukturiert erhalten — nicht mehr filtern.
        history = [m for m in _raw_history if m.get("role") in ("user", "assistant", "tool")]
        messages.extend(history)

        # ── Confirmation-Injection (#616) ────────────────────────────────────
        # Wenn der User nur kurz bestätigt (ja/ok/mach das/...) und die letzte
        # Assistent-Nachricht eine Rückfrage enthielt ("Soll ich...?"),
        # wird ein Hinweis injiziert: direkt loslegen, keine erneuten Reads.
        _CONFIRM_WORDS = {"ja", "ok", "yes", "mach das", "mach es", "go", "weiter",
                          "los", "bitte", "klar", "jep", "jap", "yep", "yup", "sure",
                          "mach", "tu es", "do it", "proceed"}
        _QUESTION_MARKERS = ("soll ich", "soll ich das", "implementiere ich", "lege ich los",
                             "machen?", "anfangen?", "starten?", "umsetzen?", "fortfahren?")
        _user_short = content.strip().lower().rstrip("!.,?")
        _last_asst = next(
            (m["content"] for m in reversed(history) if m.get("role") == "assistant"),
            ""
        ) or ""
        _is_confirmation = _user_short in _CONFIRM_WORDS and len(content.strip()) <= 20
        _was_question = any(marker in _last_asst.lower() for marker in _QUESTION_MARKERS)
        if _is_confirmation and _was_question:
            messages.append({
                "role": "system",
                "content": (
                    "[Hinweis] Der User hat bestätigt. Du hast in diesem Gespräch bereits "
                    "alle relevanten Dateien gelesen und eine Analyse erstellt. "
                    "Führe die geplanten Änderungen JETZT direkt aus — lies keine Dateien "
                    "erneut die du bereits in diesem Gespräch gelesen hast."
                ),
            })
            logger.debug("Confirmation-Injection: User bestätigte nach Rückfrage")

        # 5. Verfügbare Tools — v2: immer alle 9 Core-Tools
        boss_tools = self._allowed_tools(boss_cfg, execution_mode, user_text=content)
        litellm_tools = self._reg.as_litellm_tools(boss_tools) if boss_tools else []
        mcp_schemas = await self._mcp_schemas_for_agent(boss_cfg)
        if mcp_schemas:
            litellm_tools = _dedup_tools((litellm_tools or []) + mcp_schemas)
        # v2: Plugin-Tools vorerst deaktiviert — nur 9 Core-Tools
        # TODO: Plugins später pro Projekt konfigurierbar nachladen
        # plugin_schemas = self._plugin_schemas_for_agent(boss_cfg)
        # if plugin_schemas:
        #     litellm_tools = _dedup_tools((litellm_tools or []) + plugin_schemas)
        # Plan Mode: nur read-only Tools + enter/exit_plan_mode + file_write (für Plan-Datei)
        try:
            from .tool_registry import is_plan_mode as _is_plan_mode
        except ImportError:
            _is_plan_mode = lambda _pid: False
        if _is_plan_mode(project_id) and litellm_tools:
            _PLAN_MODE_ALLOWED = {"enter_plan_mode", "exit_plan_mode", "file_write",
                                  "file_read", "file_search"}
            litellm_tools = [
                t for t in litellm_tools
                if t.get("function", {}).get("name", "") in _PLAN_MODE_ALLOWED
                or any(
                    tool.is_read_only
                    for tool in [self._reg.get(t.get("function", {}).get("name", ""))]
                    if tool is not None
                )
            ]
            logger.info("Plan Mode aktiv — %d Tools nach Filter", len(litellm_tools))

        litellm_tools = litellm_tools or None

        # Anti-Halluzinations-Guard: System-Prompt mit tatsächlich verfügbaren Tools ergänzen
        _active_tool_names = [t["function"]["name"] for t in (litellm_tools or [])] if litellm_tools else []
        if _active_tool_names:
            _tool_guard = (
                "\n\n## Verfügbare Tools\n"
                "Du hast AUSSCHLIESSLICH folgende Tools zur Verfügung: "
                + ", ".join(f"`{n}`" for n in _active_tool_names) + ".\n"
                "KRITISCHE REGEL: Führe NUR Tools aus die in dieser Liste stehen. "
                "Für alles was kein eigenes Tool hat (Git, System, Pakete, SSH, etc.) nutze `shell_exec`. "
                "Schreibe NIEMALS Tool-Namen als Text in deine Antwort — "
                "nutze IMMER den echten Tool-Aufruf-Mechanismus."
            )
            messages[0]["content"] = messages[0]["content"] + _tool_guard
        elif not litellm_tools:
            messages[0]["content"] = messages[0]["content"] + (
                "\n\n## WARNUNG: Keine Tools verfügbar\n"
                "Dir stehen aktuell KEINE Tools zur Verfügung. "
                "Schreibe KEINE Tool-Aufrufe als Text. Antworte nur mit Text."
            )

        import json as _json
        sys_tokens   = _sys_prompt_tokens
        hist_tokens  = sum(
            _estimate_tokens(m.get("content", "") if isinstance(m.get("content"), str) else "")
            for m in history
        )
        tool_tokens  = _estimate_tokens(_json.dumps(litellm_tools or []))
        logger.info(
            "token-budget proj=%s sys≈%d hist≈%d/%d (%d msgs) tools≈%d total≈%d",
            project_id, sys_tokens, hist_tokens, _hist_budget, len(history), tool_tokens,
            sys_tokens + hist_tokens + tool_tokens,
        )

        # 6. LLM aufrufen
        self._runtime.set_activity(boss_cfg.id, "Denkt…")
        # #512: project_id für Retry/Failover-Metriken setzen
        _current_project_id.set(project_id)
        _llm_start = _perf_time.monotonic()
        try:
            response = await self._llm_call(boss_cfg, messages, litellm_tools)
        except Exception as e:
            err_str = str(e).lower()
            _context_errors = ("prompt is too long", "maximum context length", "context_length_exceeded",
                               "error in input stream", "input too long", "request too large")
            if any(s in err_str for s in _context_errors):
                _metrics.record_overflow(project_id)
                # #499/#517: Reactive Compaction — erst compacten, dann retry
                logger.warning(
                    "Context-Overflow für Projekt '%s' — versuche Reactive Compaction. Fehler: %s",
                    project_id, str(e)[:120],
                )
                try:
                    await self._compact_if_needed(project_id, boss_cfg, keep_last=4)
                    # Context neu aufbauen mit kompaktierter Session
                    _compacted_history = self._sessions.get_context(
                        project_id, max_history_tokens=_hist_budget,
                    )
                    _retry_msgs = [messages[0]] + _compacted_history  # System-Prompt + kompaktierte History
                    response = await self._llm_call(boss_cfg, _retry_msgs, litellm_tools)
                    logger.info("Reactive Compaction erfolgreich — Projekt '%s' recovered", project_id)
                except Exception as retry_err:
                    # Retry auch fehlgeschlagen → Session-Reset als letzter Ausweg
                    logger.error(
                        "Reactive Compaction + Retry fehlgeschlagen für '%s': %s — Session-Reset",
                        project_id, str(retry_err)[:120],
                    )
                    _metrics.record_session_reset(project_id)
                    await self._sessions.new_session(project_id)
                    return (
                        "Die Konversation war zu lang und konnte nicht kompaktiert werden. "
                        "Session wurde zurückgesetzt — bitte wiederhole deine letzte Nachricht."
                    ), []
            else:
                logger.error("LLM-Fehler für Boss '%s': %s", boss_cfg.id, e)
                return "[Fehler] LLM nicht erreichbar — bitte später erneut versuchen.", []

        # #512: LLM-Call Metriken erfassen
        _llm_latency = (_perf_time.monotonic() - _llm_start) * 1000
        _m_input = _m_output = _m_cache_r = _m_cache_w = 0
        if hasattr(response, "usage") and response.usage:
            _u = response.usage
            _m_input   = getattr(_u, "input_tokens", 0) or getattr(_u, "prompt_tokens", 0) or 0
            _m_output  = getattr(_u, "output_tokens", 0) or getattr(_u, "completion_tokens", 0) or 0
            _m_cache_r = getattr(_u, "cache_read_input_tokens", 0) or 0
            _m_cache_w = getattr(_u, "cache_creation_input_tokens", 0) or 0
        _metrics.record_llm_call(
            project_id,
            model=boss_cfg.llm.model,
            prompt_tokens=sys_tokens,
            history_tokens=hist_tokens,
            tool_tokens=tool_tokens,
            input_tokens=_m_input,
            output_tokens=_m_output,
            cache_read=_m_cache_r,
            cache_write=_m_cache_w,
            latency_ms=_llm_latency,
        )

        # 7. Tool-Calls verarbeiten (Agentic Loop)
        final_response = response.choices[0].message.content or ""
        tool_calls = getattr(response.choices[0].message, "tool_calls", None)

        if tool_calls:
            final_response, workers_used = await self._tool_loop(
                boss_cfg, project_id, project_cfg, messages, response,
                execution_mode=execution_mode, request_user=request_user,
            )

        # 8. Antwort in Session speichern (mit echten Token-Counts aus API)
        _usage_meta: dict = {}
        if hasattr(response, "usage") and response.usage is not None:
            u = response.usage
            input_t  = getattr(u, "input_tokens",  0) or getattr(u, "prompt_tokens",     0) or 0
            output_t = getattr(u, "output_tokens", 0) or getattr(u, "completion_tokens", 0) or 0
            cache_write = getattr(u, "cache_creation_input_tokens", 0) or 0
            cache_read  = getattr(u, "cache_read_input_tokens",     0) or 0
            if input_t or output_t:
                _usage_meta = {
                    "model": boss_cfg.llm.model,
                    "input_tokens":       input_t,
                    "output_tokens":      output_t,
                    "cache_write_tokens": cache_write,
                    "cache_read_tokens":  cache_read,
                }
            total_t = input_t + output_t
            if total_t > 0 and _tool_reg._rate_limiter is not None:
                _tool_reg._rate_limiter.track_token_usage(boss_cfg.id, total_t)

        await self._sessions.append(
            project_id, MessageRole.ASSISTANT,
            final_response, agent_id=boss_cfg.id,
            **_usage_meta,
        )
        if final_response:
            self._clear_forced_abort_handoff_if_unchanged(boss_cfg, _handoff_mtime_at_prompt)

        self._runtime.set_activity(boss_cfg.id, None)

        # #373: Performance Metrics aktualisieren
        _elapsed_ms = (_perf_time.monotonic() - _request_start) * 1000
        handle = self._runtime._handles.get(boss_cfg.id)
        if handle:
            handle.total_requests += 1
            handle.total_response_ms += _elapsed_ms
            handle.last_response_ms = _elapsed_ms

        # #656: OnTaskDone Hook-Runtime — non-blocking.
        # V1 GILT NUR FÜR DEN NON-STREAMING-PFAD (s. oben).
        # Runtime-Fehler werden hier geloggt, aber NICHT zurückpropagiert —
        # final_response bleibt unverändert.
        try:
            from .hook_runtime import run_task_done_hooks as _run_td
            _task_dict["session_id"] = ""  # V1 ohne Session-Tracking
            _result_dict = {
                "ok":          True,
                "duration_ms": int(_elapsed_ms),
                "summary":     final_response or "",
                "error":       None,
            }
            _done_report = await _run_td(_task_dict, _result_dict, context={})
            for _w in _done_report.warnings:
                logger.warning("OnTaskDone warning project=%s: %s", project_id, _w)
        except Exception as _hook_err:
            logger.debug("OnTaskDone hook runtime error: %s", _hook_err)

        return final_response, workers_used

    # ----------------------------------------------------------------- Delegiert an Sub-Module

    async def _compact_if_needed(self, project_id: str, boss_cfg, keep_last: int = 6) -> None:
        return await _compact_if_needed_fn(self._sessions, project_id, boss_cfg, keep_last=keep_last)

    @staticmethod
    def _context_mode(user_text: str) -> str:
        return _context_mode(user_text)

    @staticmethod
    def _repo_review_guidance(agent_cfg, user_text: str) -> str:
        return _repo_review_guidance(agent_cfg, user_text)

    @staticmethod
    def _resolve_model(model: str, ollama_base_url: str | None = None) -> tuple[str, str | None]:
        return _resolve_model_fn(model, ollama_base_url)

    async def _llm_call_single(self, model_name: str, agent_cfg, messages, tools):
        return await _llm_call_single_fn(model_name, agent_cfg, messages, tools)

    async def _llm_call(self, agent_cfg, messages, tools):
        last_exc = None
        models = [agent_cfg.llm.model] + agent_cfg.llm.fallback_models
        for i, m in enumerate(models):
            try:
                return await self._llm_call_single(m, agent_cfg, messages, tools)
            except Exception as e:
                last_exc = e
                if i < len(models) - 1 and _should_failover(e):
                    # #512: Failover-Metrik
                    _pid = _current_project_id.get(None) if hasattr(_current_project_id, 'get') else None
                    if _pid:
                        _metrics.record_failover(_pid)
                        # #523: Turn Journal — Failover Event
                        try:
                            from .turn_journal import journal as _tj, EventType as _JE
                            _tj.append("", _pid, _JE.FAILOVER, {"from": m, "to": models[i+1]})
                        except Exception:
                            pass
                    logger.warning("LLM-Failover: '%s' → '%s' (%s)", m, models[i+1], str(e)[:80])
                    continue
                raise
        raise last_exc

    async def _anthropic_oauth_call(self, agent_cfg, messages, tools, token, model_override=None):
        return await _anthropic_oauth_call(agent_cfg, messages, tools, token, model_override)

    async def _openai_codex_call(self, agent_cfg, messages, tools, token_data, model_name, force_tools=True):
        return await _openai_codex_call(agent_cfg, messages, tools, token_data, model_name, force_tools)

    # ----------------------------------------------------------------- Streaming (delegiert)

    async def handle_message_stream(
        self,
        project_id:  str,
        project_cfg,
        content:     str,
        sender:      str = "user",
        execution_mode: str | None = None,
        *,
        request_user: str | None = None,
    ):
        async for chunk in _handle_message_stream_fn(
            self, project_id, project_cfg, content, sender, execution_mode,
            request_user=request_user,
        ):
            yield chunk

    # ----------------------------------------------------------------- Tool-Loop & Dispatch (delegiert)

    async def _tool_loop(self, boss_cfg, project_id, project_cfg, messages, response,
                         max_rounds=None, execution_mode=None, request_user: str | None = None):
        return await _tool_loop_fn(
            self, boss_cfg, project_id, project_cfg, messages, response,
            max_rounds, execution_mode, request_user,
        )

    # v2 (#589): Dispatch-Wrapper entfernt —
    # _parse_dispatch_calls, _dispatch_parallel, _run_worker_task, _synthesize
    # waren v1-Reste. In v2 laeuft alles direkt im Tool-Loop.
