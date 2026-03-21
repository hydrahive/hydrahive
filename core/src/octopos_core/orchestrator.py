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


def _load_openai_codex_token() -> dict | None:
    """
    Laedt OpenAI Codex OAuth Token (ChatGPT Plus/Pro).
    Gespeichert in /etc/octopos/openai_codex_token.json:
    {access_token, refresh_token, expires, account_id}
    """
    import json as _json
    from pathlib import Path as _Path
    token_file = _Path("/etc/octopos/openai_codex_token.json")
    try:
        data = _json.loads(token_file.read_text(encoding="utf-8"))
        if data.get("access_token") and data.get("account_id"):
            return data
    except OSError:
        pass
    return None



_FAILOVER_SIGNALS = [
    "402", "payment", "credit", "quota", "insufficient",
    "429", "rate_limit", "rate limit",
    "529", "overloaded", "capacity", "credit_balance",
    "your credit balance is too low",
    "exceeded your current quota",
    "this request would exceed",
    "billing",
]


def _should_failover(exc: Exception) -> bool:
    """True wenn der Fehler einen Modell-Wechsel rechtfertigt (Quota/Overload)."""
    err = str(exc).lower()
    return any(s in err for s in _FAILOVER_SIGNALS)


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

            # Quota/Rate-Limit erschöpft → kein Retry, sofort an Aufrufer weitergeben (Failover)
            if any(x in err_str for x in ["rate_limit", "rate limit", "429", "quota", "credit", "billing", "payment"]):
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
        self._queue_last_used: dict[str, float]         = {}
        self._queue_idle_timeout_s: float               = 600.0

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
                    future, project_cfg, content, sender = await asyncio.wait_for(
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
        project_cfg: ProjectConfig,
        content:     str,
        sender:      str | None = None,
    ) -> str:
        """
        Haupt-API für Chat/API.
        Verarbeitet Nachrichten pro Projekt streng sequenziell.
        """
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[str] = loop.create_future()
        q = self._get_queue(project_id)
        await q.put((fut, project_cfg, content, sender))
        await self._ensure_worker(project_id)
        return await fut

    async def _handle_message_impl(
        self,
        project_id:  str,
        project_cfg: ProjectConfig,
        content:     str,
        sender:      str | None = None,
    ) -> str:
        """
        Interne Implementierung (single-flight pro Projekt durch Queue).
        """
        # 0) Boss laden
        boss_id = project_cfg.agents.boss
        boss_cfg = self._discovery.load_agent_config(boss_id)

        # 1) Session laden + User-Nachricht speichern
        history = await self._sessions.load(project_id)
        await self._sessions.append(
            project_id,
            role=MessageRole.USER,
            content=content,
            sender=sender,
            agent_id=boss_id,
        )

        # 2) Soul + Skills
        soul_text = self._load_soul(boss_cfg)

        all_skills = load_skills(boss_cfg.skills_dir)
        active     = select_skills(all_skills, content)
        skills_txt = skills_to_system_prompt(active)

        system_prompt = self._build_system_prompt(
            boss_cfg=boss_cfg,
            project_cfg=project_cfg,
            soul_text=soul_text,
            skills_prompt=skills_txt,
        )

        # 3) Tools für Boss zusammenstellen (nur erlaubte)
        tools_for_llm = self._reg.openai_tools_for_agent(boss_cfg)

        # 4) Chat-Messages bauen (History + aktuelle User-Nachricht)
        messages = [{"role": "system", "content": system_prompt}]
        messages.extend(self._history_to_openai_messages(history))
        messages.append({"role": "user", "content": content})

        # 5) LLM-Loop: Tool-Calls ausführen bis finale Antwort
        final_answer = await self._run_llm_tool_loop(
            boss_cfg=boss_cfg,
            project_id=project_id,
            project_cfg=project_cfg,
            messages=messages,
            tools_for_llm=tools_for_llm,
        )

        # 6) Antwort persistieren
        await self._sessions.append(
            project_id,
            role=MessageRole.ASSISTANT,
            content=final_answer,
            sender="assistant",
            agent_id=boss_id,
        )

        return final_answer

    # --------------------------------------------------------------- internals

    async def _run_llm_tool_loop(
        self,
        boss_cfg:      AgentConfig,
        project_id:    str,
        project_cfg:   ProjectConfig,
        messages:      list[dict],
        tools_for_llm: list[dict],
    ) -> str:
        """
        Iterativer Loop:
        - completion mit Tools
        - tool_calls ausführen
        - tool results zurück an LLM
        - bis Textantwort kommt
        """
        max_rounds = 10

        # --- Model Selection mit Fallback Chain ---
        # Priorität: config.model > env default > hardcoded
        model_chain = []

        # 1. Primary model aus Agent Config
        primary_model = boss_cfg.llm.model if boss_cfg.llm and boss_cfg.llm.model else "anthropic/claude-haiku-4-5-20251001"
        model_chain.append(primary_model)

        # 2. Add fallback models (ohne Duplikate)
        fallback_candidates = [
            "anthropic/claude-sonnet-4-20250514",
            "anthropic/claude-opus-4-20250514",
            "ollama/mistral:latest",
        ]

        if boss_cfg.llm and boss_cfg.llm.fallback_model:
            fallback_candidates.insert(0, boss_cfg.llm.fallback_model)

        for m in fallback_candidates:
            if m and m not in model_chain:
                model_chain.append(m)

        current_model_idx = 0
        model = model_chain[current_model_idx]

        # API-Keys laden
        from .llm_config import load_llm_config as _load_llm_config
        llm_cfg = _load_llm_config()
        claude_token = _load_claude_oauth_token() if llm_cfg.provider == "claude_max" else ""

        # OpenAI Codex OAuth (for codex-mini-latest)
        openai_token_data = _load_openai_codex_token() if llm_cfg.provider in ["openai", "openai_codex"] else None

        # Budget-Artefakte vermeiden: Unbekannte OpenAI-Parameter global verwerfen

        for round_idx in range(max_rounds):
            # Apply per-call overrides for params not accepted by all providers/models
            completion_kwargs = {
                "model": model,
                "messages": messages,
                "tools": tools_for_llm,
                "tool_choice": "auto",
                "temperature": 0.7,
                "max_tokens": 2048,
                "metadata": {"project_id": project_id},
                "drop_params": True,
            }

            # Claude Max Provider Routing
            if model.startswith("anthropic/") and claude_token:
                completion_kwargs["api_key"] = claude_token
                completion_kwargs["base_url"] = "https://api.anthropic.com/v1"

            # OpenAI Codex OAuth Routing
            if model.startswith("openai/") and openai_token_data:
                completion_kwargs["api_key"] = openai_token_data.get("access_token", "")
                completion_kwargs["base_url"] = "https://chatgpt.com/backend-api/codex"
                completion_kwargs["extra_headers"] = {
                    "ChatGPT-Account-ID": openai_token_data.get("account_id", ""),
                    "User-Agent": "Mozilla/5.0",
                }

            try:
                resp = await _llm_with_retry(lambda: litellm.acompletion(**completion_kwargs), max_attempts=3)
            except Exception as e:
                # Failover zu naechstem Modell bei Quota/Overload
                if _should_failover(e) and current_model_idx < len(model_chain) - 1:
                    failed_model = model
                    current_model_idx += 1
                    model = model_chain[current_model_idx]
                    logger.warning(
                        "LLM failover: %s -> %s (%s)",
                        failed_model, model, str(e)[:120]
                    )
                    # Retry same round with next model
                    continue

                # Kein weiterer Failover moeglich -> sauberer Fehlertext
                logger.error("LLM completion failed (model=%s): %s", model, e)
                return f"❌ LLM-Fehler ({model}): {str(e)[:200]}"
            msg = resp.choices[0].message

            # Fall A: Modell liefert Tool-Calls
            if getattr(msg, "tool_calls", None):
                messages.append(self._assistant_msg_with_tool_calls(msg))

                tool_results = []
                for tc in msg.tool_calls:
                    tool_name = tc.function.name
                    raw_args  = tc.function.arguments or "{}"

                    try:
                        args = json.loads(raw_args)
                    except json.JSONDecodeError:
                        args = {}

                    result = await self._execute_tool_call(
                        boss_cfg=boss_cfg,
                        project_id=project_id,
                        project_cfg=project_cfg,
                        tool_name=tool_name,
                        args=args,
                    )

                    tool_results.append((tc.id, tool_name, result))

                # Tool-Results zurück ins Nachrichten-Array
                for tool_call_id, tool_name, result in tool_results:
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tool_call_id,
                        "name": tool_name,
                        "content": json.dumps(result, ensure_ascii=False),
                    })

                # Nächste Runde
                continue

            # Fall B: Finale Textantwort
            content = getattr(msg, "content", None)
            if isinstance(content, str) and content.strip():
                return content.strip()

            # Fall C: Nichts Brauchbares
            return "Ich konnte keine sinnvolle Antwort erzeugen."

        return "Ich habe zu viele Tool-Runden benötigt und breche ab."

    async def _execute_tool_call(
        self,
        boss_cfg:     AgentConfig,
        project_id:   str,
        project_cfg:  ProjectConfig,
        tool_name:    str,
        args:         dict,
    ) -> dict:
        """
        Führt EINEN Tool-Call aus.
        dispatch_task wird speziell behandelt (parallel Worker).
        """
        # Spezialfall dispatch_task
        if tool_name == "dispatch_task":
            tasks = args.get("tasks", [])
            if isinstance(tasks, str):
                tasks = [tasks]
            if not isinstance(tasks, list):
                tasks = []

            workers = project_cfg.agents.workers or []
            if not workers:
                return {"ok": False, "error": "Keine Worker im Projekt konfiguriert."}

            dispatch_results = await self._dispatch_parallel(
                tasks=tasks,
                worker_ids=workers,
                project_id=project_id,
                project_cfg=project_cfg,
            )
            return {
                "ok": True,
                "results": [dr.__dict__ for dr in dispatch_results],
            }

        # normales Tool aus Registry
        tool = self._reg.by_name(tool_name)
        if not tool:
            return {"ok": False, "error": f"Tool '{tool_name}' unbekannt."}

        # Permission-Check
        if not self._reg.is_tool_allowed_for_agent(tool_name, boss_cfg):
            return {"ok": False, "error": f"Tool '{tool_name}' nicht erlaubt für Agent {boss_cfg.id}."}

        try:
            res = await tool.execute(
                agent_id=boss_cfg.id,
                project_id=project_id,
                **args,
            )
            if isinstance(res, dict):
                return res
            return {"ok": True, "result": res}
        except Exception as e:
            logger.exception("Tool-Ausführung fehlgeschlagen: %s", tool_name)
            return {"ok": False, "error": str(e)}

    async def _dispatch_parallel(
        self,
        tasks:       list[str],
        worker_ids:  list[str],
        project_id:  str,
        project_cfg: ProjectConfig,
    ) -> list[DispatchResult]:
        """
        Weist Tasks auf Worker zu (round-robin) und führt sie parallel aus.
        """
        if not tasks:
            return []

        coros = []
        for i, task_text in enumerate(tasks):
            wid = worker_ids[i % len(worker_ids)]
            coros.append(self._run_worker_task(wid, task_text, project_id, project_cfg))

        raw = await asyncio.gather(*coros, return_exceptions=True)

        out: list[DispatchResult] = []
        for i, r in enumerate(raw):
            wid = worker_ids[i % len(worker_ids)]
            t   = tasks[i]

            if isinstance(r, Exception):
                out.append(DispatchResult(
                    worker_id=wid,
                    task=t,
                    result="",
                    success=False,
                    error=str(r),
                ))
            else:
                out.append(r)

        return out

    async def _run_worker_task(
        self,
        worker_id:   str,
        task_text:   str,
        project_id:  str,
        project_cfg: ProjectConfig,
    ) -> DispatchResult:
        """
        Führt eine Task auf einem Worker-Agenten aus.
        Aktuell simpel: Worker verarbeitet Task direkt über runtime.process_task(...)
        """
        try:
            worker_cfg = self._discovery.load_agent_config(worker_id)
        except Exception as e:
            return DispatchResult(
                worker_id=worker_id,
                task=task_text,
                result="",
                success=False,
                error=f"Worker-Konfig fehlt: {e}",
            )

        result_text = await self._runtime.process_task(
            agent_cfg=worker_cfg,
            project_id=project_id,
            project_cfg=project_cfg,
            task=task_text,
        )

        return DispatchResult(
            worker_id=worker_id,
            task=task_text,
            result=result_text,
            success=True,
        )

    # --------------------------------------------------------- prompt + history

    def _load_soul(self, cfg: AgentConfig) -> str:
        if cfg.soul_file and cfg.soul_file.exists():
            try:
                return cfg.soul_file.read_text(encoding="utf-8")
            except OSError:
                pass
        return ""

    def _build_system_prompt(
        self,
        boss_cfg:      AgentConfig,
        project_cfg:   ProjectConfig,
        soul_text:     str,
        skills_prompt: str,
    ) -> str:
        base = [
            f"Du bist Agent '{boss_cfg.id}' ({boss_cfg.type}).",
            f"Projekt: {project_cfg.id}",
            "Arbeite präzise, sicher und nachvollziehbar.",
        ]

        if soul_text.strip():
            base.append("\n=== SOUL ===\n" + soul_text.strip())

        if skills_prompt.strip():
            base.append("\n=== AKTIVE SKILLS ===\n" + skills_prompt.strip())

        base.append(
            "\nWenn sinnvoll, nutze Tools. "
            "Für Teilaufgaben kannst du dispatch_task verwenden."
        )

        return "\n".join(base)

    @staticmethod
    def _history_to_openai_messages(history: list[dict]) -> list[dict]:
        out: list[dict] = []
        for m in history:
            role = m.get("role", "user")
            content = m.get("content", "")

            if role not in {"system", "user", "assistant", "tool"}:
                role = "user"

            # tool_messages aus alter Historie ggf. unvollständig -> als assistant notieren
            if role == "tool":
                # Historie ohne tool_call_id ist für OpenAI ungültig
                out.append({
                    "role": "assistant",
                    "content": f"[Historisches Tool-Ergebnis]\n{content}",
                })
            else:
                out.append({"role": role, "content": content})

        return out

    @staticmethod
    def _assistant_msg_with_tool_calls(msg) -> dict:
        """
        litellm/openai message -> plain dict für unser messages-array.
        """
        tc_list = []
        for tc in msg.tool_calls:
            tc_list.append({
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            })

        return {
            "role": "assistant",
            "content": msg.content or "",
            "tool_calls": tc_list,
        }
