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
        self._discovery = discovery
        self._runtime   = runtime
        self._sessions  = sessions
        self._reg       = tool_reg or default_registry

    # ------------------------------------------------------------------ public

    async def handle_message(
        self,
        project_id:  str,
        project_cfg: ProjectConfig,
        content:     str,
        sender:      str = "user",
    ) -> str:
        """
        Hauptpfad: User-Nachricht → Boss-Agent → Antwort.
        Gibt den finalen Text zurück.
        """
        # 1. Nachricht in Session speichern
        self._sessions.append(project_id, MessageRole.USER, content)

        # 2. Boss-Agent-Config holen
        boss_cfg = self._discovery.get(project_cfg.agents.boss)
        if not boss_cfg:
            return f"[Fehler] Boss-Agent '{project_cfg.agents.boss}' nicht gefunden."

        # 3. System-Prompt aufbauen (Soul + Skills)
        system_prompt = self._build_system_prompt(boss_cfg, content)

        # 4. Session-Kontext als LLM-Messages
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
            return f"[Fehler] LLM nicht erreichbar: {e}"

        # 7. Tool-Calls verarbeiten (Agentic Loop mit max. 5 Runden)
        final_response = response.choices[0].message.content or ""
        tool_calls = getattr(response.choices[0].message, "tool_calls", None)

        if tool_calls:
            final_response = await self._tool_loop(
                boss_cfg, project_id, project_cfg, messages, response
            )

        # 8. Antwort in Session speichern
        self._sessions.append(
            project_id, MessageRole.ASSISTANT,
            final_response, agent_id=boss_cfg.id
        )

        return final_response

    # ----------------------------------------------------------------- private

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

        return await litellm.acompletion(**kwargs)

    async def _tool_loop(
        self,
        boss_cfg:    AgentConfig,
        project_id:  str,
        project_cfg: ProjectConfig,
        messages:    list[dict],
        response,
        max_rounds:  int = 5,
    ) -> str:
        """
        Agentic Loop: LLM-Antwort → Tool-Calls ausführen → Ergebnisse einbauen → wiederholen.
        Dispatch-Tasks werden parallel ausgeführt, andere Tools sequentiell.
        Max. max_rounds Runden um Endlosschleifen zu vermeiden.
        """
        boss_tools = self._reg.tools_for_agent(boss_cfg.tools)
        litellm_tools = self._reg.as_litellm_tools(boss_tools) if boss_tools else None
        current_messages = list(messages)

        for _round in range(max_rounds):
            tool_calls = getattr(response.choices[0].message, "tool_calls", None)
            if not tool_calls:
                return response.choices[0].message.content or ""

            # Letzte Runde: kein weiteres Tool-Calling → Final-Antwort erzwingen
            if _round == max_rounds - 1:
                logger.warning("Tool-Loop: max_rounds=%d erreicht — erzwinge Textantwort", max_rounds)
                try:
                    final = await self._llm_call(boss_cfg, current_messages, tools=None)
                    return final.choices[0].message.content or ""
                except Exception as e:
                    return f"[Fehler] Konnte keine Antwort erzeugen: {e}"

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
                return f"[Fehler] LLM nicht erreichbar: {e}"

        return response.choices[0].message.content or ""

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
