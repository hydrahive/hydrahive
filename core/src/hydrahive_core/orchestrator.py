"""
orchestrator.py — Boss-Agent Task-Dispatching (#8, AG2, AG5)

Boss-Agent empfängt User-Nachricht, baut LLM-Kontext auf,
delegiert Tasks an Worker-Agenten (parallel), aggregiert Ergebnis.

Ablauf:
1. User-Nachricht an Session anhängen
2. Soul + aktive Skills laden → System-Prompt
3. Session-History + System-Prompt → LLM (litellm)
4. LLM ruft dispatch_task Tool auf → Worker spawnen + parallel ausführen
5. Worker-Ergebnisse an Boss zurückgeben → Final-Antwort
6. Antwort in Session speichern

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
import litellm
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
)
from .orchestrator_context import (
    _context_mode,
    _build_system_prompt as _build_system_prompt_fn,
    _repo_review_guidance,
    _compact_if_needed as _compact_if_needed_fn,
    _history_token_budget,
    _estimate_tokens,
    get_skill_tool_constraints,
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
    _plugin_schemas_for_agent as _plugin_schemas_for_agent_fn,
    _execute_mcp_tool as _execute_mcp_tool_fn,
)
from .orchestrator_stream import handle_message_stream as _handle_message_stream_fn
from .orchestrator_dispatch import (
    _tool_loop as _tool_loop_fn,
    _parse_dispatch_calls as _parse_dispatch_calls_fn,
    _dispatch_parallel as _dispatch_parallel_fn,
    _run_worker_task as _run_worker_task_fn,
    _synthesize as _synthesize_fn,
)

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


def _build_worker_context(project_cfg, discovery) -> str:
    """
    Baut einen kurzen System-Prompt-Block mit den verfügbaren Worker-IDs
    und deren Beschreibungen. Verhindert, dass der Boss Worker-Namen halluziniert.
    """
    workers = getattr(getattr(project_cfg, "agents", None), "workers", []) or []
    if not workers:
        return ""
    lines = ["## Verfügbare Worker-Agenten",
             "",
             "Delegiere Aufgaben mit `dispatch_task(worker_id=..., task=...)`. "
             "Nur diese Worker-IDs sind gültig:"]
    for wid in workers:
        cfg = discovery.get(wid)
        if cfg:
            desc = getattr(cfg, "description", "") or getattr(cfg, "identity", wid)
            lines.append(f"- `{wid}`: {desc}")
        else:
            lines.append(f"- `{wid}`")
    lines.append("")
    lines.append("Verwende KEINE anderen Worker-IDs — sie existieren nicht.")
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

    # ---------------------------------------------------------------- Tool Resolution

    def _execution_mode_for_request(
        self,
        agent_cfg: AgentConfig,
        execution_mode: str | None = None,
    ) -> str | None:
        return agent_cfg.effective_execution_mode(execution_mode)  # type: ignore[arg-type]

    def _allowed_tools(
        self,
        agent_cfg: AgentConfig,
        execution_mode: str | None = None,
        user_text: str = "",
        meta_only: bool = False,
    ) -> list:
        from .tool_groups import select_tools
        from .tool_loader import META_TOOLS
        permissions = agent_cfg.effective_permissions(execution_mode)  # type: ignore[arg-type]
        if meta_only:
            ids = [t for t in META_TOOLS if t in (agent_cfg.tools or [])]
            if "request_tools" in (agent_cfg.tools or []) and "request_tools" not in ids:
                ids.insert(0, "request_tools")
            return self._reg.tools_for_agent(ids, agent_permissions=permissions)
        if getattr(agent_cfg, "tool_selection", "auto") == "always":
            base_ids = [t.id for t in self._reg.tools_for_agent(agent_cfg.tools or [], agent_permissions=permissions)]
        else:
            base_ids = [t.id for t in self._reg.tools_for_agent(
                select_tools(agent_cfg.tools, user_text), agent_permissions=permissions
            )]
        if user_text:
            skill_allowed, skill_blocked = get_skill_tool_constraints(agent_cfg, user_text)
            if skill_allowed or skill_blocked:
                from .skill_loader import Skill as _Skill
                dummy = _Skill(skill="__filter__", allowed_tools=skill_allowed, blocked_tools=skill_blocked)
                base_ids = dummy.apply_tool_constraints(base_ids)
        return self._reg.tools_for_agent(base_ids, agent_permissions=permissions)

    def _category_tools_schema(
        self,
        agent_cfg: AgentConfig,
        execution_mode: str | None,
        categories: list[str],
    ) -> list[dict]:
        """Gibt litellm-Tool-Schemas für angegebene Kategorien zurück."""
        from .tool_loader import tools_for_categories
        permissions = agent_cfg.effective_permissions(execution_mode)  # type: ignore[arg-type]
        tool_objects = tools_for_categories(
            self._reg, agent_cfg.tools or [], permissions, categories
        )
        return self._reg.as_litellm_tools(tool_objects)

    def _allowed_tool_map(
        self,
        agent_cfg: AgentConfig,
        execution_mode: str | None = None,
        user_text: str = "",
    ) -> dict[str, object]:
        permissions = agent_cfg.effective_permissions(execution_mode)  # type: ignore[arg-type]
        all_tools = {
            tool.id: tool
            for tool in self._reg.tools_for_agent(agent_cfg.tools or [], agent_permissions=permissions)
        }
        from .plugin_manager import plugin_manager as _pm
        for pt in _pm.get_plugin_tools_for_agent(agent_cfg.id):
            all_tools[pt.id] = pt
        if user_text:
            skill_allowed, skill_blocked = get_skill_tool_constraints(agent_cfg, user_text)
            if skill_allowed or skill_blocked:
                from .skill_loader import Skill as _Skill
                dummy = _Skill(skill="__filter__", allowed_tools=skill_allowed, blocked_tools=skill_blocked)
                filtered_ids = dummy.apply_tool_constraints(list(all_tools.keys()))
                return {k: v for k, v in all_tools.items() if k in filtered_ids}
        return all_tools

    def _resolve_allowed_tool(
        self,
        agent_cfg: AgentConfig,
        tool_name: str,
        execution_mode: str | None = None,
        user_text: str = "",
    ):
        allowed = self._allowed_tool_map(agent_cfg, execution_mode, user_text=user_text)
        return allowed.get(tool_name)

    async def _execute_tool(self, tool, *, boss_cfg, project_id, tool_name, tool_input=None, execution_mode=None):
        from .plugin_manager import plugin_manager as _pm
        self._runtime.set_activity(boss_cfg.id, f"Tool: {tool_name}")
        # #421: Blockierende Pre-Hooks
        hook_result = await _pm.emit("tool.before", project_id=project_id, tool_name=tool_name, tool_input=tool_input)
        if isinstance(hook_result, dict) and hook_result.get("block"):
            self._runtime.set_activity(boss_cfg.id, "Denkt…")
            return {"error": f"Tool blockiert: {hook_result.get('reason', 'Pre-Hook')}", "blocked": True}
        try:
            result = await _execute_tool_fn(
                tool, boss_cfg=boss_cfg, project_id=project_id,
                tool_name=tool_name, tool_input=tool_input,
                execution_mode=execution_mode,
            )
            await _pm.emit("tool.after", project_id=project_id, tool_name=tool_name, result=result)
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
        return _plugin_schemas_for_agent_fn(agent_cfg)

    async def _execute_mcp_tool(self, boss_cfg, prefixed_name, args):
        return await _execute_mcp_tool_fn(
            boss_cfg, self._mcp_servers_file, prefixed_name, args,
            runtime=self._runtime,
        )

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
                    future, project_cfg, content, sender, execution_mode = await asyncio.wait_for(
                        queue.get(), timeout=self._queue_idle_timeout_s
                    )
                except asyncio.TimeoutError:
                    if queue.empty():
                        break
                    continue

                self._queue_last_used[project_id] = asyncio.get_event_loop().time()
                try:
                    result = await self._handle_message_impl(
                        project_id, project_cfg, content, sender, execution_mode
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
    ) -> tuple[str, list[str]]:
        """
        Oeffentlicher Einstiegspunkt — serialisiert ueber asyncio.Queue.
        Mehrere parallele Aufrufe ans gleiche Projekt werden sequenziell abgearbeitet.
        """
        await self._ensure_worker(project_id)
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        await self._get_queue(project_id).put((future, project_cfg, content, sender, execution_mode))
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
    ):
        """
        Hauptpfad: User-Nachricht → Boss-Agent → Antwort.
        Gibt (finaler Text, beteiligte Worker-IDs) zurück.
        """
        workers_used: list[str] = []

        # 1. Nachricht in Session speichern
        await self._sessions.append(project_id, MessageRole.USER, content)

        # 2. Boss-Agent-Config holen
        boss_cfg = self._discovery.get(project_cfg.agents.boss)
        if not boss_cfg:
            return f"[Fehler] Boss-Agent '{project_cfg.agents.boss}' nicht gefunden.", []

        # 3. System-Prompt aufbauen (Soul + A-MEM + Skills) — !refresh invalidiert Cache
        _refresh = content.strip().startswith("!refresh")
        if _refresh:
            content = content.strip()[8:].strip()
        system_prompt = await self._build_system_prompt(boss_cfg, content, invalidate=_refresh)

        # 3b. Projekt-Workflow injizieren falls vorhanden
        if project_cfg.project_dir:
            wf_text = _load_workflow_prompt(project_cfg.project_dir)
            if wf_text:
                system_prompt = system_prompt + "\n\n" + wf_text

        # 3c. Worker-Kontext injizieren (verhindert Halluzination von Worker-IDs, #107)
        worker_ctx = _build_worker_context(project_cfg, self._discovery)
        if worker_ctx:
            system_prompt = system_prompt + "\n\n" + worker_ctx

        # 4. Context kompaktieren wenn nötig (#74), dann LLM-Context holen
        await self._compact_if_needed(project_id, boss_cfg)
        messages = [{"role": "system", "content": system_prompt}]
        _sys_prompt_tokens = _estimate_tokens(system_prompt)
        _hist_budget = _history_token_budget(boss_cfg.llm.model, system_prompt_tokens=_sys_prompt_tokens)
        _raw_history = self._sessions.get_context(
            project_id,
            max_history_tokens=_hist_budget,
        )
        history = [m for m in _raw_history if m.get("role") in ("user", "assistant")]
        messages.extend(history)
        # 5. Verfügbare Tools für Boss ermitteln — Phase 1: nur Meta-Tools
        use_meta_only = "request_tools" in (boss_cfg.tools or [])
        boss_tools = self._allowed_tools(boss_cfg, execution_mode, user_text=content, meta_only=use_meta_only)
        litellm_tools = self._reg.as_litellm_tools(boss_tools) if boss_tools else []
        mcp_schemas = await self._mcp_schemas_for_agent(boss_cfg)
        if mcp_schemas:
            litellm_tools = _dedup_tools((litellm_tools or []) + mcp_schemas)
        plugin_schemas = self._plugin_schemas_for_agent(boss_cfg)
        if plugin_schemas:
            litellm_tools = _dedup_tools((litellm_tools or []) + plugin_schemas)
        litellm_tools = litellm_tools or None

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
        try:
            response = await self._llm_call(boss_cfg, messages, litellm_tools)
        except Exception as e:
            err_str = str(e).lower()
            _context_errors = ("prompt is too long", "maximum context length", "context_length_exceeded",
                               "error in input stream", "input too long", "request too large")
            if any(s in err_str for s in _context_errors):
                logger.warning(
                    "Kontext zu lang für Projekt '%s' — Session wird zurückgesetzt. Fehler: %s",
                    project_id, e,
                )
                await self._sessions.new_session(project_id)
                return (
                    "Die Konversation war zu lang. Session wurde automatisch zurückgesetzt — bitte wiederhole deine letzte Nachricht."
                ), []
            logger.error("LLM-Fehler für Boss '%s': %s", boss_cfg.id, e)
            return "[Fehler] LLM nicht erreichbar — bitte später erneut versuchen.", []

        # 7. Tool-Calls verarbeiten (Agentic Loop)
        final_response = response.choices[0].message.content or ""
        tool_calls = getattr(response.choices[0].message, "tool_calls", None)

        if tool_calls:
            final_response, workers_used = await self._tool_loop(
                boss_cfg, project_id, project_cfg, messages, response, execution_mode=execution_mode
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

        self._runtime.set_activity(boss_cfg.id, None)
        return final_response, workers_used

    # ----------------------------------------------------------------- Delegiert an Sub-Module

    async def _compact_if_needed(self, project_id: str, boss_cfg, keep_last: int = 6) -> None:
        return await _compact_if_needed_fn(self._sessions, project_id, boss_cfg, keep_last=keep_last)

    @staticmethod
    def _context_mode(user_text: str) -> str:
        return _context_mode(user_text)

    async def _build_system_prompt(self, boss_cfg, user_text: str, *, invalidate: bool = False) -> str:
        return await _build_system_prompt_fn(boss_cfg, user_text, invalidate=invalidate)

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
    ):
        async for chunk in _handle_message_stream_fn(
            self, project_id, project_cfg, content, sender, execution_mode
        ):
            yield chunk

    # ----------------------------------------------------------------- Tool-Loop & Dispatch (delegiert)

    async def _tool_loop(self, boss_cfg, project_id, project_cfg, messages, response,
                         max_rounds=None, execution_mode=None):
        return await _tool_loop_fn(
            self, boss_cfg, project_id, project_cfg, messages, response,
            max_rounds, execution_mode,
        )

    def _parse_dispatch_calls(self, tool_calls):
        return _parse_dispatch_calls_fn(tool_calls)

    async def _dispatch_parallel(self, project_cfg, dispatches, context):
        return await _dispatch_parallel_fn(self, project_cfg, dispatches, context)

    async def _run_worker_task(self, dispatch):
        return await _run_worker_task_fn(self, dispatch)

    async def _synthesize(self, boss_cfg, messages, tool_calls, results):
        return await _synthesize_fn(self, boss_cfg, messages, tool_calls, results)
