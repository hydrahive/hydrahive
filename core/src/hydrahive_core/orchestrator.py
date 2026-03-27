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
- orchestrator_llm.py     — LLM-Call-Maschinerie (Failover, OAuth, Retry)
- orchestrator_context.py — System-Prompt, Memory-Budget, Compaction
- orchestrator_tools.py   — Tool-Utilities (Truncate, Signature, Execute)
"""

import asyncio
import json
import logging

from .agent_config import AgentConfig
from .agent_discovery import AgentDiscovery
from .agent_runtime import AgentRuntime
from .project_config import ProjectConfig
from .session_manager import MessageRole, SessionManager
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
    check_llm_provider_available,
)
from .orchestrator_context import (
    _context_mode,
    _build_system_prompt as _build_system_prompt_fn,
    _repo_review_guidance,
    _compact_if_needed as _compact_if_needed_fn,
    _history_token_budget,
    get_skill_tool_constraints,
)
from .orchestrator_tools import (
    DispatchResult,
    _truncate_tool_result,
    _tool_call_signature as _tool_call_signature_fn,
    _execute_tool as _execute_tool_fn,
)

logger = logging.getLogger(__name__)


class Orchestrator:
    """
    Einer pro Core-Instanz.
    handle_message() ist der Haupt-Einstiegspunkt für eingehende Nachrichten.
    """

    def __init__(
        self,
        discovery:  AgentDiscovery,
        runtime:    AgentRuntime,
        sessions:   SessionManager,
        tool_reg:   ToolRegistry | None = None,
    ) -> None:
        self._discovery      = discovery
        self._runtime        = runtime
        self._sessions       = sessions
        self._reg            = tool_reg or default_registry
        self._project_queues: dict[str, asyncio.Queue]  = {}
        self._queue_tasks:    dict[str, asyncio.Task]   = {}
        self._queue_last_used: dict[str, float]         = {}
        self._queue_idle_timeout_s: float               = 600.0

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
            # Nur Meta-Tools laden (Phase 1: request_tools + Kern-Tools)
            ids = [t for t in META_TOOLS if t in (agent_cfg.tools or [])]
            # request_tools immer dabei wenn es in agent.yaml steht
            if "request_tools" in (agent_cfg.tools or []) and "request_tools" not in ids:
                ids.insert(0, "request_tools")
            return self._reg.tools_for_agent(ids, agent_permissions=permissions)
        if getattr(agent_cfg, "tool_selection", "auto") == "always":
            base_ids = [t.id for t in self._reg.tools_for_agent(agent_cfg.tools or [], agent_permissions=permissions)]
        else:
            base_ids = [t.id for t in self._reg.tools_for_agent(
                select_tools(agent_cfg.tools, user_text), agent_permissions=permissions
            )]
        # Skill-Tool-Constraints anwenden (#48)
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
        # Bei der Ausführung alle erlaubten Tools ohne select_tools-Filter laden.
        # select_tools ist nur für Token-Optimierung des LLM-Schemas gedacht,
        # nicht für die Ausführungs-Whitelist.
        permissions = agent_cfg.effective_permissions(execution_mode)  # type: ignore[arg-type]
        all_tools = {
            tool.id: tool
            for tool in self._reg.tools_for_agent(agent_cfg.tools or [], agent_permissions=permissions)
        }
        # Skill-Tool-Constraints anwenden (#48)
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

    async def _execute_tool(self, tool, *, boss_cfg, project_id, tool_name, tool_input=None):
        self._runtime.set_activity(boss_cfg.id, f"Tool: {tool_name}")
        try:
            return await _execute_tool_fn(
                tool, boss_cfg=boss_cfg, project_id=project_id,
                tool_name=tool_name, tool_input=tool_input,
            )
        finally:
            self._runtime.set_activity(boss_cfg.id, "Denkt…")

    @staticmethod
    def _tool_call_signature(tool_calls: list) -> tuple[str, ...]:
        return _tool_call_signature_fn(tool_calls)

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

    # ------------------------------------------------------------------ public

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
                    # Idle-Timeout: wenn keine Arbeit anliegt, Worker sauber beenden
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
            # Cleanup nur wenn das registrierte Task-Objekt dieses Worker-Task ist
            current = asyncio.current_task()
            task = self._queue_tasks.get(project_id)
            if task is current or task is None or task.done():
                self._queue_tasks.pop(project_id, None)
                # Queue nur entfernen wenn wirklich leer, damit keine Arbeit verloren geht
                q = self._project_queues.get(project_id)
                if q is not None and q.empty():
                    self._project_queues.pop(project_id, None)
                self._queue_last_used.pop(project_id, None)

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

        # 4. Context kompaktieren wenn nötig (#74), dann LLM-Context holen
        await self._compact_if_needed(project_id, boss_cfg)
        messages = [{"role": "system", "content": system_prompt}]
        history = self._sessions.get_context(
            project_id, max_messages=10,
            max_history_tokens=_history_token_budget(boss_cfg.llm.model),
        )
        messages.extend(history)
        # 5. Verfügbare Tools für Boss ermitteln — Phase 1: nur Meta-Tools
        use_meta_only = "request_tools" in (boss_cfg.tools or [])
        boss_tools = self._allowed_tools(boss_cfg, execution_mode, user_text=content, meta_only=use_meta_only)
        litellm_tools = self._reg.as_litellm_tools(boss_tools) if boss_tools else None

        import json as _json
        sys_tokens   = len(system_prompt) // 4
        hist_tokens  = sum(
            len(m.get("content", "") if isinstance(m.get("content"), str) else "") // 4
            for m in history
        )
        tool_tokens  = len(_json.dumps(litellm_tools or [])) // 4
        logger.info(
            "token-budget proj=%s sys≈%d hist≈%d (%d msgs) tools≈%d total≈%d",
            project_id, sys_tokens, hist_tokens, len(history), tool_tokens,
            sys_tokens + hist_tokens + tool_tokens,
        )

        # 6. LLM aufrufen
        self._runtime.set_activity(boss_cfg.id, "Denkt…")
        try:
            response = await self._llm_call(boss_cfg, messages, litellm_tools)
        except Exception as e:
            err_str = str(e).lower()
            if "prompt is too long" in err_str or "maximum context length" in err_str or "context_length_exceeded" in err_str:
                logger.warning(
                    "Kontext zu lang für Projekt '%s' — Session wird zurückgesetzt. Fehler: %s",
                    project_id, e,
                )
                await self._sessions.new_session(project_id)
                return (
                    "Der Konversationsverlauf war zu lang für das Sprachmodell. "
                    "Die Session wurde automatisch zurückgesetzt — bitte wiederhole deine letzte Nachricht."
                ), []
            logger.error("LLM-Fehler für Boss '%s': %s", boss_cfg.id, e)
            return f"[Fehler] LLM nicht erreichbar: {e}", []

        # 7. Tool-Calls verarbeiten (Agentic Loop mit max. 5 Runden)
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

    # ----------------------------------------------------------------- private (delegiert an Sub-Module)

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
        """LLM-Call mit Failover. Ruft self._llm_call_single — bleibt mockbar für Tests."""
        models = [agent_cfg.llm.model] + agent_cfg.llm.fallback_models
        last_exc: Exception = RuntimeError("Kein Modell konfiguriert")
        for i, model_name in enumerate(models):
            try:
                return await self._llm_call_single(model_name, agent_cfg, messages, tools)
            except Exception as e:
                last_exc = e
                if i < len(models) - 1 and _should_failover(e):
                    logger.warning(
                        "Modell '%s' nicht verfügbar (%s) — Failover auf '%s'",
                        model_name, str(e)[:80], models[i + 1],
                    )
                    continue
                raise
        raise last_exc

    async def _anthropic_oauth_call(self, agent_cfg, messages, tools, token, model_override=None):
        return await _anthropic_oauth_call(agent_cfg, messages, tools, token, model_override)

    async def _openai_codex_call(self, agent_cfg, messages, tools, token_data, model_name, force_tools=True):
        return await _openai_codex_call(agent_cfg, messages, tools, token_data, model_name, force_tools)


    async def handle_message_stream(
        self,
        project_id:  str,
        project_cfg,
        content:     str,
        sender:      str = "user",
        execution_mode: str | None = None,
    ):
        """
        Streaming-Version von handle_message.
        Yieldet SSE-Chunks: data: <text>\n\n
        Bei Quota/Overload-Fehler: automatischer Failover auf fallback_models.
        Abschluss: data: {done: true}\n\n
        """
        import json as _json

        boss_id  = project_cfg.agents.boss
        boss_cfg = self._discovery.get(boss_id)
        if not boss_cfg:
            yield f"data: {_json.dumps({'error': f'Boss-Agent {boss_id} nicht gefunden'})}\n\n"
            return

        # Stale Interrupt-Flags löschen (von eventuell vorangegangenem abgebrochenem Request)
        from .tool_registry import clear_interrupt as _clear_interrupt
        _clear_interrupt(project_id)

        # Token-Usage Akkumulator für diese Session (über alle Tool-Runden)
        _usage: dict[str, int] = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0, "rounds": 0}

        # Session + System-Prompt aufbauen
        user_msg_saved = False
        await self._sessions.append(project_id, MessageRole.USER, content, agent_id=sender)
        user_msg_saved = True

        # Context-Kompaktierung vor dem LLM-Aufruf
        await self._compact_if_needed(project_id, boss_cfg)

        _refresh = content.strip().startswith("!refresh")
        if _refresh:
            content = content.strip()[8:].strip()
        system_prompt = await self._build_system_prompt(boss_cfg, content, invalidate=_refresh)
        history       = self._sessions.get_context(
            project_id, max_messages=10,
            max_history_tokens=_history_token_budget(boss_cfg.llm.model),
        )
        messages      = [{"role": "system", "content": system_prompt}] + history
        # Tool-Schema (Phase 1: nur Meta-Tools wenn request_tools konfiguriert)
        _use_meta = "request_tools" in (boss_cfg.tools or [])
        boss_tools    = self._allowed_tools(boss_cfg, execution_mode, user_text=content, meta_only=_use_meta)
        litellm_tools = self._reg.as_litellm_tools(boss_tools) if boss_tools else None

        import json as _json
        sys_tokens_s  = len(system_prompt) // 4
        hist_tokens_s = sum(
            len(m.get("content", "") if isinstance(m.get("content"), str) else "") // 4
            for m in history
        )
        tool_tokens_s = len(_json.dumps(litellm_tools or [])) // 4
        logger.info(
            "token-budget [stream] proj=%s sys≈%d hist≈%d (%d msgs) tools≈%d total≈%d",
            project_id, sys_tokens_s, hist_tokens_s, len(history), tool_tokens_s,
            sys_tokens_s + hist_tokens_s + tool_tokens_s,
        )

        models_to_try = [boss_cfg.llm.model] + boss_cfg.llm.fallback_models

        _provider_err = check_llm_provider_available(models_to_try)
        if _provider_err:
            yield f"data: {_json.dumps({'text': _provider_err})}\n\n"
            yield "data: {\"done\": true}\n\n"
            return

        try:
            full_response = ""

            for _attempt_idx, _model_name in enumerate(models_to_try):
                try:
                    full_response = ""
                    streamed_any  = False

                    # --- OpenAI Codex (ChatGPT Plus OAuth) ---
                    _is_codex = _model_name.startswith("openai-codex/")
                    if _is_codex:
                        codex_token = _load_openai_codex_token()
                        if codex_token:
                            # Non-streaming call, simuliere SSE danach
                            codex_resp = await self._openai_codex_call(
                                boss_cfg, messages, litellm_tools, codex_token, _model_name
                            )
                            msg = codex_resp.choices[0].message
                            # Tool-Loop (gleiche Logik wie litellm-Pfad unten)
                            cur_messages = list(messages)
                            last_signature: tuple[str, ...] | None = None
                            repeated_signature_count = 0
                            for _round in range(boss_cfg.max_tool_rounds):
                                if not getattr(msg, "tool_calls", None):
                                    break
                                signature = self._tool_call_signature(msg.tool_calls)
                                if signature and signature == last_signature:
                                    repeated_signature_count += 1
                                else:
                                    repeated_signature_count = 0
                                last_signature = signature
                                if repeated_signature_count >= 3:
                                    final = await self._finalize_tool_loop_response(
                                        boss_cfg,
                                        cur_messages,
                                        reason="wiederholte Tool-Signatur",
                                        execution_mode=execution_mode,
                                    )
                                    msg = final.choices[0].message
                                    break
                                # Tool-Calls ausführen
                                tool_results = []
                                asst_tc = [
                                    {"id": tc.id, "item_id": getattr(tc, "item_id", tc.id), "type": "function",
                                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                                    for tc in msg.tool_calls
                                ]
                                cur_messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": asst_tc})
                                for tc in msg.tool_calls:
                                    yield f"data: {_json.dumps({'tool_call': tc.function.name})}\n\n"
                                    tool = self._resolve_allowed_tool(boss_cfg, tc.function.name, execution_mode, user_text=content)
                                    try:
                                        args = _json.loads(tc.function.arguments or "{}")
                                        result = await self._execute_tool(
                                            tool,
                                            boss_cfg=boss_cfg,
                                            project_id=project_id,
                                            tool_name=tc.function.name,
                                            tool_input=args,
                                        )
                                    except Exception as te:
                                        result = {"error": str(te)}
                                    result_str = _truncate_tool_result(_json.dumps(result, ensure_ascii=False))
                                    tool_results.append({"role": "tool", "tool_call_id": tc.id, "content": result_str})
                                cur_messages.extend(tool_results)
                                next_resp = await self._openai_codex_call(
                                    boss_cfg, cur_messages, litellm_tools, codex_token, _model_name,
                                    force_tools=False,
                                )
                                msg = next_resp.choices[0].message
                            # Antwort streamen
                            text = msg.content or ""
                            full_response = text
                            streamed_any = bool(text)
                            if text:
                                yield f"data: {_json.dumps({'text': text})}\n\n"
                            break  # Modell hat geantwortet

                    # --- Claude Max OAuth ---
                    _is_claude    = _model_name.startswith(("claude-", "anthropic/"))
                    oauth_token   = _load_claude_oauth_token() if _is_claude else ""

                    if oauth_token:
                        # Anthropic SDK Streaming
                        import anthropic as _anthropic
                        client = _anthropic.AsyncAnthropic(
                            api_key="",
                            auth_token=oauth_token,
                            default_headers={
                                "anthropic-beta": "claude-code-20250219,oauth-2025-04-20,fine-grained-tool-streaming-2025-05-14",
                                "user-agent":     "claude-cli/2.1.62",
                                "x-app":          "cli",
                            },
                        )
                        system_msg = ""
                        raw: list[dict] = []
                        for m in messages:
                            if m.get("role") == "system":
                                system_msg = m.get("content", "")
                            else:
                                raw.append({"role": m["role"], "content": m.get("content") or ""})
                        # Consecutive gleiche Rollen mergen
                        filtered: list[dict] = []
                        for m in raw:
                            if filtered and filtered[-1]["role"] == m["role"]:
                                filtered[-1]["content"] += "\n\n" + m["content"]
                            else:
                                filtered.append(dict(m))

                        model = _model_name
                        for prefix in ("openai/", "anthropic/", "claude/"):
                            if model.startswith(prefix):
                                model = model[len(prefix):]
                                break
                        if not model.startswith("claude-"):
                            model = "claude-haiku-4-5-20251001"

                        oauth_system = [
                            {"type": "text", "text": "You are Claude Code, Anthropic's official CLI for Claude."},
                        ]
                        if system_msg:
                            oauth_system.append({"type": "text", "text": system_msg})

                        kwargs: dict = {
                            "model":      model,
                            "max_tokens": boss_cfg.llm.max_tokens,
                            "messages":   filtered,
                            "system":     oauth_system,
                        }
                        if litellm_tools:
                            kwargs["tools"] = [
                                {
                                    "name":         t["function"]["name"],
                                    "description":  t["function"].get("description", ""),
                                    "input_schema": t["function"].get("parameters", {"type": "object", "properties": {}}),
                                }
                                for t in litellm_tools
                            ]

                        # Agentic Tool-Loop für OAuth-Streaming
                        last_tool_signature: tuple[str, ...] | None = None
                        repeated_tool_signature_count = 0
                        for _round in range(boss_cfg.max_tool_rounds):
                            async with client.messages.stream(**kwargs) as stream:
                                async for text in stream.text_stream:
                                    full_response += text
                                    streamed_any   = True
                                    yield f"data: {_json.dumps({'text': text})}\n\n"
                                final_msg = await stream.get_final_message()
                            _usage["rounds"] += 1
                            if hasattr(final_msg, "usage"):
                                _usage["input"]       += getattr(final_msg.usage, "input_tokens", 0)
                                _usage["output"]      += getattr(final_msg.usage, "output_tokens", 0)
                                _usage["cache_write"] += getattr(final_msg.usage, "cache_creation_input_tokens", 0)
                                _usage["cache_read"]  += getattr(final_msg.usage, "cache_read_input_tokens", 0)

                            tool_use_blocks = [b for b in final_msg.content if b.type == "tool_use"]
                            if not tool_use_blocks:
                                break
                            def _oauth_sig_args(name: str, inp: dict) -> str:
                                if name == "file_write":
                                    inp = {k: v for k, v in inp.items() if k != "content"}
                                return _json.dumps(inp, ensure_ascii=False, sort_keys=True)
                            signature = tuple(
                                f"{block.name}:{_oauth_sig_args(block.name, block.input)}"
                                for block in tool_use_blocks
                            )
                            if signature and signature == last_tool_signature:
                                repeated_tool_signature_count += 1
                            else:
                                repeated_tool_signature_count = 0
                            last_tool_signature = signature
                            if repeated_tool_signature_count >= 3:
                                kwargs_final = dict(kwargs)
                                kwargs_final.pop("tools", None)
                                kwargs_final["messages"] = filtered + [
                                    {
                                        "role": "user",
                                        "content": [
                                            {
                                                "type": "text",
                                                "text": "[System: Wiederholte Tool-Signatur erkannt — kein weiterer Fortschritt möglich. Berichte: 1) Was wurde abgeschlossen? 2) Was ist gescheitert und warum? Rufe keine weiteren Tools auf.]",
                                            }
                                        ],
                                    }
                                ]
                                async with client.messages.stream(**kwargs_final) as stream:
                                    async for text in stream.text_stream:
                                        full_response += text
                                        streamed_any = True
                                        yield f"data: {_json.dumps({'text': text})}\n\n"
                                    _fm = await stream.get_final_message()
                                _usage["rounds"] += 1
                                if hasattr(_fm, "usage"):
                                    _usage["input"]       += getattr(_fm.usage, "input_tokens", 0)
                                    _usage["output"]      += getattr(_fm.usage, "output_tokens", 0)
                                    _usage["cache_write"] += getattr(_fm.usage, "cache_creation_input_tokens", 0)
                                    _usage["cache_read"]  += getattr(_fm.usage, "cache_read_input_tokens", 0)
                                break
                            if _round == boss_cfg.max_tool_rounds - 2:
                                # Vorletzter Durchlauf — Agent soll jetzt abschließen
                                filtered.append({"role": "user", "content": [{"type": "text", "text": "[System: Letzte Tool-Runde — fasse ab was abgeschlossen wurde, was nicht geklappt hat und warum.]"}]})
                                kwargs["messages"] = filtered

                            tool_results = []
                            any_tool_error = False
                            for block in tool_use_blocks:
                                yield f"data: {_json.dumps({'tool_call': block.name, 'tool_input': block.input})}\n\n"
                                tool = self._resolve_allowed_tool(boss_cfg, block.name, execution_mode, user_text=content)
                                if tool:
                                    try:
                                        result = await self._execute_tool(
                                            tool,
                                            boss_cfg=boss_cfg,
                                            project_id=project_id,
                                            tool_name=block.name,
                                            tool_input=block.input,
                                        )
                                    except Exception as te:
                                        result = {"error": str(te)}
                                else:
                                    result = {"error": f"Tool '{block.name}' ist in diesem Modus nicht erlaubt"}
                                if isinstance(result, dict) and "error" in result:
                                    any_tool_error = True
                                result_str = _truncate_tool_result(_json.dumps(result, ensure_ascii=False))
                                tool_results.append({
                                    "type":        "tool_result",
                                    "tool_use_id": block.id,
                                    "content":     result_str,
                                })
                            # Loop-Counter zurücksetzen wenn ein Tool-Fehler aufgetreten ist
                            # (verhindert fälschliche Loop-Erkennung bei Retry nach Fehler)
                            if any_tool_error:
                                repeated_tool_signature_count = 0

                            asst_content = []
                            for b in final_msg.content:
                                if b.type == "text":
                                    asst_content.append({"type": "text", "text": b.text})
                                elif b.type == "tool_use":
                                    asst_content.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
                            filtered.append({"role": "assistant", "content": asst_content})
                            filtered.append({"role": "user",      "content": tool_results})
                            kwargs["messages"] = filtered
                        else:
                            # Loop normal beendet (kein break nach letzter Runde)
                            # Finaler Call ohne Tools damit der Agent abschließen kann
                            kwargs_final = dict(kwargs)
                            kwargs_final.pop("tools", None)
                            kwargs_final["messages"] = filtered + [{"role": "user", "content": [{"type": "text", "text": "[System: Fasse ab was abgeschlossen wurde, was nicht geklappt hat und warum.]"}]}]
                            async with client.messages.stream(**kwargs_final) as stream:
                                async for text in stream.text_stream:
                                    full_response += text
                                    streamed_any   = True
                                    yield f"data: {_json.dumps({'text': text})}\n\n"
                                _fm2 = await stream.get_final_message()
                            _usage["rounds"] += 1
                            if hasattr(_fm2, "usage"):
                                _usage["input"]       += getattr(_fm2.usage, "input_tokens", 0)
                                _usage["output"]      += getattr(_fm2.usage, "output_tokens", 0)
                                _usage["cache_write"] += getattr(_fm2.usage, "cache_creation_input_tokens", 0)
                                _usage["cache_read"]  += getattr(_fm2.usage, "cache_read_input_tokens", 0)

                    else:
                        # litellm Streaming (Ollama / OpenAI) mit Tool-Loop
                        import json as _json2
                        model, api_base = self._resolve_model(_model_name, boss_cfg.llm.ollama_base_url)
                        loop_messages = list(messages)
                        last_tool_signature: tuple[str, ...] | None = None
                        repeated_tool_signature_count = 0
                        _tools_disabled = False  # wird gesetzt wenn Modell keine Tools unterstützt

                        for _round in range(boss_cfg.max_tool_rounds):
                            kwargs = {
                                "model":       model,
                                "messages":    loop_messages,
                                "temperature": boss_cfg.llm.temperature,
                                "max_tokens":  boss_cfg.llm.max_tokens,
                                "stream":      True,
                            }
                            if api_base:
                                kwargs["api_base"] = api_base
                            if litellm_tools and not _tools_disabled:
                                kwargs["tools"] = litellm_tools

                            round_text = ""
                            accumulated_tcs: dict = {}  # index → {id, name, arguments}

                            try:
                              _stream = await litellm.acompletion(**kwargs, drop_params=True)
                            except Exception as _e:
                                if "does not support tools" in str(_e) and "tools" in kwargs:
                                    _tools_disabled = True
                                    kwargs.pop("tools")
                                    _stream = await litellm.acompletion(**kwargs, drop_params=True)
                                else:
                                    raise

                            async for chunk in _stream:
                                # litellm liefert usage im letzten Chunk (stream_options)
                                if getattr(chunk, "usage", None):
                                    _usage["input"]       += getattr(chunk.usage, "prompt_tokens", 0)
                                    _usage["output"]      += getattr(chunk.usage, "completion_tokens", 0)
                                    _usage["cache_write"] += getattr(chunk.usage, "cache_creation_input_tokens", 0)
                                    _usage["cache_read"]  += getattr(chunk.usage, "cache_read_input_tokens", 0)
                                choice = chunk.choices[0]
                                delta  = choice.delta
                                if delta.content:
                                    round_text     += delta.content
                                    full_response  += delta.content
                                    streamed_any    = True
                                    yield f"data: {_json.dumps({'text': delta.content})}\n\n"
                                # Tool-Call-Deltas akkumulieren
                                if getattr(delta, "tool_calls", None):
                                    for tc_d in delta.tool_calls:
                                        idx = tc_d.index
                                        if idx not in accumulated_tcs:
                                            accumulated_tcs[idx] = {"id": "", "name": "", "arguments": ""}
                                        if tc_d.id:
                                            accumulated_tcs[idx]["id"] = tc_d.id
                                        fn = getattr(tc_d, "function", None)
                                        if fn:
                                            if getattr(fn, "name", None):
                                                accumulated_tcs[idx]["name"] += fn.name
                                            if getattr(fn, "arguments", None):
                                                accumulated_tcs[idx]["arguments"] += fn.arguments

                            # Kein Tool-Call → fertig
                            if not accumulated_tcs:
                                break

                            # Assistant-Nachricht mit tool_calls in History
                            tc_list = [accumulated_tcs[i] for i in sorted(accumulated_tcs)]
                            def _litellm_sig_args(name: str, raw: str) -> str:
                                if name == "file_write":
                                    try:
                                        import json as _jj
                                        d = _jj.loads(raw)
                                        d.pop("content", None)
                                        return _jj.dumps(d, sort_keys=True)
                                    except Exception:
                                        pass
                                return raw
                            signature = tuple(
                                f"{tc['name']}:{_litellm_sig_args(tc['name'], tc['arguments'])}"
                                for tc in tc_list
                            )
                            if signature and signature == last_tool_signature:
                                repeated_tool_signature_count += 1
                            else:
                                repeated_tool_signature_count = 0
                            last_tool_signature = signature
                            if repeated_tool_signature_count >= 3:
                                loop_messages.append({
                                    "role": "user",
                                    "content": "[System: Wiederholte Tool-Signatur erkannt — kein weiterer Fortschritt möglich. Berichte: 1) Was wurde abgeschlossen? 2) Was ist gescheitert und warum? Rufe keine weiteren Tools auf.]",
                                })
                                final_resp = await self._llm_call_single(_model_name, boss_cfg, loop_messages, tools=None)
                                final_text = final_resp.choices[0].message.content or ""
                                if final_text:
                                    full_response += final_text
                                    streamed_any = True
                                    yield f"data: {_json.dumps({'text': final_text})}\n\n"
                                break

                            def _safe_args(raw: str) -> str:
                                """Stellt sicher dass arguments valides JSON ist."""
                                if not raw:
                                    return "{}"
                                try:
                                    _json2.loads(raw)
                                    return raw
                                except _json2.JSONDecodeError:
                                    # Versuche zu reparieren: doppelte Objekte zusammenführen
                                    try:
                                        parts = [p for p in raw.split("}{") if p]
                                        if len(parts) > 1:
                                            merged = {}
                                            for p in parts:
                                                s = p if p.startswith("{") else "{" + p
                                                s = s if s.endswith("}") else s + "}"
                                                merged.update(_json2.loads(s))
                                            return _json2.dumps(merged)
                                    except Exception:
                                        pass
                                    return "{}"

                            # Assistant-Nachricht ohne tool_calls einfügen (plain text) +
                            # Tools ausführen und Ergebnisse als user-Nachricht — umgeht
                            # litellm Ollama-Transformation die mit tool_call-History crasht
                            if round_text:
                                loop_messages.append({"role": "assistant", "content": round_text})

                            tool_results_text = []
                            for tc in tc_list:
                                yield f"data: {_json.dumps({'tool_call': tc['name']})}\n\n"
                                tool = self._resolve_allowed_tool(boss_cfg, tc["name"], execution_mode)
                                try:
                                    tool_input = _json2.loads(_safe_args(tc["arguments"]))
                                    result = await self._execute_tool(
                                        tool,
                                        boss_cfg=boss_cfg,
                                        project_id=project_id,
                                        tool_name=tc["name"],
                                        tool_input=tool_input,
                                    )
                                except Exception as te:
                                    result = f"Tool-Fehler: {te}"
                                tool_results_text.append(
                                    f"[Tool: {tc['name']}]\n{_json2.dumps(result, ensure_ascii=False) if isinstance(result, dict) else str(result)}"
                                )

                            loop_messages.append({
                                "role":    "user",
                                "content": "Tool-Ergebnisse:\n" + "\n\n".join(tool_results_text),
                            })

                    break  # Erfolgreich — kein weiterer Failover nötig

                except Exception as model_exc:
                    can_failover = (
                        not streamed_any
                        and _attempt_idx < len(models_to_try) - 1
                        and _should_failover(model_exc)
                    )
                    if can_failover:
                        next_model = models_to_try[_attempt_idx + 1]
                        logger.warning(
                            "Streaming-Failover: '%s' → '%s' (%s)",
                            _model_name, next_model, str(model_exc)[:80],
                        )
                        yield f"data: {_json.dumps({'info': f'Modell nicht verfügbar, wechsle zu {next_model}…'})}\n\n"
                        continue
                    raise

            # Antwort in Session speichern (mit echten Token-Counts aus API)
            _stream_meta: dict = {}
            if _usage.get("input") or _usage.get("output"):
                _stream_meta = {
                    "model":              boss_cfg.llm.model,
                    "input_tokens":       _usage.get("input",       0),
                    "output_tokens":      _usage.get("output",      0),
                    "cache_write_tokens": _usage.get("cache_write", 0),
                    "cache_read_tokens":  _usage.get("cache_read",  0),
                }
            await self._sessions.append(
                project_id, MessageRole.ASSISTANT,
                full_response, agent_id=boss_cfg.id,
                **_stream_meta,
            )
            total_tokens = _usage.get("input", 0) + _usage.get("output", 0)
            if total_tokens > 0 and _tool_reg._rate_limiter is not None:
                _tool_reg._rate_limiter.track_token_usage(boss_cfg.id, total_tokens)
            yield f"data: {_json.dumps({'done': True, 'session_id': None, 'usage': _usage})}\n\n"

        except Exception as e:
            err_str = str(e).lower()
            if "prompt is too long" in err_str or "maximum context length" in err_str or "context_length_exceeded" in err_str:
                logger.warning(
                    "Kontext zu lang für Projekt '%s' — Session wird zurückgesetzt. Fehler: %s",
                    project_id, e,
                )
                await self._sessions.new_session(project_id)
                yield f"data: {_json.dumps({'error': 'Der Konversationsverlauf war zu lang für das Sprachmodell. Die Session wurde automatisch zurückgesetzt — bitte wiederhole deine letzte Nachricht.'})}\n\n"
            else:
                logger.error("Streaming-Fehler: %s", e)
                if user_msg_saved:
                    await self._sessions.pop_last(project_id)
                yield f"data: {_json.dumps({'error': str(e)})}\n\n"

    async def _tool_loop(
        self,
        boss_cfg:    AgentConfig,
        project_id:  str,
        project_cfg: ProjectConfig,
        messages:    list[dict],
        response,
        max_rounds:  int | None = None,
        execution_mode: str | None = None,
    ) -> tuple[str, list[str]]:
        """
        Agentic Loop: LLM-Antwort → Tool-Calls ausführen → Ergebnisse einbauen → wiederholen.
        Dispatch-Tasks werden parallel ausgeführt, andere Tools sequentiell.
        Max. max_rounds Runden um Endlosschleifen zu vermeiden.
        Gibt (finale Antwort, beteiligte Worker-IDs) zurück.
        """
        # user_text für on-demand Tool-Filterung aus letzter User-Message
        _last_user = next(
            (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
            "",
        )
        # Im _tool_loop immer alle Tools (Kategorien wurden ggf. schon per request_tools nachgeladen)
        boss_tools = self._allowed_tools(boss_cfg, execution_mode, user_text=_last_user)
        litellm_tools: list[dict] = self._reg.as_litellm_tools(boss_tools) if boss_tools else []
        _loaded_categories: set[str] = set()  # Tracking für On-Demand-Kategorien
        current_messages = list(messages)
        workers_used: list[str] = []
        last_signature: tuple[str, ...] | None = None
        repeated_signature_count = 0
        max_rounds = max_rounds or boss_cfg.max_tool_rounds

        for _round in range(max_rounds):
            tool_calls = getattr(response.choices[0].message, "tool_calls", None)
            if not tool_calls:
                return response.choices[0].message.content or "", workers_used

            signature = self._tool_call_signature(tool_calls)
            if signature and signature == last_signature:
                repeated_signature_count += 1
            else:
                repeated_signature_count = 0
            last_signature = signature

            if repeated_signature_count >= 2:
                logger.warning(
                    "Tool-Loop: wiederholte Tool-Signatur erkannt (%s) — erzwinge Abschluss",
                    ", ".join(signature[:3])[:180],
                )
                try:
                    final = await self._finalize_tool_loop_response(
                        boss_cfg,
                        current_messages,
                        reason="wiederholte Tool-Signatur",
                        execution_mode=execution_mode,
                    )
                    return final.choices[0].message.content or "", workers_used
                except Exception as e:
                    return f"[Fehler] Konnte keine Antwort erzeugen: {e}", workers_used

            # Letzte Runde: kein weiteres Tool-Calling → Final-Antwort erzwingen
            if _round == max_rounds - 1:
                logger.warning("Tool-Loop: max_rounds=%d erreicht — erzwinge Textantwort", max_rounds)
                try:
                    from .tool_registry import _notify as _tr_notify
                    _tr_notify(project_id, "agent_warning",
                               f"Tool-Loop Limit erreicht",
                               f"Agent hat {max_rounds} Runden durchlaufen — Antwort wird erzwungen.",
                               link=f"/chat/{project_id}")
                except Exception:
                    pass
                try:
                    final = await self._finalize_tool_loop_response(
                        boss_cfg,
                        current_messages,
                        reason=f"max_rounds={max_rounds}",
                        execution_mode=execution_mode,
                    )
                    return final.choices[0].message.content or "", workers_used
                except Exception as e:
                    return f"[Fehler] Konnte keine Antwort erzeugen: {e}", workers_used

            # Assistant-Message mit Tool-Calls in History aufnehmen
            current_messages.append({
                "role": "assistant",
                "tool_calls": [
                    {
                     "id": tc.id,
                     "item_id": getattr(tc, "item_id", tc.id),
                     "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in tool_calls
                ],
            })

            # dispatch_task separat → paralleles Worker-Dispatch
            # request_tools separat → on-demand Tool-Kategorien nachladen
            dispatch_tcs     = [tc for tc in tool_calls if tc.function.name == "dispatch_task"]
            request_tool_tcs = [tc for tc in tool_calls if tc.function.name == "request_tools"]
            other_tcs        = [tc for tc in tool_calls if tc.function.name not in ("dispatch_task", "request_tools")]

            # On-Demand Tool-Kategorien nachladen
            for tc in request_tool_tcs:
                try:
                    args       = json.loads(tc.function.arguments)
                    categories = args.get("categories", [])
                    new_cats   = [c for c in categories if c not in _loaded_categories]
                    if new_cats:
                        new_schemas = self._category_tools_schema(boss_cfg, execution_mode, new_cats)
                        existing    = {t["function"]["name"] for t in litellm_tools}
                        added = [s for s in new_schemas if s["function"]["name"] not in existing]
                        litellm_tools.extend(added)
                        _loaded_categories.update(new_cats)
                        logger.info(
                            "request_tools: +%d Tools (Kategorien: %s, Agent: %s)",
                            len(added), new_cats, boss_cfg.id,
                        )
                    tool_results[tc.id] = json.dumps({
                        "ok": True,
                        "categories": categories,
                        "tools_added": len([c for c in categories if c not in (_loaded_categories - set(new_cats))]),
                        "note": "Du kannst die geladenen Tools jetzt direkt verwenden.",
                    }, ensure_ascii=False)
                except Exception as e:
                    tool_results[tc.id] = f"[Fehler] request_tools: {e}"

            tool_results: dict[str, str] = {}  # call_id → content

            if dispatch_tcs:
                dispatches = self._parse_dispatch_calls(dispatch_tcs)
                results = await self._dispatch_parallel(project_cfg, dispatches, context="")
                for res in results:
                    call_id = next(
                        (tc.id for tc in dispatch_tcs
                         if json.loads(tc.function.arguments).get("worker_id") == res.worker_id),
                        "unknown"
                    )
                    content = res.result if res.success else f"[Fehler] {res.error}"
                    tool_results[call_id] = content
                    if res.worker_id not in workers_used:
                        workers_used.append(res.worker_id)

            for tc in other_tcs:
                tool = self._resolve_allowed_tool(boss_cfg, tc.function.name, execution_mode)
                if tool is None:
                    tool_results[tc.id] = f"[Fehler] Tool in diesem Modus nicht erlaubt: {tc.function.name}"
                    continue
                try:
                    args = json.loads(tc.function.arguments)
                    result = await self._execute_tool(
                        tool,
                        boss_cfg=boss_cfg,
                        project_id=project_id,
                        tool_name=tc.function.name,
                        tool_input=args,
                    )
                    tool_results[tc.id] = _truncate_tool_result(json.dumps(result, ensure_ascii=False))
                except Exception as e:
                    logger.error("Tool '%s' fehlgeschlagen: %s", tc.function.name, e)
                    tool_results[tc.id] = f"[Fehler] {e}"

            # Tool-Results in Messages einbauen
            for tc in tool_calls:
                current_messages.append({
                    "role":         "tool",
                    "tool_call_id": tc.id,
                    "content":      tool_results.get(tc.id, ""),
                })

            # Nächste LLM-Runde
            try:
                response = await self._llm_call(boss_cfg, current_messages, litellm_tools)
            except Exception as e:
                logger.error("LLM-Fehler in Tool-Loop: %s", e)
                return f"[Fehler] LLM nicht erreichbar: {e}", workers_used

        return response.choices[0].message.content or "", workers_used

    def _parse_dispatch_calls(self, tool_calls: list) -> list[dict]:
        """Extrahiert dispatch_task-Aufrufe aus LLM Tool-Calls."""
        dispatches = []
        for tc in tool_calls:
            if tc.function.name != "dispatch_task":
                continue
            try:
                args = json.loads(tc.function.arguments)
                dispatches.append({
                    "call_id":   tc.id,
                    "worker_id": args["worker_id"],
                    "task":      args["task"],
                    "context":   args.get("context", ""),
                })
            except (json.JSONDecodeError, KeyError) as e:
                logger.warning("Ungültiger dispatch_task-Aufruf: %s", e)
        return dispatches

    async def _dispatch_parallel(
        self,
        project_cfg: ProjectConfig,
        dispatches:  list[dict],
        context:     str,
    ) -> list[DispatchResult]:
        """
        Alle Tasks parallel ausführen (AG2: paralleles Dispatching für Swarm).
        Nur Agenten die dem Projekt zugewiesen sind dürfen gespawnt werden (AG5).
        """
        allowed_workers = set(project_cfg.agents.workers)
        tasks = []
        for d in dispatches:
            if d["worker_id"] not in allowed_workers:
                logger.warning(
                    "dispatch_task für '%s' abgelehnt — nicht im Projekt", d["worker_id"]
                )
                tasks.append(asyncio.coroutine(lambda d=d: DispatchResult(
                    worker_id=d["worker_id"], task=d["task"],
                    result="", success=False,
                    error="Agent nicht dem Projekt zugewiesen",
                ))())
                continue
            tasks.append(self._run_worker_task(d))

        return list(await asyncio.gather(*tasks, return_exceptions=False))

    async def _run_worker_task(self, dispatch: dict) -> DispatchResult:
        """Einen Worker-Agenten mit einem Task beauftragen."""
        worker_id = dispatch["worker_id"]
        task      = dispatch["task"]
        context   = dispatch.get("context", "")

        worker_cfg = self._discovery.get(worker_id)
        if not worker_cfg:
            return DispatchResult(
                worker_id=worker_id, task=task, result="",
                success=False, error=f"Agent '{worker_id}' nicht in Discovery"
            )

        logger.info("Dispatche Task an %s: %s", worker_id, task[:60])

        # Worker-LLM-Aufruf (kein Tool-Calling für Worker — nur ausführen)
        messages = [
            {"role": "system", "content": f"Du bist {worker_cfg.identity}. Erledige den folgenden Task präzise und knapp."},
        ]
        if context:
            messages.append({"role": "user", "content": f"Kontext: {context}"})
        messages.append({"role": "user", "content": task})

        try:
            response = await self._llm_call(worker_cfg, messages, tools=None)
            result = response.choices[0].message.content or ""
            return DispatchResult(worker_id=worker_id, task=task, result=result)
        except Exception as e:
            logger.error("Worker '%s' LLM-Fehler: %s", worker_id, e)
            return DispatchResult(
                worker_id=worker_id, task=task, result="",
                success=False, error=str(e)
            )

    async def _synthesize(
        self,
        boss_cfg:   AgentConfig,
        messages:   list[dict],
        tool_calls: list,
        results:    list[DispatchResult],
    ) -> str:
        """Boss fasst Worker-Ergebnisse zur finalen Antwort zusammen."""
        # Tool-Results als Messages anhängen
        follow_up = list(messages)
        follow_up.append({
            "role": "assistant",
            "tool_calls": [
                {
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.function.name,
                        "arguments": tc.function.arguments,
                    },
                }
                for tc in tool_calls
            ],
        })
        for result in results:
            # Tool-Result für den passenden Call
            call_id = next(
                (tc.id for tc in tool_calls
                 if json.loads(tc.function.arguments).get("worker_id") == result.worker_id),
                "unknown"
            )
            content = result.result if result.success else f"[Fehler] {result.error}"
            follow_up.append({
                "role":         "tool",
                "tool_call_id": call_id,
                "content":      content,
            })

        try:
            response = await self._llm_call(boss_cfg, follow_up, tools=None)
            return response.choices[0].message.content or ""
        except Exception as e:
            logger.error("Synthese-LLM-Fehler: %s", e)
            # Fallback: Ergebnisse direkt zusammenfassen
            lines = [f"**{r.worker_id}**: {r.result}" for r in results if r.success]
            return "\n".join(lines)
