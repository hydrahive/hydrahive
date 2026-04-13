"""
orchestrator_dispatch.py — v2 Tool-Loop (#386, v2 cleanup #589)

Agentic Loop: LLM-Antwort → Tool-Calls → Ergebnisse → nächste Runde.

v2: Kein Worker-Dispatch mehr. `dispatch_task`, DAG-Dispatch, Built-in
Workers und Coordinator-Mode wurden entfernt (v1-Reste). Alle Tools
laufen direkt im Projekt-Agent mit den 9 Core-Tools.
"""

import asyncio
import json
import logging

from .agent_config import AgentConfig
from .project_config import ProjectConfig
from .orchestrator_tools import (
    check_repeated_signature, execute_tool_call, format_tool_result,
)

from .session_manager import MessageRole
from .session_metrics import metrics as _metrics

logger = logging.getLogger(__name__)


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
    Parallel-safe Tools werden via asyncio.gather ausgeführt, Rest sequentiell.
    Max. max_rounds Runden um Endlosschleifen zu vermeiden.
    Gibt (finale Antwort, workers_used-Liste) zurück.

    v2: workers_used bleibt leer — kein Worker-Dispatch mehr.
    """
    from .orchestrator_tools import _tool_call_signature as _tool_call_signature_fn

    # user_text für on-demand Tool-Filterung aus letzter User-Message
    _last_user = next(
        (m.get("content", "") for m in reversed(messages) if m.get("role") == "user"),
        "",
    )
    # v2: Immer alle 9 Core-Tools
    boss_tools = orch._allowed_tools(boss_cfg, execution_mode, user_text=_last_user)
    litellm_tools: list[dict] = orch._reg.as_litellm_tools(boss_tools) if boss_tools else []
    _mcp_schemas = await orch._mcp_schemas_for_agent(boss_cfg)
    if _mcp_schemas:
        litellm_tools = litellm_tools + _mcp_schemas
    # Dedup über alles (MCP kann Duplikate erzeugen)
    from .orchestrator import _dedup_tools
    litellm_tools = _dedup_tools(litellm_tools)
    _file_read_cache: dict[str, str] = {}
    current_messages = list(messages)
    workers_used: list[str] = []
    last_signature: tuple[str, ...] | None = None
    repeated_signature_count = 0
    _fuzzy_history_dispatch: list[str] = []  # #618
    max_rounds = max_rounds or boss_cfg.max_tool_rounds

    for _round in range(max_rounds):
        tool_calls = getattr(response.choices[0].message, "tool_calls", None)
        if not tool_calls:
            return response.choices[0].message.content or "", workers_used

        # #512: Tool-Round Metriken
        _metrics.record_tool_round(project_id, len(tool_calls))

        signature = _tool_call_signature_fn(tool_calls)
        from .orchestrator_tools import _fuzzy_fingerprint, check_fuzzy_loop
        for tc in tool_calls:
            _fn = getattr(tc, "function", None)
            if _fn is None:
                continue
            _fuzzy_history_dispatch.append(
                _fuzzy_fingerprint(getattr(_fn, "name", "") or "", getattr(_fn, "arguments", "") or "")
            )
        last_signature, repeated_signature_count, should_abort = check_repeated_signature(
            signature, last_signature, repeated_signature_count, threshold=2,
        )
        if not should_abort:
            _fuzzy_abort, _fuzzy_fp = check_fuzzy_loop(_fuzzy_history_dispatch)
            if _fuzzy_abort:
                logger.warning(
                    "Fuzzy-Loop erkannt (dispatch): Pattern '%s' — Abbruch",
                    (_fuzzy_fp or "")[:120],
                )
                should_abort = True

        if should_abort:
            _metrics.record_signature_abort(project_id)
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
            _metrics.record_max_rounds_hit(project_id)
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

        tool_results: dict[str, str] = {}

        # #418: parallel-safe Tools via asyncio.gather, Rest sequentiell
        parallel_tcs = []
        sequential_tcs = []
        for tc in tool_calls:
            tool_obj = orch._resolve_allowed_tool(boss_cfg, tc.function.name, execution_mode)
            if tool_obj and getattr(tool_obj, "parallel_safe", False):
                parallel_tcs.append(tc)
            else:
                sequential_tcs.append(tc)

        # Parallel-safe Tools gleichzeitig ausführen
        if parallel_tcs:
            async def _run_parallel(tc):
                args = json.loads(tc.function.arguments)
                result, is_error = await execute_tool_call(
                    orch, boss_cfg=boss_cfg, project_id=project_id,
                    tool_name=tc.function.name, tool_input=args,
                    execution_mode=execution_mode,
                    file_read_cache=_file_read_cache,
                )
                if is_error:
                    logger.error("Tool '%s' fehlgeschlagen: %s", tc.function.name, result.get("error", ""))
                return tc.id, format_tool_result(result)

            parallel_results = await asyncio.gather(*[_run_parallel(tc) for tc in parallel_tcs])
            for tc_id, formatted in parallel_results:
                tool_results[tc_id] = formatted
            if len(parallel_tcs) > 1:
                logger.info("Parallel-Execution: %d Tools gleichzeitig (%s)",
                            len(parallel_tcs), ", ".join(tc.function.name for tc in parallel_tcs))

        # Nicht-parallele Tools sequentiell
        for tc in sequential_tcs:
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

        # Tool-Calls + Results in Session persistieren (nicht nur in-memory)
        _tc_list = [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in tool_calls
        ]
        await orch._sessions.append(
            project_id, MessageRole.ASSISTANT, "",
            agent_id=boss_cfg.id, tool_calls=_tc_list,
        )
        for tc in tool_calls:
            _result_content = tool_results.get(tc.id, "")
            await orch._sessions.append(
                project_id, MessageRole.TOOL, _result_content,
                agent_id=boss_cfg.id, tool_call_id=tc.id,
                tool_name=tc.function.name,
            )

        # Nächste LLM-Runde
        try:
            response = await orch._llm_call(boss_cfg, current_messages, litellm_tools)
        except Exception as e:
            logger.error("LLM-Fehler in Tool-Loop: %s", e)
            return "[Fehler] LLM nicht erreichbar — bitte später erneut versuchen.", workers_used

    return response.choices[0].message.content or "", workers_used
