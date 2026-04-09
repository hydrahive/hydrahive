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

from .session_metrics import metrics as _metrics

logger = logging.getLogger(__name__)


def _parse_dispatch_calls(tool_calls: list) -> list[dict]:
    """Extrahiert dispatch_task-Aufrufe aus LLM Tool-Calls (#415: +task_id, +depends_on)."""
    dispatches = []
    for tc in tool_calls:
        if tc.function.name != "dispatch_task":
            continue
        try:
            args = json.loads(tc.function.arguments)
            dispatches.append({
                "call_id":    tc.id,
                "worker_id":  args["worker_id"],
                "task":       args["task"],
                "context":    args.get("context", ""),
                "task_id":    args.get("task_id", tc.id),  # Fallback: LLM call_id
                "depends_on": args.get("depends_on", []),
            })
        except (json.JSONDecodeError, KeyError) as e:
            logger.warning("Ungültiger dispatch_task-Aufruf: %s", e)
    return dispatches


def _validate_dag(dispatches: list[dict]) -> str | None:
    """Prüft DAG auf Zyklen und unbekannte Referenzen. Gibt Fehlermeldung oder None zurück."""
    known_ids = {d["task_id"] for d in dispatches}
    for d in dispatches:
        for dep in d["depends_on"]:
            if dep not in known_ids:
                return f"Task '{d['task_id']}' referenziert unbekannte Abhängigkeit '{dep}'"
    # Zyklenerkennung (DFS)
    WHITE, GREY, BLACK = 0, 1, 2
    color: dict[str, int] = {d["task_id"]: WHITE for d in dispatches}
    adj: dict[str, list[str]] = {d["task_id"]: list(d["depends_on"]) for d in dispatches}
    def _dfs(node: str) -> bool:
        color[node] = GREY
        for dep in adj.get(node, []):
            if color.get(dep) == GREY:
                return True  # Zyklus
            if color.get(dep) == WHITE and _dfs(dep):
                return True
        color[node] = BLACK
        return False
    for tid in known_ids:
        if color[tid] == WHITE and _dfs(tid):
            return f"Zyklische Abhängigkeit erkannt bei Task '{tid}'"
    return None


# #434: Auto-Worker-Selection Strategien
def _select_worker_auto(orch, project_cfg, task: str) -> str | None:
    """Wählt automatisch den besten Worker für einen Task (capability-match via Identity-Keywords)."""
    workers = project_cfg.agents.workers
    if not workers:
        return None
    # Keyword-Matching: Task-Text gegen Worker-Identity
    best_worker = None
    best_score = 0
    task_lower = task.lower()
    for wid in workers:
        cfg = orch._discovery.get(wid)
        if not cfg:
            continue
        identity = (cfg.identity or "").lower()
        # Simpels Scoring: Anzahl überlappender Wörter
        task_words = set(task_lower.split())
        identity_words = set(identity.split())
        score = len(task_words & identity_words)
        if score > best_score:
            best_score = score
            best_worker = wid
    # Fallback: round-robin (erster Worker)
    return best_worker or workers[0]


async def _dispatch_dag(
    orch,
    project_cfg: ProjectConfig,
    dispatches: list[dict],
    context: str,
) -> list[DispatchResult]:
    """
    DAG-aware Task-Dispatch (#415): Tasks mit Abhängigkeiten werden in der
    richtigen Reihenfolge ausgeführt. Tasks ohne Abhängigkeiten laufen parallel.
    Cascade Failure: fehlgeschlagene Tasks blockieren alle Abhängigen.
    """
    # #434: Auto-Worker-Selection wenn worker_id="auto"
    for d in dispatches:
        if d["worker_id"] == "auto":
            auto_worker = _select_worker_auto(orch, project_cfg, d["task"])
            if auto_worker:
                d["worker_id"] = auto_worker
                logger.info("Auto-assigned worker '%s' für Task: %s", auto_worker, d["task"][:60])

    # #526: Built-in Workers sind immer verfügbar
    from .built_in_workers import is_builtin_worker
    allowed_workers = set(project_cfg.agents.workers)
    has_deps = any(d.get("depends_on") for d in dispatches)

    # Kein DAG → alter Pfad (flat parallel)
    if not has_deps:
        tasks = []
        for d in dispatches:
            if d["worker_id"] not in allowed_workers and not is_builtin_worker(d["worker_id"]):
                logger.warning("dispatch_task für '%s' abgelehnt — nicht im Projekt", d["worker_id"])
                async def _rejected(d=d) -> DispatchResult:
                    return DispatchResult(worker_id=d["worker_id"], task=d["task"],
                                         result="", success=False, error="Agent nicht dem Projekt zugewiesen",
                                         task_id=d.get("task_id"))
                tasks.append(_rejected())
                continue
            tasks.append(_run_worker_task(orch, d))
        return list(await asyncio.gather(*tasks, return_exceptions=False))

    # DAG-Validierung
    err = _validate_dag(dispatches)
    if err:
        logger.error("DAG-Validierung fehlgeschlagen: %s", err)
        return [DispatchResult(worker_id=d["worker_id"], task=d["task"], result="",
                               success=False, error=f"DAG-Fehler: {err}", task_id=d.get("task_id"))
                for d in dispatches]

    # DAG-Execution Loop
    task_map = {d["task_id"]: d for d in dispatches}
    completed: dict[str, DispatchResult] = {}
    failed_ids: set[str] = set()
    results: list[DispatchResult] = []

    logger.info("DAG-Dispatch: %d Tasks, Dependencies: %s",
                len(dispatches),
                {d["task_id"]: d["depends_on"] for d in dispatches if d["depends_on"]})

    max_waves = len(dispatches) + 1  # Safety gegen Endlosloop
    for _wave in range(max_waves):
        # Finde Tasks die bereit sind (alle Deps completed + nicht selbst fertig/failed)
        ready = []
        for d in dispatches:
            tid = d["task_id"]
            if tid in completed or tid in failed_ids:
                continue
            deps = d["depends_on"]
            # Cascade: wenn eine Dep fehlgeschlagen ist → dieser Task auch
            failed_deps = [dep for dep in deps if dep in failed_ids]
            if failed_deps:
                res = DispatchResult(
                    worker_id=d["worker_id"], task=d["task"], result="",
                    success=False, error=f"Cascade Failure: Abhängigkeit(en) {failed_deps} fehlgeschlagen",
                    task_id=tid,
                )
                failed_ids.add(tid)
                results.append(res)
                continue
            # Alle Deps müssen completed sein
            if all(dep in completed for dep in deps):
                ready.append(d)

        if not ready:
            break  # Alles erledigt oder Deadlock

        # Ready-Tasks parallel ausführen
        wave_tasks = []
        for d in ready:
            if d["worker_id"] not in allowed_workers:
                async def _rejected(d=d) -> DispatchResult:
                    return DispatchResult(worker_id=d["worker_id"], task=d["task"],
                                         result="", success=False, error="Agent nicht dem Projekt zugewiesen",
                                         task_id=d.get("task_id"))
                wave_tasks.append(_rejected())
            else:
                # Dependency-Ergebnisse als Kontext mitgeben
                dep_context = d.get("context", "")
                for dep_id in d["depends_on"]:
                    dep_res = completed.get(dep_id)
                    if dep_res and dep_res.result:
                        dep_context += f"\n\n[Ergebnis von {dep_id}]: {dep_res.result[:2000]}"
                d_with_ctx = {**d, "context": dep_context.strip()}
                wave_tasks.append(_run_worker_task(orch, d_with_ctx))

        wave_results = await asyncio.gather(*wave_tasks, return_exceptions=False)
        logger.info("DAG Wave %d: %d Tasks (%s)",
                    _wave + 1, len(wave_results),
                    ", ".join(d["task_id"] for d in ready))

        for res in wave_results:
            res.task_id = res.task_id or next((d["task_id"] for d in ready if d["worker_id"] == res.worker_id), None)
            if res.success:
                completed[res.task_id] = res
            else:
                failed_ids.add(res.task_id)
            results.append(res)

    return results

# Alias für Abwärtskompatibilität (orchestrator.py importiert diesen Namen)
_dispatch_parallel = _dispatch_dag


async def _run_builtin_worker(orch, dispatch: dict, profile: dict) -> DispatchResult:
    """#526: Built-in Worker ausführen — nutzt Boss-LLM mit speziellem System-Prompt."""
    worker_id = dispatch["worker_id"]
    task = dispatch["task"]
    context = dispatch.get("context", "")
    task_id = dispatch.get("task_id")

    logger.info("Built-in Worker '%s': %s", worker_id, task[:60])

    messages = [
        {"role": "system", "content": profile["system_prompt"]},
    ]
    if context:
        messages.append({"role": "user", "content": f"Kontext: {context}"})
    messages.append({"role": "user", "content": task})

    # Built-in Workers nutzen das Boss-LLM (kein eigener Agent nötig)
    # Tool-Schemas nur für erlaubte Tools
    allowed = set(profile.get("allowed_tools", []))
    tools = []
    for tool_obj in orch._reg.values():
        if hasattr(tool_obj, "id") and tool_obj.id in allowed:
            tools.append({
                "type": "function",
                "function": {
                    "name": tool_obj.id,
                    "description": tool_obj.description,
                    "parameters": tool_obj.parameters,
                },
            })

    try:
        import litellm
        from .orchestrator_llm import _llm_with_retry
        from .orchestrator_tools import execute_tool_call, format_tool_result

        # #519: Verify-Worker bekommt Mini-Tool-Loop (max 5 Runden)
        # Andere Built-ins (explore, plan, review) bleiben Single-Shot
        use_tool_loop = worker_id == "verify" and tools
        max_rounds = 5 if use_tool_loop else 1

        for _round in range(max_rounds):
            resp = await _llm_with_retry(lambda: litellm.acompletion(
                model="claude-haiku-4-5-20251001",
                messages=messages,
                tools=tools if tools else None,
                max_tokens=4000,
                drop_params=True,
            ))
            msg = resp.choices[0].message

            # Keine Tool-Calls → fertig
            tool_calls = getattr(msg, "tool_calls", None)
            if not tool_calls or not use_tool_loop:
                result = msg.content or ""
                break

            # Tool-Calls ausführen
            messages.append({
                "role": "assistant",
                "content": msg.content or "",
                "tool_calls": [
                    {"id": tc.id, "type": "function",
                     "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                    for tc in tool_calls
                ],
            })
            for tc in tool_calls:
                tc_args = json.loads(tc.function.arguments or "{}")
                # Safety: Nur erlaubte Tools ausführen
                if tc.function.name not in allowed:
                    messages.append({"role": "tool", "tool_call_id": tc.id,
                                     "content": f"[Tool '{tc.function.name}' nicht erlaubt für Built-in Worker]"})
                    continue
                try:
                    tc_result, _ = await execute_tool_call(
                        orch, boss_cfg=None, project_id=dispatch.get("project_id", ""),
                        tool_name=tc.function.name, tool_input=tc_args,
                    )
                    messages.append({"role": "tool", "tool_call_id": tc.id,
                                     "content": format_tool_result(tc_result)})
                except Exception as tc_err:
                    messages.append({"role": "tool", "tool_call_id": tc.id,
                                     "content": f"[Fehler: {tc_err}]"})

            logger.debug("Built-in Worker '%s' Runde %d/%d", worker_id, _round + 1, max_rounds)
        else:
            # max_rounds erreicht — letzte Antwort nehmen
            result = msg.content or "[Max. Runden erreicht]"

        return DispatchResult(worker_id=worker_id, task=task, result=result, task_id=task_id)
    except Exception as e:
        logger.error("Built-in Worker '%s' Fehler: %s", worker_id, e)
        return DispatchResult(
            worker_id=worker_id, task=task, result="",
            success=False, error=str(e), task_id=task_id,
        )


async def _run_worker_task(orch, dispatch: dict) -> DispatchResult:
    """Einen Worker-Agenten mit einem Task beauftragen."""
    worker_id = dispatch["worker_id"]
    task      = dispatch["task"]
    context   = dispatch.get("context", "")
    task_id   = dispatch.get("task_id")

    # #526: Built-in Workers (explore, plan) — virtueller Agent mit eigenem Prompt
    from .built_in_workers import get_builtin_worker
    builtin = get_builtin_worker(worker_id)
    if builtin:
        return await _run_builtin_worker(orch, dispatch, builtin)

    worker_cfg = orch._discovery.get(worker_id)
    if not worker_cfg:
        return DispatchResult(
            worker_id=worker_id, task=task, result="",
            success=False, error=f"Agent '{worker_id}' nicht in Discovery",
            task_id=task_id,
        )

    logger.info("Dispatche Task an %s: %s", worker_id, task[:60])

    # #522: Worktree-Isolation wenn Feature-Flag aktiv
    _wt_info = None
    try:
        from .settings import settings as _s
        if _s.worktree_isolation:
            from .worktree_manager import worktree_manager as _wm
            # Workspace des Projekts finden (wenn Git-Repo vorhanden)
            _project_id = dispatch.get("project_id", "")
            if _project_id:
                _workspace = Path(f"/tmp/hydrahive-git/{_project_id}")
                if (_workspace / ".git").exists():
                    _wt_info = await _wm.create_worktree(_workspace, task_id or worker_id, worker_id)
                    logger.info("Worker '%s' arbeitet in Worktree: %s", worker_id, _wt_info.path)
    except Exception as _wt_err:
        logger.debug("Worktree creation skipped: %s", _wt_err)

    # Worker-LLM-Aufruf (kein Tool-Calling für Worker — nur ausführen)
    messages = [
        {"role": "system", "content": f"Du bist {worker_cfg.identity}. Erledige den folgenden Task präzise und knapp."},
    ]
    if context:
        messages.append({"role": "user", "content": f"Kontext: {context}"})
    messages.append({"role": "user", "content": task})

    # #522: ContextVar setzen wenn Worktree aktiv
    _wt_token = None
    if _wt_info:
        from .tool_registry import _active_worktree
        _wt_token = _active_worktree.set(_wt_info.path)

    try:
        response = await orch._llm_call(worker_cfg, messages, tools=None)
        result = response.choices[0].message.content or ""
        return DispatchResult(worker_id=worker_id, task=task, result=result, task_id=task_id)
    except Exception as e:
        logger.error("Worker '%s' LLM-Fehler: %s", worker_id, e)
        # #362: Fehlgeschlagene Dispatches notifyen
        try:
            from .tool_registry import _notify
            _notify(dispatch.get("call_id", ""), "agent_error",
                    f"Worker-Dispatch fehlgeschlagen: {worker_id}",
                    f"Task: {task[:80]}\nFehler: {e}",
                    link=f"/agents")
        except Exception:
            pass
        return DispatchResult(
            worker_id=worker_id, task=task, result="",
            success=False, error=str(e), task_id=task_id,
        )
    finally:
        # #522: ContextVar zurücksetzen + Worktree Cleanup
        if _wt_token is not None:
            from .tool_registry import _active_worktree
            _active_worktree.reset(_wt_token)
        if _wt_info:
            try:
                from .worktree_manager import worktree_manager as _wm
                await _wm.cleanup_worktree(task_id or worker_id, merge=True)
            except Exception as _wt_clean_err:
                logger.warning("Worktree cleanup failed: %s", _wt_clean_err)


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
             if json.loads(tc.function.arguments).get("task_id") == result.task_id
             or json.loads(tc.function.arguments).get("worker_id") == result.worker_id),
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

        # #512: Tool-Round Metriken
        _metrics.record_tool_round(project_id, len(tool_calls))

        signature = _tool_call_signature_fn(tool_calls)
        last_signature, repeated_signature_count, should_abort = check_repeated_signature(
            signature, last_signature, repeated_signature_count, threshold=2,
        )

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
            results = await _dispatch_dag(orch, project_cfg, dispatches, context="")
            # #415: task_id-basiertes Mapping (Fallback: worker_id)
            call_id_by_task_id = {d["task_id"]: d["call_id"] for d in dispatches}
            for res in results:
                call_id = call_id_by_task_id.get(res.task_id) or next(
                    (tc.id for tc in dispatch_tcs
                     if json.loads(tc.function.arguments).get("worker_id") == res.worker_id),
                    "unknown"
                )
                content = res.result if res.success else f"[Fehler] {res.error}"
                tool_results[call_id] = content
                if res.worker_id not in workers_used:
                    workers_used.append(res.worker_id)

        # #418: parallel-safe Tools via asyncio.gather, Rest sequentiell
        parallel_tcs = []
        sequential_tcs = []
        for tc in other_tcs:
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

        # Nächste LLM-Runde
        try:
            response = await orch._llm_call(boss_cfg, current_messages, litellm_tools)
        except Exception as e:
            logger.error("LLM-Fehler in Tool-Loop: %s", e)
            return "[Fehler] LLM nicht erreichbar — bitte später erneut versuchen.", workers_used

    return response.choices[0].message.content or "", workers_used
