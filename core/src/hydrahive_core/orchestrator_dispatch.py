"""
orchestrator_dispatch.py — Tool-Loop, Worker-Dispatch & Synthese (#386)

Agentic Loop: LLM-Antwort → Tool-Calls → Ergebnisse → nächste Runde.
Worker-Tasks parallel ausführen, Ergebnisse aggregieren.
"""

import asyncio
import json
import logging

from .agent_config import AgentConfig
from .project_config import ProjectConfig
from .orchestrator_tools import (
    DispatchResult, _truncate_tool_result,
    check_repeated_signature, execute_tool_call, format_tool_result,
    handle_request_tools,
)

logger = logging.getLogger(__name__)


def _parse_dispatch_calls(tool_calls: list) -> list[dict]:
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
    orch,
    project_cfg: ProjectConfig,
    dispatches: list[dict],
    context: str,
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
            async def _rejected(d=d) -> DispatchResult:
                return DispatchResult(
                    worker_id=d["worker_id"], task=d["task"],
                    result="", success=False,
                    error="Agent nicht dem Projekt zugewiesen",
                )
            tasks.append(_rejected())
            continue
        tasks.append(_run_worker_task(orch, d))

    return list(await asyncio.gather(*tasks, return_exceptions=False))


async def _run_worker_task(orch, dispatch: dict) -> DispatchResult:
    """Einen Worker-Agenten mit einem Task beauftragen."""
    worker_id = dispatch["worker_id"]
    task      = dispatch["task"]
    context   = dispatch.get("context", "")

    worker_cfg = orch._discovery.get(worker_id)
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
        response = await orch._llm_call(worker_cfg, messages, tools=None)
        result = response.choices[0].message.content or ""
        return DispatchResult(worker_id=worker_id, task=task, result=result)
    except Exception as e:
        logger.error("Worker '%s' LLM-Fehler: %s", worker_id, e)
        return DispatchResult(
            worker_id=worker_id, task=task, result="",
            success=False, error=str(e)
        )


async def _synthesize(
    orch,
    boss_cfg: AgentConfig,
    messages: list[dict],
    tool_calls: list,
    results: list[DispatchResult],
) -> str:
    """Boss fasst Worker-Ergebnisse zur finalen Antwort zusammen."""
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
        response = await orch._llm_call(boss_cfg, follow_up, tools=None)
        return response.choices[0].message.content or ""
    except Exception as e:
        logger.error("Synthese-LLM-Fehler: %s", e)
        lines = [f"**{r.worker_id}**: {r.result}" for r in results if r.success]
        return "\n".join(lines)


async def _tool_loop(
    orch,
    boss_cfg: AgentConfig,
    project_id: str,
    project_cfg: ProjectConfig,
    messages: list[dict],
    response,
    max_rounds: int | None = None,
    execution_mode: str | None = None,
) -> tuple[str, list[str]]:
    """
    Agentic Loop: LLM-Antwort → Tool-Calls ausführen → Ergebnisse einbauen → wiederholen.
    Dispatch-Tasks werden parallel ausgeführt, andere Tools sequentiell.
    Max. max_rounds Runden um Endlosschleifen zu vermeiden.
    Gibt (finale Antwort, beteiligte Worker-IDs) zurück.
    """
    from .orchestrator_tools import _tool_call_signature as _tool_call_signature_fn

    # user_text für on-demand Tool-Filterung aus letzter User-Message
    _last_user = next(
        (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
        "",
    )
    # Im _tool_loop immer alle Tools (Kategorien wurden ggf. schon per request_tools nachgeladen)
    boss_tools = orch._allowed_tools(boss_cfg, execution_mode, user_text=_last_user)
    litellm_tools: list[dict] = orch._reg.as_litellm_tools(boss_tools) if boss_tools else []
    _mcp_schemas = await orch._mcp_schemas_for_agent(boss_cfg)
    if _mcp_schemas:
        litellm_tools = litellm_tools + _mcp_schemas
    # Plugin-Tools (#110)
    _plugin_schemas = orch._plugin_schemas_for_agent(boss_cfg)
    if _plugin_schemas:
        litellm_tools = litellm_tools + _plugin_schemas
    # Dedup über alles (MCP + Plugins können Duplikate erzeugen)
    from .orchestrator import _dedup_tools
    litellm_tools = _dedup_tools(litellm_tools)
    _loaded_categories: set[str] = set()
    _file_read_cache: dict[str, str] = {}
    current_messages = list(messages)
    workers_used: list[str] = []
    last_signature: tuple[str, ...] | None = None
    repeated_signature_count = 0
    max_rounds = max_rounds or boss_cfg.max_tool_rounds

    for _round in range(max_rounds):
        tool_calls = getattr(response.choices[0].message, "tool_calls", None)
        if not tool_calls:
            return response.choices[0].message.content or "", workers_used

        signature = _tool_call_signature_fn(tool_calls)
        last_signature, repeated_signature_count, should_abort = check_repeated_signature(
            signature, last_signature, repeated_signature_count, threshold=2,
        )

        if should_abort:
            logger.warning(
                "Tool-Loop: wiederholte Tool-Signatur erkannt (%s) — erzwinge Abschluss",
                ", ".join(signature[:3])[:180],
            )
            try:
                final = await orch._finalize_tool_loop_response(
                    boss_cfg,
                    current_messages,
                    reason="wiederholte Tool-Signatur",
                    execution_mode=execution_mode,
                )
                return final.choices[0].message.content or "", workers_used
            except Exception as e:
                logger.error("Tool-Loop Finalisierung fehlgeschlagen: %s", e)
                return "[Fehler] Konnte keine Antwort erzeugen — bitte erneut versuchen.", workers_used

        # Letzte Runde: kein weiteres Tool-Calling → Final-Antwort erzwingen
        if _round == max_rounds - 1:
            logger.warning("Tool-Loop: max_rounds=%d erreicht — erzwinge Textantwort", max_rounds)
            try:
                from .tool_registry import _notify as _tr_notify
                _tr_notify(project_id, "agent_warning",
                           f"Tool-Loop Limit erreicht",
                           f"Agent hat {max_rounds} Runden durchlaufen — Antwort wird erzwungen.",
                           link=f"/chat/{project_id}")
            except Exception as e:
                logger.debug("Failed to send tool-loop warning notification: %s", e)
            try:
                final = await orch._finalize_tool_loop_response(
                    boss_cfg,
                    current_messages,
                    reason=f"max_rounds={max_rounds}",
                    execution_mode=execution_mode,
                )
                return final.choices[0].message.content or "", workers_used
            except Exception as e:
                logger.error("Tool-Loop max_rounds Finalisierung fehlgeschlagen: %s", e)
                return "[Fehler] Konnte keine Antwort erzeugen — bitte erneut versuchen.", workers_used

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

        tool_results: dict[str, str] = {}

        # On-Demand Tool-Kategorien nachladen
        for tc in request_tool_tcs:
            try:
                args = json.loads(tc.function.arguments)
                categories = args.get("categories", [])
                _, result_dict = handle_request_tools(
                    orch, boss_cfg, execution_mode, categories,
                    _loaded_categories, litellm_tools,
                )
                tool_results[tc.id] = json.dumps(result_dict, ensure_ascii=False)
            except Exception as e:
                tool_results[tc.id] = f"[Fehler] request_tools: {e}"

        if dispatch_tcs:
            dispatches = _parse_dispatch_calls(dispatch_tcs)
            results = await _dispatch_parallel(orch, project_cfg, dispatches, context="")
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
            args = json.loads(tc.function.arguments)
            result, is_error = await execute_tool_call(
                orch, boss_cfg=boss_cfg, project_id=project_id,
                tool_name=tc.function.name, tool_input=args,
                execution_mode=execution_mode,
                file_read_cache=_file_read_cache,
            )
            if is_error:
                logger.error("Tool '%s' fehlgeschlagen: %s", tc.function.name, result.get("error", ""))
            tool_results[tc.id] = format_tool_result(result)

        # Tool-Results in Messages einbauen
        for tc in tool_calls:
            current_messages.append({
                "role":         "tool",
                "tool_call_id": tc.id,
                "content":      tool_results.get(tc.id, ""),
            })

        # Nächste LLM-Runde
        try:
            response = await orch._llm_call(boss_cfg, current_messages, litellm_tools)
        except Exception as e:
            logger.error("LLM-Fehler in Tool-Loop: %s", e)
            return "[Fehler] LLM nicht erreichbar — bitte später erneut versuchen.", workers_used

    return response.choices[0].message.content or "", workers_used
