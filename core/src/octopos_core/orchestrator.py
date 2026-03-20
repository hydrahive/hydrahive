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
"""

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import litellm

from .agent_config import AgentConfig
from .agent_discovery import AgentDiscovery
from .agent_runtime import AgentRuntime
from .project_config import ProjectConfig
from .session_manager import MessageRole, SessionManager
from .skill_loader import load_skills, select_skills, skills_to_system_prompt
from .tool_registry import ToolRegistry, registry as default_registry

logger = logging.getLogger(__name__)

litellm.drop_params = True   # Unbekannte LLM-Parameter ignorieren statt crashen


@dataclass
class DispatchResult:
    worker_id: str
    task:      str
    result:    str
    success:   bool = True
    error:     str | None = None



def _load_claude_oauth_token() -> str:
    """
    Laedt Claude OAuth Token falls vorhanden.
    Token wird in LLM-Config gesetzt (sk-ant-oat01-...).
    """
    from pathlib import Path as _Path
    token_file = _Path("/etc/octopos/claude_oauth_token")
    try:
        token = token_file.read_text(encoding="utf-8").strip()
        return token if token.startswith("sk-ant-oat01-") else ""
    except OSError:
        return ""



async def _llm_with_retry(coro_factory, max_attempts: int = 3, base_delay: float = 1.0):
    """
    Retry-Wrapper fuer LLM-Calls.
    - 429 Rate-Limit: sofort retry mit Backoff
    - 5xx Server-Fehler: retry
    - 401/403 Auth: kein Retry
    - Timeout: retry
    Exponential Backoff mit 10% Jitter, max 30s.
    """
    import random as _random

    last_exc = None
    for attempt in range(max_attempts):
        try:
            return await coro_factory()
        except Exception as e:
            last_exc = e
            err_str = str(e).lower()

            # Auth-Fehler → kein Retry
            if any(x in err_str for x in ["401", "403", "unauthorized", "forbidden", "authentication"]):
                raise

            # Letzter Versuch → aufgeben
            if attempt == max_attempts - 1:
                raise

            # Backoff berechnen: 1s, 2s, 4s... max 30s + Jitter
            delay = min(base_delay * (2 ** attempt), 30.0)
            delay *= (1 + _random.uniform(-0.1, 0.1))  # 10% Jitter

            logger.warning(
                "LLM-Fehler (Versuch %d/%d): %s — retry in %.1fs",
                attempt + 1, max_attempts, str(e)[:80], delay
            )
            await asyncio.sleep(delay)

    raise last_exc


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
        """Verarbeitet Nachrichten sequenziell — kein paralleler Orchestrator-Zustand."""
        queue = self._get_queue(project_id)
        try:
            while True:
                future, project_cfg, content, sender = await queue.get()
                try:
                    result = await self._handle_message_impl(
                        project_id, project_cfg, content, sender
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

    async def handle_message(
        self,
        project_id:  str,
        project_cfg: "ProjectConfig",
        content:     str,
        sender:      str = "user",
    ) -> tuple[str, list[str]]:
        """
        Oeffentlicher Einstiegspunkt — serialisiert ueber asyncio.Queue.
        Mehrere parallele Aufrufe ans gleiche Projekt werden sequenziell abgearbeitet.
        """
        await self._ensure_worker(project_id)
        future: asyncio.Future = asyncio.get_event_loop().create_future()
        await self._get_queue(project_id).put((future, project_cfg, content, sender))
        return await future

    async def _handle_message_impl(self, project_id: str, project_cfg, content: str, sender: str):
        """
        Hauptpfad: User-Nachricht → Boss-Agent → Antwort.
        Gibt (finaler Text, beteiligte Worker-IDs) zurück.
        """
        workers_used: list[str] = []

        # 1. Nachricht in Session speichern
        self._sessions.append(project_id, MessageRole.USER, content)

        # 2. Boss-Agent-Config holen
        boss_cfg = self._discovery.get(project_cfg.agents.boss)
        if not boss_cfg:
            return f"[Fehler] Boss-Agent '{project_cfg.agents.boss}' nicht gefunden.", []

        # 3. System-Prompt aufbauen (Soul + Skills)
        system_prompt = self._build_system_prompt(boss_cfg, content)

        # 4. Context kompaktieren wenn nötig (#74), dann LLM-Context holen
        await self._compact_if_needed(project_id, boss_cfg)
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(self._sessions.get_context(project_id, max_messages=40))

        # 5. Verfügbare Tools für Boss ermitteln
        boss_tools = self._reg.tools_for_agent(boss_cfg.tools)
        litellm_tools = self._reg.as_litellm_tools(boss_tools) if boss_tools else None

        # 6. LLM aufrufen
        try:
            response = await self._llm_call(boss_cfg, messages, litellm_tools)
        except Exception as e:
            logger.error("LLM-Fehler für Boss '%s': %s", boss_cfg.id, e)
            return f"[Fehler] LLM nicht erreichbar: {e}", []

        # 7. Tool-Calls verarbeiten (Agentic Loop mit max. 5 Runden)
        final_response = response.choices[0].message.content or ""
        tool_calls = getattr(response.choices[0].message, "tool_calls", None)

        if tool_calls:
            final_response, workers_used = await self._tool_loop(
                boss_cfg, project_id, project_cfg, messages, response
            )

        # 8. Antwort in Session speichern
        self._sessions.append(
            project_id, MessageRole.ASSISTANT,
            final_response, agent_id=boss_cfg.id
        )

        return final_response, workers_used

    # ----------------------------------------------------------------- private

    async def _compact_if_needed(
        self,
        project_id: str,
        boss_cfg: AgentConfig,
        token_threshold: int = 6000,
        keep_last: int = 10,
    ) -> None:
        """
        Context-Kompaktierung (#74): wenn Session zu gross wird, aelteren Kontext
        per LLM zusammenfassen und durch eine Summary-Message ersetzen.
        token_threshold: geschaetzte Tokens ab denen kompaktiert wird.
        """
        if self._sessions.estimated_tokens(project_id) < token_threshold:
            return

        session = self._sessions.get_active(project_id)
        if not session or len(session.messages) <= keep_last + 2:
            return

        to_summarize = session.messages[:-keep_last]
        history_text = "\n".join(
            f"{m.role.value.upper()}: {m.content[:500]}"
            for m in to_summarize
        )

        summary_prompt = [
            {"role": "system", "content": (
                "Fasse die folgende Konversation praegnant zusammen. "
                "Behalte alle wichtigen Fakten, Entscheidungen und Aufgaben. "
                "Antworte nur mit der Zusammenfassung, keine Einleitung."
            )},
            {"role": "user", "content": history_text},
        ]

        try:
            resp = await _llm_with_retry(lambda: litellm.acompletion(
                model=boss_cfg.llm.model,
                messages=summary_prompt,
                max_tokens=600,
            ))
            summary = resp.choices[0].message.content or ""
            if summary:
                self._sessions.compact(project_id, summary, keep_last=keep_last)
                logger.info(
                    "Context kompaktiert (Projekt: %s, ~%d Tokens → Summary)",
                    project_id, self._sessions.estimated_tokens(project_id),
                )
        except Exception as e:
            logger.warning("Context-Kompaktierung fehlgeschlagen: %s", e)

    def _build_system_prompt(self, boss_cfg: AgentConfig, user_text: str) -> str:
        parts = [f"Du bist {boss_cfg.identity}."]

        # Soul laden wenn vorhanden
        if boss_cfg.soul and boss_cfg.agent_dir:
            soul_path = boss_cfg.agent_dir / boss_cfg.soul
            if soul_path.exists():
                parts.append(soul_path.read_text(encoding="utf-8").strip())

        # QMD-Skills laden (scope=always immer, on-demand bei Keyword-Match)
        if boss_cfg.agent_dir:
            all_skills = load_skills(boss_cfg.agent_dir)
            active_skills = select_skills(all_skills, user_text)
            if active_skills:
                parts.append(skills_to_system_prompt(active_skills))

        return "\n\n".join(parts)

    @staticmethod
    def _resolve_model(model: str) -> tuple[str, str | None]:
        """
        Gibt (litellm_model, api_base) zurück.
        Provider-Prefix (z.B. anthropic/, openai/) → direkt weiterreichen.
        Claude/GPT-Modellnamen → passenden Provider-Prefix ergänzen.
        Kein Prefix, kein bekannter Cloud-Name → Ollama auf localhost.
        """
        if "/" in model:
            return model, None
        # Bekannte Cloud-Modell-Prefixe automatisch ergänzen
        if model.startswith(("claude-",)):
            return f"anthropic/{model}", None
        if model.startswith(("gpt-", "o1-", "o3-")):
            return f"openai/{model}", None
        # Kein Prefix → lokales Ollama-Modell
        return f"ollama/{model}", "http://localhost:11434"

    async def _llm_call(
        self,
        agent_cfg:   AgentConfig,
        messages:    list[dict],
        tools:       list[dict] | None,
    ):
        """
        LLM-Call mit automatischer Provider-Erkennung:
        - sk-ant-oat01-* Token → Anthropic SDK direkt mit OAuth-Header
        - Alle anderen         → litellm (Ollama, OpenAI API-Key, etc.)
        """
        model_name = agent_cfg.llm.model
        is_claude_model = model_name.startswith(("claude-", "anthropic/"))
        oauth_token = _load_claude_oauth_token() if is_claude_model else ""
        if oauth_token:
            return await self._anthropic_oauth_call(agent_cfg, messages, tools, oauth_token)

        model, api_base = self._resolve_model(agent_cfg.llm.model)
        kwargs: dict = {
            "model":       model,
            "messages":    messages,
            "temperature": agent_cfg.llm.temperature,
            "max_tokens":  agent_cfg.llm.max_tokens,
        }
        if api_base:
            kwargs["api_base"] = api_base
        if tools:
            kwargs["tools"]       = tools
            kwargs["tool_choice"] = "auto"

        return await _llm_with_retry(lambda: litellm.acompletion(**kwargs))

    async def _anthropic_oauth_call(
        self,
        agent_cfg: AgentConfig,
        messages:  list[dict],
        tools:     list[dict] | None,
        token:     str,
    ):
        """
        Direkter Anthropic SDK Call mit OAuth-Token (Claude Max Subscription).
        Setzt anthropic-beta: oauth-2025-04-20 Header wie OpenClaw.
        Gibt ein litellm-kompatibles Response-Objekt zurueck.
        """
        import anthropic as _anthropic
        from types import SimpleNamespace

        # api_key="" verhindert dass der SDK ANTHROPIC_API_KEY aus env liest
        # (SDK sendet sonst x-api-key UND Authorization: Bearer gleichzeitig)
        client = _anthropic.AsyncAnthropic(
            api_key="",
            auth_token=token,
            default_headers={"anthropic-beta": "oauth-2025-04-20"},
        )

        # System-Message extrahieren
        system_msg = ""
        filtered   = []
        for m in messages:
            if m.get("role") == "system":
                system_msg = m.get("content", "")
            else:
                filtered.append({"role": m["role"], "content": m.get("content", "")})

        # Modell-Name normalisieren (openai/claude-... → claude-...)
        model = agent_cfg.llm.model
        for prefix in ("openai/", "anthropic/", "claude/"):
            if model.startswith(prefix):
                model = model[len(prefix):]
                break
        if not model.startswith("claude-"):
            model = "claude-haiku-4-5"

        # Nur claude-haiku-4-5 funktioniert via OAuth (Stand 2026-03)
        # Alle anderen claude-* Modelle → Fallback auf Haiku
        if not model.startswith("claude-haiku"):
            logger.info("OAuth: %s nicht verfügbar, Fallback auf claude-haiku-4-5", model)
            model = "claude-haiku-4-5"

        kwargs: dict = {
            "model":       model,
            "max_tokens":  agent_cfg.llm.max_tokens,
            "messages":    filtered,
            "temperature": agent_cfg.llm.temperature,
        }
        if system_msg:
            kwargs["system"] = system_msg
        if tools:
            # Anthropic Tool-Format aus litellm-Format ableiten
            kwargs["tools"] = [
                {
                    "name":         t["function"]["name"],
                    "description":  t["function"].get("description", ""),
                    "input_schema": t["function"].get("parameters", {"type": "object", "properties": {}}),
                }
                for t in tools
            ]

        resp = await _llm_with_retry(lambda: client.messages.create(**kwargs))

        # Anthropic Response → litellm-kompatibles SimpleNamespace
        text = ""
        tool_calls = []
        for block in resp.content:
            if block.type == "text":
                text = block.text
            elif block.type == "tool_use":
                import json as _json
                tool_calls.append(SimpleNamespace(
                    id=block.id,
                    type="function",
                    function=SimpleNamespace(
                        name=block.name,
                        arguments=_json.dumps(block.input),
                    )
                ))

        message = SimpleNamespace(
            role="assistant",
            content=text,
            tool_calls=tool_calls if tool_calls else None,
        )
        choice  = SimpleNamespace(message=message, finish_reason=resp.stop_reason)
        return  SimpleNamespace(choices=[choice], model=model)


    async def handle_message_stream(
        self,
        project_id:  str,
        project_cfg,
        content:     str,
        sender:      str = "user",
    ):
        """
        Streaming-Version von handle_message.
        Yieldet SSE-Chunks: data: <text>\n\n
        Abschluss: data: [DONE]\n\n
        """
        import json as _json

        boss_id  = project_cfg.agents.boss
        boss_cfg = self._discovery.get(boss_id)
        if not boss_cfg:
            yield f"data: {_json.dumps({'error': f'Boss-Agent {boss_id} nicht gefunden'})}\n\n"
            return

        # Session + System-Prompt aufbauen (gleich wie handle_message)
        self._sessions.append(project_id, MessageRole.USER, content, agent_id=sender)
        system_prompt = self._build_system_prompt(boss_cfg, content)
        history       = self._sessions.get_context(project_id, max_messages=20)
        messages      = [{"role": "system", "content": system_prompt}] + history

        # Tool-Schema
        boss_tools    = self._reg.tools_for_agent(boss_cfg.tools)
        litellm_tools = self._reg.as_litellm_tools(boss_tools) if boss_tools else None

        try:
            full_response = ""
            _model_name   = boss_cfg.llm.model
            _is_claude    = _model_name.startswith(("claude-", "anthropic/"))
            oauth_token   = _load_claude_oauth_token() if _is_claude else ""

            if oauth_token:
                # Anthropic SDK Streaming
                import anthropic as _anthropic
                client = _anthropic.AsyncAnthropic(
                    api_key="",
                    default_headers={"anthropic-beta": "oauth-2025-04-20"},
                    auth_token=oauth_token,
                )
                system_msg = ""
                filtered   = []
                for m in messages:
                    if m.get("role") == "system":
                        system_msg = m.get("content", "")
                    else:
                        filtered.append({"role": m["role"], "content": m.get("content", "")})

                model = boss_cfg.llm.model
                for prefix in ("openai/", "anthropic/", "claude/"):
                    if model.startswith(prefix):
                        model = model[len(prefix):]
                        break
                if not model.startswith("claude-"):
                    model = "claude-haiku-4-5-20251001"

                kwargs: dict = {
                    "model":      model,
                    "max_tokens": boss_cfg.llm.max_tokens,
                    "messages":   filtered,
                }
                if system_msg:
                    kwargs["system"] = system_msg

                async with client.messages.stream(**kwargs) as stream:
                    async for text in stream.text_stream:
                        full_response += text
                        yield f"data: {_json.dumps({'text': text})}\n\n"

            else:
                # litellm Streaming (Ollama / OpenAI)
                model, api_base = self._resolve_model(boss_cfg.llm.model)
                kwargs = {
                    "model":       model,
                    "messages":    messages,
                    "temperature": boss_cfg.llm.temperature,
                    "max_tokens":  boss_cfg.llm.max_tokens,
                    "stream":      True,
                }
                if api_base:
                    kwargs["api_base"] = api_base

                async for chunk in await litellm.acompletion(**kwargs):
                    delta = chunk.choices[0].delta.content or ""
                    if delta:
                        full_response += delta
                        yield f"data: {_json.dumps({'text': delta})}\n\n"

            # Antwort in Session speichern
            self._sessions.append(
                project_id, MessageRole.ASSISTANT,
                full_response, agent_id=boss_cfg.id
            )
            yield f"data: {_json.dumps({'done': True, 'session_id': None})}\n\n"

        except Exception as e:
            logger.error("Streaming-Fehler: %s", e)
            yield f"data: {_json.dumps({'error': str(e)})}\n\n"

    async def _tool_loop(
        self,
        boss_cfg:    AgentConfig,
        project_id:  str,
        project_cfg: ProjectConfig,
        messages:    list[dict],
        response,
        max_rounds:  int = 5,
    ) -> tuple[str, list[str]]:
        """
        Agentic Loop: LLM-Antwort → Tool-Calls ausführen → Ergebnisse einbauen → wiederholen.
        Dispatch-Tasks werden parallel ausgeführt, andere Tools sequentiell.
        Max. max_rounds Runden um Endlosschleifen zu vermeiden.
        Gibt (finale Antwort, beteiligte Worker-IDs) zurück.
        """
        boss_tools = self._reg.tools_for_agent(boss_cfg.tools)
        litellm_tools = self._reg.as_litellm_tools(boss_tools) if boss_tools else None
        current_messages = list(messages)
        workers_used: list[str] = []

        for _round in range(max_rounds):
            tool_calls = getattr(response.choices[0].message, "tool_calls", None)
            if not tool_calls:
                return response.choices[0].message.content or "", workers_used

            # Letzte Runde: kein weiteres Tool-Calling → Final-Antwort erzwingen
            if _round == max_rounds - 1:
                logger.warning("Tool-Loop: max_rounds=%d erreicht — erzwinge Textantwort", max_rounds)
                try:
                    final = await self._llm_call(boss_cfg, current_messages, tools=None)
                    return final.choices[0].message.content or "", workers_used
                except Exception as e:
                    return f"[Fehler] Konnte keine Antwort erzeugen: {e}", workers_used

            # Assistant-Message mit Tool-Calls in History aufnehmen
            current_messages.append({
                "role": "assistant",
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in tool_calls
                ],
            })

            # dispatch_task separat → paralleles Worker-Dispatch
            dispatch_tcs = [tc for tc in tool_calls if tc.function.name == "dispatch_task"]
            other_tcs    = [tc for tc in tool_calls if tc.function.name != "dispatch_task"]

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
                tool = self._reg.get(tc.function.name)
                if tool is None:
                    tool_results[tc.id] = f"[Fehler] Unbekanntes Tool: {tc.function.name}"
                    continue
                try:
                    args = json.loads(tc.function.arguments)
                    result = await tool.execute(
                        agent_id=boss_cfg.id, project_id=project_id, **args
                    )
                    tool_results[tc.id] = json.dumps(result, ensure_ascii=False)
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
