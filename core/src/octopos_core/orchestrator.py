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
from .learning_memory import build_learning_prompt_snippet
from .project_config import ProjectConfig
from .session_manager import MessageRole, SessionManager
from .skill_loader import load_skills, select_skills, skills_to_system_prompt
from .tool_registry import ToolRegistry, registry as default_registry

logger = logging.getLogger(__name__)

# drop_params per-call via kwargs, nicht global (verhindert cross-module side effects)


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
    ) -> list:
        permissions = agent_cfg.effective_permissions(execution_mode)  # type: ignore[arg-type]
        return self._reg.tools_for_agent(agent_cfg.tools, agent_permissions=permissions)

    def _allowed_tool_map(
        self,
        agent_cfg: AgentConfig,
        execution_mode: str | None = None,
    ) -> dict[str, object]:
        return {tool.id: tool for tool in self._allowed_tools(agent_cfg, execution_mode)}

    def _resolve_allowed_tool(
        self,
        agent_cfg: AgentConfig,
        tool_name: str,
        execution_mode: str | None = None,
    ):
        allowed = self._allowed_tool_map(agent_cfg, execution_mode)
        return allowed.get(tool_name)

    async def _execute_tool(
        self,
        tool,
        *,
        boss_cfg: AgentConfig,
        project_id: str,
        tool_name: str,
        tool_input: dict | None = None,
    ):
        args = dict(tool_input or {})
        effective_pid = args.pop("project_id", None) or project_id
        if tool is None:
            return {"error": f"Tool '{tool_name}' ist in diesem Modus nicht erlaubt"}
        return await tool.execute(
            agent_id=boss_cfg.id,
            project_id=effective_pid,
            **args,
        )

    @staticmethod
    def _tool_call_signature(tool_calls: list) -> tuple[str, ...]:
        signature: list[str] = []
        for tc in tool_calls:
            fn = getattr(tc, "function", None)
            name = getattr(fn, "name", "") or ""
            arguments = getattr(fn, "arguments", "") or ""
            signature.append(f"{name}:{arguments}")
        return tuple(signature)

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
                    "Fasse die vorliegenden Ergebnisse jetzt kurz und konkret zusammen. "
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
        return await future

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
        boss_tools = self._allowed_tools(boss_cfg, execution_mode)
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
                boss_cfg, project_id, project_cfg, messages, response, execution_mode=execution_mode
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
                drop_params=True,
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

        # Persistentes Gedächtnis laden (#85)
        if boss_cfg.agent_dir:
            memory_dir = boss_cfg.agent_dir / "memory"
            if memory_dir.exists():
                mem_parts = []
                learning_snippet = build_learning_prompt_snippet(boss_cfg.agent_dir)
                if learning_snippet:
                    mem_parts.append(learning_snippet)
                for mf in sorted(memory_dir.glob("*.md")):
                    if mf.name == "learned-facts.md":
                        continue
                    try:
                        text = mf.read_text(encoding="utf-8").strip()
                        if text:
                            mem_parts.append(f"### {mf.stem}\n{text}")
                    except OSError:
                        pass
                if mem_parts:
                    parts.append("## Persistentes Gedächtnis\n\n" + "\n\n".join(mem_parts))

        # QMD-Skills laden (scope=always immer, on-demand bei Keyword-Match)
        if boss_cfg.agent_dir:
            all_skills = load_skills(boss_cfg.agent_dir)
            active_skills = select_skills(all_skills, user_text)
            if active_skills:
                parts.append(skills_to_system_prompt(active_skills))

        repo_guidance = self._repo_review_guidance(boss_cfg, user_text)
        if repo_guidance:
            parts.append(repo_guidance)

        return "\n\n".join(parts)

    @staticmethod
    def _repo_review_guidance(agent_cfg: AgentConfig, user_text: str) -> str:
        text = (user_text or "").lower()
        triggers = (
            "repo", "repository", "review", "commit", "diff", "issue",
            "gitea", "github", "pull request", "pr ", "datei", "file",
            "struktur", "tree", "deep dive", "http://", "https://",
        )
        if not any(token in text for token in triggers):
            return ""

        available = set(agent_cfg.tools or [])
        repo_tools = {"gitea_repo_inspect", "gitea_repo_tree", "gitea_repo_file", "gitea_repo_commits"}
        if not available.intersection(repo_tools) and "git_status" not in available and "git_diff" not in available:
            return ""

        return (
            "## Repo-Review-Arbeitsrahmen\n"
            "- Bei Repo-, Review-, Commit- oder Datei-Anfragen zuerst das Zielrepo sauber aufloesen.\n"
            "- Fuer Gitea-Repo-Links repo-aware Tools bevorzugen, nicht mit einem rohen http_request nach dem ersten 404 aufhoeren.\n"
            "- Sinnvolle Reihenfolge:\n"
            "  1. gitea_repo_inspect fuer Repo-Metadaten und Grundzustand\n"
            "  2. gitea_repo_tree fuer Struktur und relevante Verzeichnisse\n"
            "  3. gitea_repo_file fuer konkrete Dateien\n"
            "  4. git_status oder git_diff nur wenn lokaler Workspace-Zustand oder Aenderungen wirklich relevant sind\n"
            "- Keine breite Bewertung ohne mindestens Struktur oder konkrete Dateien geprueft zu haben.\n"
            "- Wenn ein Repo-Link nicht direkt oeffnet, ueber Repo-Aufloesung, API oder owner/repo weiterarbeiten statt abzubrechen."
        )

    @staticmethod
    def _resolve_model(model: str, ollama_base_url: str | None = None) -> tuple[str, str | None]:
        """
        Gibt (litellm_model, api_base) zurück.
        Provider-Prefix (z.B. anthropic/, openai/) → direkt weiterreichen.
        Claude/GPT-Modellnamen → passenden Provider-Prefix ergänzen.
        Kein Prefix, kein bekannter Cloud-Name → Ollama auf localhost.
        ollama_base_url: wenn gesetzt, wird statt localhost dieser Endpunkt genutzt (WKS-Ollama).
        """
        ollama_base = ollama_base_url or "http://localhost:11434"
        # ollama/ → ollama_chat/ damit /api/chat (mit Tool Calling) statt /api/generate genutzt wird
        if model.startswith("ollama/"):
            return f"ollama_chat/{model[len('ollama/'):]}", ollama_base
        if "/" in model:
            return model, None
        # Bekannte Cloud-Modell-Prefixe automatisch ergänzen
        if model.startswith(("claude-",)):
            return f"anthropic/{model}", None
        if model.startswith(("gpt-", "o1-", "o3-")):
            return f"openai/{model}", None
        # Kein Prefix → lokales Ollama-Modell (chat)
        return f"ollama_chat/{model}", ollama_base

    async def _llm_call_single(
        self,
        model_name:  str,
        agent_cfg:   AgentConfig,
        messages:    list[dict],
        tools:       list[dict] | None,
    ):
        """Ein einzelnes Modell aufrufen — kein Failover-Logik."""
        # OpenAI Codex (ChatGPT Plus OAuth)
        if model_name.startswith("openai-codex/"):
            codex_token = _load_openai_codex_token()
            if codex_token:
                return await self._openai_codex_call(agent_cfg, messages, tools, codex_token, model_name)

        # Claude Max OAuth
        is_claude = model_name.startswith(("claude-", "anthropic/"))
        oauth_token = _load_claude_oauth_token() if is_claude else ""
        if oauth_token:
            return await self._anthropic_oauth_call(agent_cfg, messages, tools, oauth_token, model_name)

        model, api_base = self._resolve_model(model_name, agent_cfg.llm.ollama_base_url)
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

        return await _llm_with_retry(lambda: litellm.acompletion(**kwargs, drop_params=True))

    async def _llm_call(
        self,
        agent_cfg:   AgentConfig,
        messages:    list[dict],
        tools:       list[dict] | None,
    ):
        """
        LLM-Call mit Failover: versucht primary model, dann fallback_models.
        Bei Quota/Overload-Fehler wird automatisch zum nächsten Modell gewechselt.
        """
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

    async def _anthropic_oauth_call(
        self,
        agent_cfg:      AgentConfig,
        messages:       list[dict],
        tools:          list[dict] | None,
        token:          str,
        model_override: str | None = None,
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
            default_headers={
                "anthropic-beta": "claude-code-20250219,oauth-2025-04-20,fine-grained-tool-streaming-2025-05-14",
                "user-agent":     "claude-cli/2.1.62",
                "x-app":          "cli",
            },
        )

        # System-Message extrahieren + OpenAI→Anthropic-Format konvertieren
        import json as _json
        system_msg = ""
        filtered   = []
        for m in messages:
            role = m.get("role", "")
            if role == "system":
                system_msg = m.get("content", "")
                continue

            # OpenAI tool-result: {"role":"tool","tool_call_id":...,"content":...}
            # → Anthropic: {"role":"user","content":[{"type":"tool_result","tool_use_id":...,"content":...}]}
            if role == "tool":
                tool_result_block = {
                    "type":        "tool_result",
                    "tool_use_id": m.get("tool_call_id", "unknown"),
                    "content":     m.get("content", ""),
                }
                # Mit vorherigem user-Block zusammenführen wenn möglich
                if filtered and filtered[-1]["role"] == "user" and isinstance(filtered[-1].get("content"), list):
                    filtered[-1]["content"].append(tool_result_block)
                else:
                    filtered.append({"role": "user", "content": [tool_result_block]})
                continue

            # OpenAI assistant mit tool_calls: {"role":"assistant","tool_calls":[...]}
            # → Anthropic: {"role":"assistant","content":[{"type":"tool_use","id":...,"name":...,"input":...}]}
            tool_calls = m.get("tool_calls")
            if role == "assistant" and tool_calls:
                asst_content = []
                if m.get("content"):
                    asst_content.append({"type": "text", "text": m["content"]})
                for tc in tool_calls:
                    fn = tc.get("function", {})
                    try:
                        inp = _json.loads(fn.get("arguments", "{}"))
                    except Exception:
                        inp = {}
                    asst_content.append({
                        "type":  "tool_use",
                        "id":    tc.get("id", "unknown"),
                        "name":  fn.get("name", "unknown"),
                        "input": inp,
                    })
                filtered.append({"role": "assistant", "content": asst_content})
                continue

            # Normaler Text-Message
            filtered.append({"role": role, "content": m.get("content") or ""})

        # Consecutive gleiche Rollen mergen (Anthropic-Constraint) — nur für Text-Messages
        merged: list[dict] = []
        for m in filtered:
            if (merged and merged[-1]["role"] == m["role"]
                    and isinstance(m.get("content"), str)
                    and isinstance(merged[-1].get("content"), str)):
                merged[-1]["content"] += "\n\n" + m["content"]
            else:
                merged.append(dict(m))
        filtered = merged

        # Modell-Name normalisieren (openai/claude-... → claude-...)
        model = model_override or agent_cfg.llm.model
        for prefix in ("openai/", "anthropic/", "claude/"):
            if model.startswith(prefix):
                model = model[len(prefix):]
                break
        if not model.startswith("claude-"):
            model = "claude-haiku-4-5"

        # OAuth erfordert Claude-Code-Identity als ersten System-Block
        oauth_system = [{"type": "text", "text": "You are Claude Code, Anthropic's official CLI for Claude."}]
        if system_msg:
            oauth_system.append({"type": "text", "text": system_msg})

        kwargs: dict = {
            "model":       model,
            "max_tokens":  agent_cfg.llm.max_tokens,
            "messages":    filtered,
            "temperature": agent_cfg.llm.temperature,
            "system":      oauth_system,
        }
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


    async def _openai_codex_call(
        self,
        agent_cfg:    AgentConfig,
        messages:     list[dict],
        tools:        list[dict] | None,
        token_data:   dict,
        model_name:   str,
        force_tools:  bool = True,
    ):
        """
        ChatGPT Plus/Pro via Codex OAuth.
        Endpoint: chatgpt.com/backend-api/codex/responses (OpenAI Responses API).
        Die Codex API erfordert stream=true — wir sammeln alle Chunks zu einem Response.
        """
        import json as _json
        import httpx as _httpx
        from types import SimpleNamespace

        model_id = model_name
        if model_id.startswith("openai-codex/"):
            model_id = model_id[len("openai-codex/"):]

        access_token = token_data["access_token"]
        account_id   = token_data["account_id"]

        # Chat Completions Messages → Responses API input konvertieren
        def _codex_item_id(tool_call: dict) -> str:
            item_id = str(tool_call.get("item_id") or "").strip()
            if item_id.startswith("fc_"):
                return item_id
            call_id = str(tool_call.get("id") or "").strip()
            if call_id.startswith("fc_"):
                return call_id
            if call_id.startswith("call_"):
                return "fc_" + call_id[len("call_"):]
            if call_id:
                return "fc_" + call_id.replace(" ", "_")
            return "fc_unknown"

        system_prompt = ""
        input_items: list = []
        for m in messages:
            role    = m.get("role", "")
            content = m.get("content", "") or ""
            if role == "system":
                system_prompt = content
                continue
            if role == "tool":
                input_items.append({
                    "type":    "function_call_output",
                    "call_id": m.get("tool_call_id", ""),
                    "output":  content,
                })
                continue
            tc_list = m.get("tool_calls")
            if role == "assistant" and tc_list:
                if content:
                    input_items.append({
                        "role":    "assistant",
                        "content": [{"type": "output_text", "text": content}],
                    })
                for tc in tc_list:
                    fn = tc.get("function", {})
                    input_items.append({
                        "type":      "function_call",
                        "id":        _codex_item_id(tc),
                        "call_id":   tc.get("id", ""),
                        "name":      fn.get("name", ""),
                        "arguments": fn.get("arguments", "{}"),
                    })
                continue
            # user/assistant: Responses API erwartet content als Liste
            input_items.append({
                "role":    role,
                "content": [{"type": "input_text" if role == "user" else "output_text", "text": content}],
            })

        resp_tools = None
        if tools:
            resp_tools = [
                {
                    "type":        "function",
                    "name":        t["function"]["name"],
                    "description": t["function"].get("description", ""),
                    "parameters":  t["function"].get("parameters", {"type": "object", "properties": {}}),
                    "strict":      None,
                }
                for t in tools
            ]

        # Codex API: temperature, max_output_tokens etc. werden abgelehnt
        payload: dict = {
            "model":                  model_id,
            "input":                  input_items,
            "store":                  False,
            "stream":                 True,
            "text":                   {"verbosity": "medium"},
            "include":                ["reasoning.encrypted_content"],
            "parallel_tool_calls":    True,
        }
        instructions = system_prompt
        if resp_tools:
            tool_names = ", ".join(t["name"] for t in resp_tools)
            tool_hint  = (
                f"\n\nDu hast {len(resp_tools)} Tools zur Verfügung: {tool_names}. "
                "Nutze sie aktiv und direkt — führe Befehle aus statt sie zu erklären. "
                "Frage nicht nach Erlaubnis, handle autonom."
            )
            instructions = (instructions + tool_hint) if instructions else tool_hint
        if instructions:
            payload["instructions"] = instructions
        if resp_tools:
            payload["tools"]       = resp_tools
            payload["tool_choice"] = "required" if force_tools else "auto"

        headers = {
            "Authorization":      f"Bearer {access_token}",
            "chatgpt-account-id": account_id,
            "OpenAI-Beta":        "responses=experimental",
            "originator":         "pi",
            "Content-Type":       "application/json",
        }

        text = ""
        accumulated_fn: dict[str, dict] = {}  # call_id → {id, name, arguments}

        async with _httpx.AsyncClient(timeout=120) as client:
            async with client.stream(
                "POST", "https://chatgpt.com/backend-api/codex/responses",
                headers=headers, json=payload,
            ) as resp:
                if resp.status_code != 200:
                    body = await resp.aread()
                    raise RuntimeError(f"Codex API {resp.status_code}: {body.decode()[:300]}")

                async for line in resp.aiter_lines():
                    if not line.startswith("data: "):
                        continue
                    data_str = line[6:].strip()
                    if not data_str or data_str == "[DONE]":
                        continue
                    try:
                        ev = _json.loads(data_str)
                    except Exception:
                        continue
                    ev_type = ev.get("type", "")

                    if ev_type == "response.output_text.delta":
                        text += ev.get("delta", "")

                    elif ev_type == "response.output_item.added":
                        item = ev.get("item", {})
                        if item.get("type") == "function_call":
                            item_id  = item.get("id", "")          # fc_xxx  (used by delta/done events)
                            call_id  = item.get("call_id", "")      # call_xxx (used in function_call_output)
                            accumulated_fn[item_id] = {
                                "id":        item_id,
                                "call_id":   call_id,
                                "name":      item.get("name", ""),
                                "arguments": "",
                            }

                    elif ev_type == "response.function_call_arguments.delta":
                        cid = ev.get("item_id", ev.get("call_id", ""))
                        if cid in accumulated_fn:
                            accumulated_fn[cid]["arguments"] += ev.get("delta", "")

                    elif ev_type == "response.function_call_arguments.done":
                        cid = ev.get("item_id", ev.get("call_id", ""))
                        if cid in accumulated_fn:
                            accumulated_fn[cid]["arguments"] = ev.get("arguments", accumulated_fn[cid]["arguments"])

        tool_calls_out = [
            SimpleNamespace(
                id=fn["call_id"],
                item_id=fn["id"],
                type="function",
                function=SimpleNamespace(
                    name=fn["name"],
                    arguments=fn["arguments"] or "{}",
                ),
            )
            for fn in accumulated_fn.values()
        ]

        message = SimpleNamespace(
            role="assistant",
            content=text,
            tool_calls=tool_calls_out if tool_calls_out else None,
        )
        choice = SimpleNamespace(message=message, finish_reason="stop")
        return SimpleNamespace(choices=[choice], model=model_id)


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

        # Session + System-Prompt aufbauen
        user_msg_saved = False
        self._sessions.append(project_id, MessageRole.USER, content, agent_id=sender)
        user_msg_saved = True
        system_prompt = self._build_system_prompt(boss_cfg, content)
        history       = self._sessions.get_context(project_id, max_messages=20)
        messages      = [{"role": "system", "content": system_prompt}] + history

        # Tool-Schema
        boss_tools    = self._allowed_tools(boss_cfg, execution_mode)
        litellm_tools = self._reg.as_litellm_tools(boss_tools) if boss_tools else None

        models_to_try = [boss_cfg.llm.model] + boss_cfg.llm.fallback_models

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
                                if repeated_signature_count >= 1:
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
                                    tool = self._resolve_allowed_tool(boss_cfg, tc.function.name, execution_mode)
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
                                    result_str = _json.dumps(result, ensure_ascii=False)
                                    if len(result_str) > 8000:
                                        result_str = result_str[:8000] + "\n...[gekürzt]"
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

                            tool_use_blocks = [b for b in final_msg.content if b.type == "tool_use"]
                            if not tool_use_blocks:
                                break
                            signature = tuple(
                                f"{block.name}:{_json.dumps(block.input, ensure_ascii=False, sort_keys=True)}"
                                for block in tool_use_blocks
                            )
                            if signature and signature == last_tool_signature:
                                repeated_tool_signature_count += 1
                            else:
                                repeated_tool_signature_count = 0
                            last_tool_signature = signature
                            if repeated_tool_signature_count >= 1:
                                kwargs_final = dict(kwargs)
                                kwargs_final.pop("tools", None)
                                kwargs_final["messages"] = filtered + [
                                    {
                                        "role": "user",
                                        "content": [
                                            {
                                                "type": "text",
                                                "text": "[System: Wiederholte Tool-Signatur erkannt. Bitte fasse die vorhandenen Ergebnisse jetzt kurz zusammen und rufe keine weiteren Tools auf.]",
                                            }
                                        ],
                                    }
                                ]
                                async with client.messages.stream(**kwargs_final) as stream:
                                    async for text in stream.text_stream:
                                        full_response += text
                                        streamed_any = True
                                        yield f"data: {_json.dumps({'text': text})}\n\n"
                                break
                            if _round == boss_cfg.max_tool_rounds - 2:
                                # Vorletzter Durchlauf — Agent soll jetzt abschließen
                                filtered.append({"role": "user", "content": [{"type": "text", "text": "[System: Letzte Tool-Runde — bitte Ergebnisse jetzt zusammenfassen und abschließen.]"}]})
                                kwargs["messages"] = filtered

                            tool_results = []
                            for block in tool_use_blocks:
                                yield f"data: {_json.dumps({'tool_call': block.name, 'tool_input': block.input})}\n\n"
                                tool = self._resolve_allowed_tool(boss_cfg, block.name, execution_mode)
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
                                result_str = _json.dumps(result, ensure_ascii=False)
                                if len(result_str) > 8000:
                                    result_str = result_str[:8000] + "\n...[gekürzt, zu groß]"
                                tool_results.append({
                                    "type":        "tool_result",
                                    "tool_use_id": block.id,
                                    "content":     result_str,
                                })

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
                            kwargs_final["messages"] = filtered + [{"role": "user", "content": [{"type": "text", "text": "[System: Bitte fasse deine Ergebnisse jetzt kurz zusammen.]"}]}]
                            async with client.messages.stream(**kwargs_final) as stream:
                                async for text in stream.text_stream:
                                    full_response += text
                                    streamed_any   = True
                                    yield f"data: {_json.dumps({'text': text})}\n\n"

                    else:
                        # litellm Streaming (Ollama / OpenAI) mit Tool-Loop
                        import json as _json2
                        model, api_base = self._resolve_model(_model_name, boss_cfg.llm.ollama_base_url)
                        loop_messages = list(messages)
                        last_tool_signature: tuple[str, ...] | None = None
                        repeated_tool_signature_count = 0

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
                            if litellm_tools:
                                kwargs["tools"] = litellm_tools

                            round_text = ""
                            accumulated_tcs: dict = {}  # index → {id, name, arguments}

                            async for chunk in await litellm.acompletion(**kwargs, drop_params=True):
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
                            signature = tuple(
                                f"{tc['name']}:{tc['arguments']}"
                                for tc in tc_list
                            )
                            if signature and signature == last_tool_signature:
                                repeated_tool_signature_count += 1
                            else:
                                repeated_tool_signature_count = 0
                            last_tool_signature = signature
                            if repeated_tool_signature_count >= 1:
                                loop_messages.append({
                                    "role": "user",
                                    "content": "[System: Wiederholte Tool-Signatur erkannt. Bitte fasse die vorhandenen Ergebnisse jetzt kurz zusammen und rufe keine weiteren Tools auf.]",
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

            # Antwort in Session speichern
            self._sessions.append(
                project_id, MessageRole.ASSISTANT,
                full_response, agent_id=boss_cfg.id
            )
            yield f"data: {_json.dumps({'done': True, 'session_id': None})}\n\n"

        except Exception as e:
            logger.error("Streaming-Fehler: %s", e)
            if user_msg_saved:
                self._sessions.pop_last(project_id)
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
        boss_tools = self._allowed_tools(boss_cfg, execution_mode)
        litellm_tools = self._reg.as_litellm_tools(boss_tools) if boss_tools else None
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

            if repeated_signature_count >= 1:
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
                    result_str = json.dumps(result, ensure_ascii=False)
                    if len(result_str) > 8000:
                        result_str = result_str[:8000] + "\n...[gekürzt, zu groß]"
                    tool_results[tc.id] = result_str
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
