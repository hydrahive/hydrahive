"""
orchestrator_stream.py — SSE-Streaming Response (#386)

Streaming-Version des Orchestrators: yieldet SSE-Chunks für die
Chat-API. Unterstützt Anthropic OAuth, OpenAI Codex und litellm.
"""

import asyncio as _asyncio
import json as _json
import logging
import os
from typing import Any

from .agent_config import AgentConfig
from .session_manager import MessageRole
from .orchestrator_llm import (
    _should_failover,
    _load_claude_oauth_token,
    _load_openai_codex_token,
    _apply_cache_control,
    check_llm_provider_available,
    _current_project_id,
)
from .session_metrics import metrics as _metrics
from .turn_journal import journal as _journal, EventType as _JE
from .orchestrator_context import (
    _history_token_budget,
    _estimate_tokens,
)
from .orchestrator_tools import (
    _truncate_tool_result, check_repeated_signature, execute_tool_call,
    format_tool_detail, format_tool_result,
)

logger = logging.getLogger(__name__)

# SSE-Keepalive: verhindert dass Proxies (nginx) oder Browser die Verbindung
# bei langen Tool-Ausführungen (Compile, SSH etc.) wegen Inaktivität schließen.
_KEEPALIVE_INTERVAL = 15  # Sekunden


async def _keepalive_comment_stream():
    """Yieldet SSE-Kommentare alle 15s — hält die Verbindung am Leben."""
    while True:
        await _asyncio.sleep(_KEEPALIVE_INTERVAL)
        yield ": keepalive\n\n"


def _extract_tool_image(result: Any, tool_name: str) -> str | None:
    """SSE-Event-JSON für Inline-Bild-Anzeige im Chat.

    Unterstützte Tool-Result-Shapes (Priorität absteigend):
    - ``{"image_data_uri": "data:image/png;base64,..."}`` → direkt einbettbar
      (#791 Phase 1: image_generate legt das zusaetzlich ins Result).
    - ``{"image_base64": "...", "format": "png"}`` → data-URI (legacy).
    - ``{"artifacts": [{"mime": "image/...", "download_url": "/me/jobs/..."}]}``
      → HTTP-URL (nur als letzter Fallback — <img>-Tag schickt keine Cookies
      mit, darum 403 auf /me/jobs/*/artifacts/*; die data_uri-Variante ist
      der robuste Pfad).

    Event-Shape: ``{"type": "tool_image", "tool_image": <src>, "tool_name": <id>}``.

    Returns None wenn kein Bild im Result ist.
    """
    if not isinstance(result, dict):
        return None

    # #791: Bevorzugt data-URI (umgeht das Cookie-Problem bei <img>-Tags).
    data_uri = result.get("image_data_uri")
    if isinstance(data_uri, str) and data_uri.startswith("data:image/"):
        return _json.dumps({
            "type": "tool_image",
            "tool_image": data_uri,
            "tool_name": tool_name,
        })

    if "image_base64" in result:
        fmt = result.get("format", "png")
        return _json.dumps({
            "type": "tool_image",
            "tool_image": f"data:image/{fmt};base64,{result['image_base64']}",
            "tool_name": tool_name,
        })

    # #773 Followup: Jobs-basierte Media-Tools (image/video/music) liefern
    # Artifacts mit download_url. Als Fallback wenn image_data_uri fehlt.
    artifacts = result.get("artifacts")
    if isinstance(artifacts, list):
        for a in artifacts:
            if not isinstance(a, dict):
                continue
            mime = str(a.get("mime") or "")
            url = str(a.get("download_url") or "")
            if mime.startswith("image/") and url:
                return _json.dumps({
                    "type": "tool_image",
                    "tool_image": url,
                    "tool_name": tool_name,
                })

    return None


async def handle_message_stream(
    orch,
    project_id: str,
    project_cfg,
    content: str,
    sender: str = "user",
    execution_mode: str | None = None,
    *,
    request_user: str | None = None,
):
    """
    Streaming-Version von handle_message.
    Yieldet SSE-Chunks: data: <text>\\n\\n
    Bei Quota/Overload-Fehler: automatischer Failover auf fallback_models.
    Abschluss: data: {done: true}\\n\\n
    """
    from .orchestrator import _dedup_tools
    from .orchestrator_tools import _tool_call_signature as _tool_call_signature_fn
    from . import tool_registry as _tool_reg

    # v2: Projekt ist sein eigener Agent — kein Boss-Agent nötig
    if getattr(project_cfg, "is_v2", False):
        from .agent_config import agent_config_from_project
        boss_id = project_cfg.id
        boss_cfg = agent_config_from_project(project_cfg)
    else:
        # v1: Boss-Agent aus Discovery laden
        boss_id = project_cfg.agents.boss
        boss_cfg = orch._discovery.get(boss_id)
    if not boss_cfg:
        yield f"data: {_json.dumps({'error': f'Boss-Agent {boss_id} nicht gefunden'})}\n\n"
        return

    # #750: Token-Budget Pre-Check vor erstem LLM-Call (Streaming-Pfad).
    # Hard-Stop verhindert Token-Burn in hängenden/loopenden Agents.
    _rl = getattr(_tool_reg, "_rate_limiter", None)
    if _rl is not None:
        from .rate_limiter import TokenBudgetExceeded as _TBE
        try:
            _rl.check_token_budget(boss_cfg.id)
        except _TBE as _budget_exc:
            _msg = (
                f"⛔ Token-Budget überschritten (#750): Agent '{boss_cfg.id}' hat "
                f"~{_budget_exc.tokens_used} Tokens in der letzten Stunde verbraucht "
                f"(Hard-Limit: {_budget_exc.limit}). Der Agent pausiert bis sich das "
                "1-Stunden-Fenster leert."
            )
            await orch._sessions.append(
                project_id, MessageRole.ASSISTANT, _msg, agent_id=boss_cfg.id,
            )
            yield f"data: {_json.dumps({'text': _msg})}\n\n"
            yield f"data: {_json.dumps({'done': True, 'reason': 'token_budget_hard_stop'})}\n\n"
            return

    _handoff_mtime_at_prompt = orch._forced_abort_handoff_mtime(boss_cfg)

    # Stale Interrupt-Flags löschen (von eventuell vorangegangenem abgebrochenem Request)
    from .tool_registry import clear_interrupt as _clear_interrupt
    _clear_interrupt(project_id)

    # Token-Usage Akkumulator für diese Session (über alle Tool-Runden)
    _usage: dict[str, int] = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0, "rounds": 0}

    # #414: Image-Blocks extrahieren — Text für Session, volle Blocks für LLM
    _vision_blocks = None  # list[dict] wenn Images dabei sind
    _text_content = content
    if isinstance(content, list):
        _vision_blocks = content
        _text_content = next((b.get("text", "") for b in content if b.get("type") == "text"), "")

    # Session + System-Prompt aufbauen
    user_msg_saved = False
    _save_content = _text_content if isinstance(_text_content, str) else str(_text_content)
    if _vision_blocks:
        _save_content = f"[Bild-Nachricht] {_save_content}"
    await orch._sessions.append(project_id, MessageRole.USER, _save_content, agent_id=sender)
    user_msg_saved = True
    # #523: Turn Journal — User-Message aufzeichnen
    _sid = getattr(orch._sessions.get_active(project_id), "id", "unknown") if orch._sessions.get_active(project_id) else "unknown"
    _journal.append(_sid, project_id, _JE.USER_MESSAGE, {"length": len(_save_content)})

    # Context-Kompaktierung vor dem LLM-Aufruf
    await orch._compact_if_needed(project_id, boss_cfg)

    _content_str = _text_content if isinstance(_text_content, str) else str(_text_content)
    _refresh = _content_str.strip().startswith("!refresh")
    if _refresh:
        content = _content_str.strip()[8:].strip()
    # #636: einheitlicher Builder, kein Fallback-Pfad mehr.
    # Builder-Exception propagiert — laute Fail statt stiller Kontextdrift.
    from .orchestrator_context import build_system_prompt
    _active_session = orch._sessions.get_active(project_id)
    _static_prompt, _dynamic_prompt = await build_system_prompt(
        boss_cfg, _content_str, invalidate=_refresh, session=_active_session,
        request_user=request_user,
    )
    # v2 (#589): Worker-Kontext entfernt — kein Dispatch-Modell mehr
    system_prompt = (_static_prompt + "\n\n" + _dynamic_prompt).strip() if _dynamic_prompt else _static_prompt
    # #485: Frustration Detection — System-Prompt-Injection wenn User genervt ist
    from .frustration_detection import get_frustration_injection
    _frustration_hint = get_frustration_injection(_text_content if isinstance(_text_content, str) else str(content))
    if _frustration_hint:
        system_prompt += _frustration_hint
        logger.debug("Frustration detected in message from %s", sender)
    # #348: Token-basierte History statt max_messages=10 (OpenClaw-Strategie)
    _sys_prompt_tokens_s = _estimate_tokens(system_prompt)
    _hist_budget_s = _history_token_budget(boss_cfg.llm.model, system_prompt_tokens=_sys_prompt_tokens_s)
    _raw_history   = orch._sessions.get_context(
        project_id,
        max_history_tokens=_hist_budget_s,
    )
    # #637: role:"tool" bleibt strukturiert erhalten — nicht mehr filtern.
    history       = [m for m in _raw_history if m.get("role") in ("user", "assistant", "tool")]
    # #414: Letzte User-Message mit Vision-Blocks ersetzen
    if _vision_blocks and history:
        for i in range(len(history) - 1, -1, -1):
            if history[i].get("role") == "user":
                history[i] = {"role": "user", "content": _vision_blocks}
                break
    # v2: Core-Tools + geladene deferred Tools (#620 Phase 4).
    # Wird pro Runde via _build_stream_tools() neu aufgebaut, damit
    # ToolSearch-Käufe im gleichen Loop wirksam werden.
    from .orchestrator_mcp import filter_mcp_schemas_by_loaded
    from .tool_registry import loaded_deferred_ids as _loaded_ids, session_key as _skey_fn

    _all_mcp_schemas = await orch._mcp_schemas_for_agent(boss_cfg)

    def _build_stream_tools() -> list[dict] | None:
        extra = orch._allowed_tool_map(
            boss_cfg, execution_mode, user_text=_text_content, project_id=project_id,
        )
        schemas = orch._reg.as_litellm_tools(list(extra.values())) if extra else []
        if _all_mcp_schemas:
            loaded = _loaded_ids(_skey_fn(project_id, boss_cfg.id))
            schemas = schemas + filter_mcp_schemas_by_loaded(_all_mcp_schemas, loaded)
        return _dedup_tools(schemas) if schemas else None

    litellm_tools = _build_stream_tools()

    # Anti-Halluzinations-Guard: System-Prompt ergänzen mit tatsächlich verfügbaren Tools
    # Verhindert dass der Agent Tools als Text schreibt statt sie echt aufzurufen
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
        system_prompt = system_prompt + _tool_guard
    else:
        system_prompt = system_prompt + (
            "\n\n## WARNUNG: Keine Tools verfügbar\n"
            "Dir stehen aktuell KEINE Tools zur Verfügung. "
            "Schreibe KEINE Tool-Aufrufe als Text. Antworte nur mit Text."
        )

    messages      = [{"role": "system", "content": system_prompt}] + history

    sys_tokens_s  = _sys_prompt_tokens_s
    hist_tokens_s = sum(
        _estimate_tokens(m.get("content", "") if isinstance(m.get("content"), str) else "")
        for m in history
    )
    tool_tokens_s = _estimate_tokens(_json.dumps(litellm_tools or []))
    logger.info(
        "token-budget [stream] proj=%s sys≈%d hist≈%d/%d (%d msgs) tools≈%d total≈%d",
        project_id, sys_tokens_s, hist_tokens_s, _hist_budget_s, len(history), tool_tokens_s,
        sys_tokens_s + hist_tokens_s + tool_tokens_s,
    )

    # #778: Pre-Call-Budget-Check MIT Call-Groessen-Schaetzung (Streaming-Pfad).
    # Die Werte sind oben schon berechnet (sys_tokens_s+hist_tokens_s+tool_tokens_s).
    # check_token_budget macht den hard>0-Check selbst.
    if _rl is not None:
        _estimated_call = sys_tokens_s + hist_tokens_s + tool_tokens_s
        try:
            _rl.check_token_budget(boss_cfg.id, estimated_next_call_tokens=_estimated_call)
        except _TBE as _budget_exc:
            _msg = (
                f"⛔ Token-Budget-Block (#778, Stream): Geschaetzter naechster Call "
                f"(~{_estimated_call} Tokens) wuerde Hard-Limit {_budget_exc.limit} "
                f"sprengen. Kontext reduzieren."
            )
            await orch._sessions.append(
                project_id, MessageRole.ASSISTANT, _msg, agent_id=boss_cfg.id,
            )
            yield f"data: {_json.dumps({'text': _msg})}\n\n"
            yield f"data: {_json.dumps({'done': True, 'reason': 'token_budget_estimate_block'})}\n\n"
            return
        except TypeError:
            pass  # Test-Setups mit MagicMock-Limiter

    # #433 + #445: Context-Info als SSE-Event für Frontend
    yield f"data: {_json.dumps({'_context_info': {'system_tokens': sys_tokens_s, 'history_tokens': hist_tokens_s, 'tool_tokens': tool_tokens_s, 'history_messages': len(history), 'history_budget': _hist_budget_s}})}\n\n"

    # #512: project_id für Retry/Failover-Metriken setzen
    _current_project_id.set(project_id)

    models_to_try = [boss_cfg.llm.model] + boss_cfg.llm.fallback_models

    _provider_err = check_llm_provider_available(models_to_try, ollama_base_url=boss_cfg.llm.ollama_base_url)
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
                        async for chunk in _stream_codex(
                            orch, boss_cfg, boss_id, project_id, content,
                            messages, litellm_tools, codex_token, _model_name,
                            execution_mode, _usage, request_user=request_user,
                        ):
                            if isinstance(chunk, dict):
                                full_response = chunk.get("_full_response", full_response)
                                streamed_any = chunk.get("_streamed_any", streamed_any)
                            else:
                                yield chunk
                        break

                # --- Claude Max OAuth ---
                _is_claude    = _model_name.startswith(("claude-", "anthropic/"))
                if _is_claude:
                    # Terminal-Token (sk-ant-oat01-) auch über OAuth-Pfad für Rate-Limit Headers
                    _env_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
                    oauth_token = _env_key if _env_key.startswith("sk-ant-oat01-") else _load_claude_oauth_token()
                else:
                    oauth_token = ""

                if oauth_token:
                    async for chunk in _stream_anthropic_oauth(
                        orch, boss_cfg, boss_id, project_id, content,
                        messages, litellm_tools, oauth_token, _model_name,
                        execution_mode, _usage,
                        request_user=request_user,
                        static_prompt=_static_prompt, dynamic_prompt=_dynamic_prompt,
                    ):
                        if isinstance(chunk, dict):
                            full_response = chunk.get("_full_response", full_response)
                            streamed_any = chunk.get("_streamed_any", streamed_any)
                        else:
                            yield chunk

                else:
                    # litellm Streaming (Ollama / OpenAI) mit Tool-Loop
                    async for chunk in _stream_litellm(
                        orch, boss_cfg, boss_id, project_id, content,
                        messages, litellm_tools, _model_name,
                        execution_mode, _usage, request_user=request_user,
                    ):
                        if isinstance(chunk, dict):
                            full_response = chunk.get("_full_response", full_response)
                            streamed_any = chunk.get("_streamed_any", streamed_any)
                        else:
                            yield chunk

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
        _is_fallback = _model_name != boss_cfg.llm.model
        _stream_meta: dict = {}
        if _usage.get("input") or _usage.get("output"):
            _stream_meta = {
                "model":              _model_name,
                "input_tokens":       _usage.get("input",       0),
                "output_tokens":      _usage.get("output",      0),
                "cache_write_tokens": _usage.get("cache_write", 0),
                "cache_read_tokens":  _usage.get("cache_read",  0),
            }
        if full_response:  # Leere Responses nicht in Session speichern
            await orch._sessions.append(
                project_id, MessageRole.ASSISTANT,
                full_response, agent_id=boss_cfg.id,
                **_stream_meta,
            )
            orch._clear_forced_abort_handoff_if_unchanged(boss_cfg, _handoff_mtime_at_prompt)
        total_tokens = _usage.get("input", 0) + _usage.get("output", 0)
        if total_tokens > 0 and _tool_reg._rate_limiter is not None:
            _tool_reg._rate_limiter.track_token_usage(boss_cfg.id, total_tokens)

        # #512: Streaming-LLM-Call Metriken erfassen (akkumuliert über alle Rounds)
        _metrics.record_llm_call(
            project_id,
            model=_model_name,
            prompt_tokens=sys_tokens_s,
            history_tokens=hist_tokens_s,
            tool_tokens=tool_tokens_s,
            input_tokens=_usage.get("input", 0),
            output_tokens=_usage.get("output", 0),
            cache_read=_usage.get("cache_read", 0),
            cache_write=_usage.get("cache_write", 0),
        )
        _done_payload: dict = {
            'done': True,
            'session_id': None,
            'usage': _usage,
            'model': _model_name,
            'is_fallback': _is_fallback,
        }
        yield f"data: {_json.dumps(_done_payload)}\n\n"

        # #488: Prompt Speculation — Follow-up Vorschläge generieren
        try:
            from .prompt_speculation import generate_suggestions
            _suggestions = await generate_suggestions(
                user_text=_text_content if isinstance(_text_content, str) else str(content),
                assistant_response=full_response,
            )
            if _suggestions:
                yield f"data: {_json.dumps({'suggestions': _suggestions})}\n\n"
        except Exception as _spec_err:
            logger.debug("Prompt speculation failed: %s", _spec_err)

    except Exception as e:
        err_str = str(e).lower()
        _context_errors = ("prompt is too long", "maximum context length", "context_length_exceeded",
                           "error in input stream", "input too long", "request too large")
        if any(s in err_str for s in _context_errors):
            _metrics.record_overflow(project_id)
            _journal.append(_sid, project_id, _JE.OVERFLOW, {"error": str(e)[:200]})
            # #499/#517: Reactive Compaction — erst compacten, dann retry
            logger.warning(
                "Context-Overflow (Streaming) für Projekt '%s' — versuche Reactive Compaction. Fehler: %s",
                project_id, str(e)[:120],
            )
            try:
                from .orchestrator_context import _compact_if_needed as _compact_fn
                await _compact_fn(orch._sessions, project_id, boss_cfg, keep_last=4)
                yield f"data: {_json.dumps({'text': '[Kontext wurde automatisch kompaktiert — fahre fort…]\\n\\n'})}\n\n"
                # Kompaktierten Context neu aufbauen und zweiten Streaming-Versuch starten.
                # #636: derselbe einheitliche Builder wie im Hauptpfad.
                from .orchestrator_context import build_system_prompt as _bsp_retry
                _retry_history = orch._sessions.get_context(project_id)
                _retry_session = orch._sessions.get_active(project_id)
                _retry_static, _retry_dynamic = await _bsp_retry(
                    boss_cfg, content, session=_retry_session,
                    request_user=request_user,
                )
                _retry_sys = (_retry_static + "\n\n" + _retry_dynamic).strip() if _retry_dynamic else _retry_static
                _retry_msgs = [{"role": "system", "content": _retry_sys}] + _retry_history
                # Vereinfachter Retry: non-streaming LLM-Call für die Recovery
                _retry_resp = await orch._llm_call(boss_cfg, _retry_msgs, litellm_tools)
                _retry_text = ""
                if hasattr(_retry_resp, "choices") and _retry_resp.choices:
                    _retry_text = _retry_resp.choices[0].message.content or ""
                if _retry_text:
                    yield f"data: {_json.dumps({'text': _retry_text})}\n\n"
                    await orch._sessions.append(project_id, MessageRole.ASSISTANT, _retry_text, agent_id=boss_id)
                logger.info("Reactive Compaction (Streaming) erfolgreich — Projekt '%s' recovered", project_id)
            except Exception as retry_err:
                logger.error(
                    "Reactive Compaction + Retry fehlgeschlagen für '%s': %s — Session-Reset",
                    project_id, str(retry_err)[:120],
                )
                _metrics.record_session_reset(project_id)
                await orch._sessions.new_session(project_id)
                yield f"data: {_json.dumps({'error': 'Die Konversation war zu lang und konnte nicht kompaktiert werden. Session wurde zurückgesetzt — bitte wiederhole deine letzte Nachricht.', 'session_reset': True})}\n\n"
        else:
            logger.error("Streaming-Fehler: %s", e)
            if user_msg_saved:
                await orch._sessions.pop_last(project_id)
            yield f"data: {_json.dumps({'error': str(e)})}\n\n"


# ---------------------------------------------------------------------------
# Provider-spezifische Streaming-Implementierungen
# ---------------------------------------------------------------------------

async def _stream_codex(
    orch, boss_cfg, boss_id, project_id, content,
    messages, litellm_tools, codex_token, model_name,
    execution_mode, _usage,
    *, request_user: str | None = None,
):
    """OpenAI Codex (ChatGPT Plus OAuth) — non-streaming mit Tool-Loop."""
    from .orchestrator_tools import _tool_call_signature as _tool_call_signature_fn

    codex_resp = await orch._openai_codex_call(
        boss_cfg, messages, litellm_tools, codex_token, model_name
    )
    msg = codex_resp.choices[0].message
    # #700: Token-Usage inkl. cache_read/cache_write akkumulieren.
    from .orchestrator_llm import _accumulate_codex_usage
    _accumulate_codex_usage(_usage, getattr(codex_resp, "usage", None))
    cur_messages = list(messages)
    last_signature: tuple[str, ...] | None = None
    repeated_signature_count = 0
    _fuzzy_history_codex: list[str] = []  # #618

    for _round in range(boss_cfg.max_tool_rounds):
        if not getattr(msg, "tool_calls", None):
            break
        signature = _tool_call_signature_fn(msg.tool_calls)
        from .orchestrator_tools import _fuzzy_fingerprint, check_fuzzy_loop
        for tc in msg.tool_calls:
            _fn = getattr(tc, "function", None)
            if _fn is None:
                continue
            _fuzzy_history_codex.append(
                _fuzzy_fingerprint(getattr(_fn, "name", "") or "", getattr(_fn, "arguments", "") or "")
            )
        last_signature, repeated_signature_count, should_abort = check_repeated_signature(
            signature, last_signature, repeated_signature_count, threshold=4,
        )
        abort_reason = "signature_abort"
        if not should_abort:
            _fuzzy_abort, _fuzzy_fp = check_fuzzy_loop(_fuzzy_history_codex)
            if _fuzzy_abort:
                logger.warning(
                    "Fuzzy-Loop erkannt (Codex): Pattern '%s' — Abbruch", (_fuzzy_fp or "")[:120],
                )
                should_abort = True
                abort_reason = "fuzzy_loop_abort"
        if should_abort:
            await orch._write_forced_abort_handoff(
                boss_cfg,
                cur_messages,
                reason=abort_reason,
                execution_mode=execution_mode,
            )
            final = await orch._finalize_tool_loop_response(
                boss_cfg, cur_messages,
                reason="wiederholte Tool-Signatur",
                execution_mode=execution_mode,
            )
            msg = final.choices[0].message
            break
        # Tool-Calls ausführen
        asst_tc = [
            {"id": tc.id, "item_id": getattr(tc, "item_id", tc.id), "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in msg.tool_calls
        ]
        cur_messages.append({"role": "assistant", "content": msg.content or "", "tool_calls": asst_tc})

        # Phase 1: SSE-Events senden + Tools in parallel/sequential splitten (#418)
        _parsed_args: dict[str, dict] = {}
        parallel_tcs = []
        sequential_tcs = []
        for tc in msg.tool_calls:
            _tc_args = _json.loads(tc.function.arguments or "{}")
            _parsed_args[tc.id] = _tc_args
            _tc_detail = format_tool_detail(tc.function.name, _tc_args)
            yield f"data: {_json.dumps({'tool_call': tc.function.name, 'tool_call_id': tc.id, 'tool_input': _tc_args, 'tool_detail': _tc_detail})}\n\n"
            # #486: Destructive Command Warning
            if tc.function.name in ("shell_exec", "wks_shell_exec"):
                from .destructive_warning import get_destructive_warning
                _dw = get_destructive_warning(_tc_args.get("command", ""))
                if _dw:
                    yield f"data: {_json.dumps({'tool_warning': _dw, 'tool_name': tc.function.name})}\n\n"
            # Tool-Call Info nur als SSE-Event, nicht in Session (wird am Ende der Runde strukturiert gespeichert)
            # Klassifizierung: parallel_safe oder sequential
            tool_obj = orch._resolve_allowed_tool(boss_cfg, tc.function.name, execution_mode)
            # #528: Tool Policy als Fallback für parallel_safe
            _is_parallel = getattr(tool_obj, "parallel_safe", None) if tool_obj else None
            if _is_parallel is None and tool_obj:
                from .context_lifecycle import get_tool_policy
                _is_parallel = get_tool_policy(tool_obj.id if hasattr(tool_obj, "id") else "").parallel_safe
            if _is_parallel:
                parallel_tcs.append(tc)
            else:
                sequential_tcs.append(tc)

        # Phase 2: Parallel-safe Tools gleichzeitig ausführen (#418)
        _tool_results: dict[str, Any] = {}  # tc.id → (result, tc)
        # #641: SSE-Bridge für tool_confirm_required-Events
        _codex_pending_sse: list[dict] = []
        _codex_confirm_signal = lambda ev: _codex_pending_sse.append(ev)
        if parallel_tcs:
            _par_tasks = [
                _asyncio.create_task(execute_tool_call(
                    orch, boss_cfg=boss_cfg, project_id=project_id,
                    tool_name=tc.function.name, tool_input=_parsed_args[tc.id],
                    execution_mode=execution_mode, user_text=content,
                    request_user=request_user,
                    tool_call_id=tc.id,
                    confirm_signal=_codex_confirm_signal,
                ))
                for tc in parallel_tcs
            ]
            # Keepalive während parallel Tools laufen
            _par_wait = 0.0
            while not all(t.done() for t in _par_tasks):
                await _asyncio.sleep(0.2)
                _par_wait += 0.2
                while _codex_pending_sse:
                    yield f"data: {_json.dumps(_codex_pending_sse.pop(0))}\n\n"
                if not all(t.done() for t in _par_tasks) and _par_wait >= _KEEPALIVE_INTERVAL:
                    yield ": keepalive\n\n"
                    _par_wait = 0.0
            for tc, task in zip(parallel_tcs, _par_tasks):
                result, _ = task.result()
                _tool_results[tc.id] = (result, tc)
            if len(parallel_tcs) > 1:
                logger.info("Streaming parallel: %d Tools (%s)",
                            len(parallel_tcs), ", ".join(tc.function.name for tc in parallel_tcs))

        # Phase 3: Sequentielle Tools nacheinander
        for tc in sequential_tcs:
            _tool_task = _asyncio.create_task(execute_tool_call(
                orch, boss_cfg=boss_cfg, project_id=project_id,
                tool_name=tc.function.name, tool_input=_parsed_args[tc.id],
                execution_mode=execution_mode, user_text=content,
                request_user=request_user,
                tool_call_id=tc.id,
                confirm_signal=_codex_confirm_signal,
            ))
            _elapsed_wait = 0.0
            while not _tool_task.done():
                await _asyncio.sleep(0.2)  # Schnelles Polling — Tool kann in <1s fertig sein
                _elapsed_wait += 0.2
                while _codex_pending_sse:
                    yield f"data: {_json.dumps(_codex_pending_sse.pop(0))}\n\n"
                if not _tool_task.done() and _elapsed_wait >= _KEEPALIVE_INTERVAL:
                    yield ": keepalive\n\n"
                    _elapsed_wait = 0.0
            result, _ = _tool_task.result()
            _tool_results[tc.id] = (result, tc)

        # Phase 4: Ergebnisse verarbeiten (in Original-Reihenfolge)
        for tc in msg.tool_calls:
            result, _ = _tool_results[tc.id]
            # #489: Session Memory — Tool-Call zählen, bei Schwelle Facts extrahieren
            try:
                from .session_memory import record_tool_call, mark_extracted, extract_session_facts
                if record_tool_call(boss_id) and boss_cfg.agent_dir:
                    _sm_ctx = orch._sessions.get_context(project_id, max_messages=20)
                    _sm_facts = await extract_session_facts(boss_id, boss_cfg.agent_dir, _sm_ctx)
                    if _sm_facts:
                        mark_extracted(boss_id)
            except Exception as _sm_err:
                logger.debug("Session memory: %s", _sm_err)
            # #414: Bild-Event senden bevor Result formatiert wird
            _img_evt = _extract_tool_image(result, tc.function.name)
            if _img_evt:
                yield f"data: {_img_evt}\n\n"
            result_str = format_tool_result(result)
            cur_messages.append({"role": "tool", "tool_call_id": tc.id, "content": result_str})
            # #612: Tool-Output ans Frontend senden (max 3000 Zeichen für Anzeige)
            _result_preview = result_str[:3000] + ("…" if len(result_str) > 3000 else "")
            yield f"data: {_json.dumps({'type': 'tool_result', 'tool_call_id': tc.id, 'tool_result': _result_preview})}\n\n"

        # Tool-Calls + Results in Session persistieren (OpenAI-Format)
        _codex_tc_list = [
            {"id": tc.id, "type": "function",
             "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
            for tc in msg.tool_calls
        ]
        await orch._sessions.append(
            project_id, MessageRole.ASSISTANT, "",
            agent_id=boss_id, tool_calls=_codex_tc_list,
        )
        for tc in msg.tool_calls:
            _cr = _tool_results[tc.id][0]
            _cr_str = format_tool_result(_cr)
            await orch._sessions.append(
                project_id, MessageRole.TOOL, _cr_str,
                agent_id=boss_id, tool_call_id=tc.id,
                tool_name=tc.function.name,
            )

        next_resp = await orch._openai_codex_call(
            boss_cfg, cur_messages, litellm_tools, codex_token, model_name,
            force_tools=False,
        )
        msg = next_resp.choices[0].message
        # #700: Folge-Call nach Tool-Runde — Helper akkumuliert cache_read/write mit.
        from .orchestrator_llm import _accumulate_codex_usage as _acc_codex
        _acc_codex(_usage, getattr(next_resp, "usage", None))

    else:
        await orch._write_forced_abort_handoff(
            boss_cfg,
            cur_messages,
            reason=f"max_rounds_hit:{boss_cfg.max_tool_rounds}",
            execution_mode=execution_mode,
        )

    text = msg.content or ""
    if text:
        yield f"data: {_json.dumps({'text': text})}\n\n"
    # Signal zurück an den Aufrufer
    yield {"_full_response": text, "_streamed_any": bool(text)}


async def _stream_anthropic_oauth(
    orch, boss_cfg, boss_id, project_id, content,
    messages, litellm_tools, oauth_token, model_name,
    execution_mode, _usage,
    *, request_user: str | None = None,
    static_prompt: str = "", dynamic_prompt: str = "",
):
    """Anthropic SDK Streaming mit OAuth."""
    import anthropic as _anthropic
    from .orchestrator_tools import _tool_call_signature as _tool_call_signature_fn

    # #628-Followup: Normalisierung muss auch im Streaming-Pfad laufen, nicht
    # nur im non-streaming. Sonst greifen Pair-Repair / Dedupe / Whitespace-
    # Kanonisierung gerade dort nicht wo der Hauptchat läuft.
    from .message_normalization import normalize_messages_for_call
    messages = normalize_messages_for_call(messages)

    from .provider_config import ANTHROPIC_OAUTH_HEADERS
    client = _anthropic.AsyncAnthropic(
        api_key="",
        auth_token=oauth_token,
        default_headers=ANTHROPIC_OAUTH_HEADERS,
    )
    # #637-Followup: gemeinsamer Helper für OpenAI→Anthropic-Konvertierung —
    # vorher hatte dieser Pfad eine naive Loop, die `role: tool` und
    # `tool_calls` nicht konvertierte → Anthropic 400 "Unexpected role tool".
    from .message_normalization import to_anthropic_format
    system_msg, filtered = to_anthropic_format(messages)

    model = model_name
    for prefix in ("openai/", "anthropic/", "claude/"):
        if model.startswith(prefix):
            model = model[len(prefix):]
            break
    if not model.startswith("claude-"):
        model = "claude-haiku-4-5-20251001"

    from .provider_config import ANTHROPIC_OAUTH_IDENTITY
    oauth_system = [
        {"type": "text", "text": ANTHROPIC_OAUTH_IDENTITY},
    ]
    # Cache-Optimierung: Static-Block mit cache_control, Dynamic-Block ohne
    if static_prompt:
        oauth_system.append({"type": "text", "text": static_prompt,
                             "cache_control": {"type": "ephemeral"}})
    if dynamic_prompt:
        oauth_system.append({"type": "text", "text": dynamic_prompt})
    elif system_msg and not static_prompt:
        oauth_system.append({"type": "text", "text": system_msg,
                             "cache_control": {"type": "ephemeral"}})

    # #351: Ältere History-Messages cachen (max 3 Breakpoints, Anthropic-Limit)
    _cache_cutoff = max(0, len(filtered) - 4)
    _hcc = 0
    for _idx, _fm in enumerate(filtered):
        if _hcc >= 3:
            break
        if _idx < _cache_cutoff and _fm.get("role") in ("user", "assistant"):
            _ct = _fm.get("content", "")
            if isinstance(_ct, str) and _ct:
                filtered[_idx] = {**_fm, "content": [
                    {"type": "text", "text": _ct, "cache_control": {"type": "ephemeral"}}
                ]}
                _hcc += 1

    # Safety: Anthropic erfordert mindestens eine Message
    if not filtered:
        filtered = [{"role": "user", "content": content or "Hallo"}]
    # Anthropic erfordert dass die erste Message role:user ist
    if filtered[0].get("role") != "user":
        filtered.insert(0, {"role": "user", "content": content or "Hallo"})

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

    full_response = ""
    streamed_any = False

    # Agentic Tool-Loop für OAuth-Streaming
    last_tool_signature: tuple[str, ...] | None = None
    repeated_tool_signature_count = 0
    _fuzzy_history: list[str] = []  # #618
    _oauth_file_read_cache: dict[str, str] = {}
    _oauth_loaded_cats: set[str] = set()

    for _round in range(boss_cfg.max_tool_rounds):
        _round_text = ""
        # #620 Phase 4: Tools pro Runde aktualisieren — ToolSearch kann
        # deferred Tools im vorherigen Turn geladen haben, die ab jetzt
        # zur Verfügung stehen sollen.
        if _round > 0:
            try:
                from .orchestrator_mcp import filter_mcp_schemas_by_loaded
                from .orchestrator import _dedup_tools
                from .tool_registry import loaded_deferred_ids as _loaded_ids, session_key as _skey_fn
                extra = orch._allowed_tool_map(
                    boss_cfg, execution_mode, user_text="", project_id=project_id,
                )
                _new_litellm = orch._reg.as_litellm_tools(list(extra.values())) if extra else []
                _mcp_s = await orch._mcp_schemas_for_agent(boss_cfg)
                if _mcp_s:
                    _loaded = _loaded_ids(_skey_fn(project_id, boss_cfg.id))
                    _new_litellm = _new_litellm + filter_mcp_schemas_by_loaded(_mcp_s, _loaded)
                _new_litellm = _dedup_tools(_new_litellm) if _new_litellm else None
                if _new_litellm:
                    kwargs["tools"] = [
                        {
                            "name":         t["function"]["name"],
                            "description":  t["function"].get("description", ""),
                            "input_schema": t["function"].get("parameters", {"type": "object", "properties": {}}),
                        }
                        for t in _new_litellm
                    ]
                else:
                    kwargs.pop("tools", None)
            except Exception as _rebuild_err:
                logger.warning("Tool-Rebuild fehlgeschlagen, nutze Vorrunden-Set: %s", _rebuild_err)
        async with client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                full_response += text
                _round_text   += text
                streamed_any   = True
                yield f"data: {_json.dumps({'text': text})}\n\n"
            final_msg = await stream.get_final_message()
            # Rate-Limit Headers aus dem Stream-Response parsen
            try:
                _http_resp = getattr(stream, "_raw_response", None) or getattr(stream, "response", None)
                if _http_resp and hasattr(_http_resp, "headers"):
                    from .orchestrator_llm import _extract_rate_limit_headers
                    _extract_rate_limit_headers(_http_resp.headers)
            except Exception:
                pass
        _usage["rounds"] += 1
        if hasattr(final_msg, "usage"):
            _usage["input"]       += getattr(final_msg.usage, "input_tokens", 0)
            _usage["output"]      += getattr(final_msg.usage, "output_tokens", 0)
            _usage["cache_write"] += getattr(final_msg.usage, "cache_creation_input_tokens", 0)
            _usage["cache_read"]  += getattr(final_msg.usage, "cache_read_input_tokens", 0)

        tool_use_blocks = [b for b in final_msg.content if b.type == "tool_use"]
        if not tool_use_blocks:
            break
        # Zwischentext persistent speichern (vor Tool-Ausführung)
        if _round_text.strip():
            await orch._sessions.append(project_id, MessageRole.ASSISTANT, _round_text.strip(), agent_id=boss_id)
        _LOOP_EXCLUDE_OAUTH = {"file_write"}
        signature = tuple(
            f"{block.name}:{_json.dumps(block.input, ensure_ascii=False, sort_keys=True)}"
            for block in tool_use_blocks
            if block.name not in _LOOP_EXCLUDE_OAUTH
        )
        # #618: Fuzzy-Loop-History füttern (tool_name + args-prefix)
        from .orchestrator_tools import _fuzzy_fingerprint, check_fuzzy_loop
        for block in tool_use_blocks:
            if block.name in _LOOP_EXCLUDE_OAUTH:
                continue
            _args_json = _json.dumps(block.input, ensure_ascii=False, sort_keys=True)
            _fuzzy_history.append(_fuzzy_fingerprint(block.name, _args_json))
        last_tool_signature, repeated_tool_signature_count, should_abort = check_repeated_signature(
            signature, last_tool_signature, repeated_tool_signature_count, threshold=4,
        )
        abort_reason = "signature_abort"
        # #618: Fuzzy-Detector zusätzlich — fängt variierende Pfade / URLs
        if not should_abort:
            _fuzzy_abort, _fuzzy_fp = check_fuzzy_loop(_fuzzy_history)
            if _fuzzy_abort:
                logger.warning(
                    "Fuzzy-Loop erkannt (OAuth): Pattern '%s' dominiert — Abbruch",
                    (_fuzzy_fp or "")[:120],
                )
                should_abort = True
                abort_reason = "fuzzy_loop_abort"
        if should_abort:
            await orch._write_forced_abort_handoff(
                boss_cfg,
                filtered,
                reason=abort_reason,
                execution_mode=execution_mode,
            )
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
            filtered.append({"role": "user", "content": [{"type": "text", "text": "[System: Letzte Tool-Runde — fasse ab was abgeschlossen wurde, was nicht geklappt hat und warum.]"}]})
            kwargs["messages"] = filtered

        tool_results = []
        any_tool_error = False

        # Phase 1: SSE-Events senden + Tools klassifizieren (#418)
        _oauth_parallel = []
        _oauth_sequential = []
        _oauth_block_results: dict[str, Any] = {}  # block.id → (result, is_error)
        for block in tool_use_blocks:
            _tc_input = block.input or {}
            _tc_detail = format_tool_detail(block.name, _tc_input)
            yield f"data: {_json.dumps({'tool_call': block.name, 'tool_input': _tc_input, 'tool_detail': _tc_detail})}\n\n"
            tool_obj = orch._resolve_allowed_tool(boss_cfg, block.name, execution_mode)
            _is_parallel = getattr(tool_obj, "parallel_safe", None) if tool_obj else None
            if _is_parallel is None and tool_obj:
                from .context_lifecycle import get_tool_policy
                _is_parallel = get_tool_policy(tool_obj.id if hasattr(tool_obj, "id") else "").parallel_safe
            if _is_parallel:
                _oauth_parallel.append(block)
            else:
                _oauth_sequential.append(block)

        # #641: shared SSE-Bridge — execute_tool_call hängt confirm-Events hier an,
        # die Polling-Loops yielden sie ans Frontend.
        _oauth_pending_sse: list[dict] = []
        _oauth_confirm_signal = lambda ev: _oauth_pending_sse.append(ev)

        # Phase 2: Parallel-safe Tools gleichzeitig (#418)
        if _oauth_parallel:
            _par_tasks = [
                _asyncio.create_task(execute_tool_call(
                    orch, boss_cfg=boss_cfg, project_id=project_id,
                    tool_name=b.name, tool_input=b.input or {},
                    execution_mode=execution_mode, user_text=content,
                    request_user=request_user,
                    file_read_cache=_oauth_file_read_cache,
                    tool_call_id=b.id,
                    confirm_signal=_oauth_confirm_signal,
                ))
                for b in _oauth_parallel
            ]
            _par_wait = 0.0
            while not all(t.done() for t in _par_tasks):
                await _asyncio.sleep(0.2)
                _par_wait += 0.2
                while _oauth_pending_sse:
                    yield f"data: {_json.dumps(_oauth_pending_sse.pop(0))}\n\n"
                if not all(t.done() for t in _par_tasks) and _par_wait >= _KEEPALIVE_INTERVAL:
                    yield ": keepalive\n\n"
                    _par_wait = 0.0
            for block, task in zip(_oauth_parallel, _par_tasks):
                result, is_error = task.result()
                if is_error:
                    any_tool_error = True
                _oauth_block_results[block.id] = (result, is_error)
            if len(_oauth_parallel) > 1:
                logger.info("Streaming parallel (OAuth): %d Tools (%s)",
                            len(_oauth_parallel), ", ".join(b.name for b in _oauth_parallel))

        # Phase 3: Sequentielle Tools nacheinander
        for block in _oauth_sequential:
            _tc_input = block.input or {}
            _tool_task_oauth = _asyncio.create_task(execute_tool_call(
                orch, boss_cfg=boss_cfg, project_id=project_id,
                tool_name=block.name, tool_input=_tc_input,
                execution_mode=execution_mode, user_text=content,
                request_user=request_user,
                file_read_cache=_oauth_file_read_cache,
                tool_call_id=block.id,
                confirm_signal=_oauth_confirm_signal,
            ))
            _oauth_wait = 0.0
            while not _tool_task_oauth.done():
                await _asyncio.sleep(0.2)
                _oauth_wait += 0.2
                while _oauth_pending_sse:
                    yield f"data: {_json.dumps(_oauth_pending_sse.pop(0))}\n\n"
                if not _tool_task_oauth.done() and _oauth_wait >= _KEEPALIVE_INTERVAL:
                    yield ": keepalive\n\n"
                    _oauth_wait = 0.0
            result, is_error = _tool_task_oauth.result()
            if is_error:
                any_tool_error = True
            _oauth_block_results[block.id] = (result, is_error)

        # Phase 4: Ergebnisse in Original-Reihenfolge sammeln
        for block in tool_use_blocks:
            result, _is_err = _oauth_block_results[block.id]
            _img_evt = _extract_tool_image(result, block.name)
            if _img_evt:
                yield f"data: {_img_evt}\n\n"
            result_str = format_tool_result(result)
            tool_results.append({
                "type":        "tool_result",
                "tool_use_id": block.id,
                "content":     result_str,
            })
        if any_tool_error:
            repeated_tool_signature_count = 0

        # Tool-Calls + Results in Session persistieren (OpenAI-Format)
        _oauth_tc_list = [
            {"id": block.id, "type": "function",
             "function": {"name": block.name, "arguments": _json.dumps(block.input) if block.input else "{}"}}
            for block in tool_use_blocks
        ]
        await orch._sessions.append(
            project_id, MessageRole.ASSISTANT, "",
            agent_id=boss_id, tool_calls=_oauth_tc_list,
        )
        for block in tool_use_blocks:
            _result, _ = _oauth_block_results[block.id]
            _result_str = format_tool_result(_result)
            await orch._sessions.append(
                project_id, MessageRole.TOOL, _result_str,
                agent_id=boss_id, tool_call_id=block.id,
                tool_name=block.name,
            )

        asst_content = []
        for b in final_msg.content:
            if b.type == "thinking":
                # #473: Include thinking blocks — will be redacted in older messages
                asst_content.append({"type": "thinking", "thinking": getattr(b, "thinking", "[redacted]")})
            elif b.type == "text":
                asst_content.append({"type": "text", "text": b.text})
            elif b.type == "tool_use":
                asst_content.append({"type": "tool_use", "id": b.id, "name": b.name, "input": b.input})
        filtered.append({"role": "assistant", "content": asst_content})
        filtered.append({"role": "user",      "content": tool_results})
        # #473: Redact thinking blocks from older assistant messages before next round
        from .session_manager import redact_thinking_blocks
        kwargs["messages"] = redact_thinking_blocks(filtered)
    else:
        # Loop normal beendet (kein break nach letzter Runde)
        await orch._write_forced_abort_handoff(
            boss_cfg,
            filtered,
            reason=f"max_rounds_hit:{boss_cfg.max_tool_rounds}",
            execution_mode=execution_mode,
        )
        kwargs_final = dict(kwargs)
        kwargs_final.pop("tools", None)
        kwargs_final["messages"] = filtered + [{"role": "user", "content": [{"type": "text", "text": "[System: Tool-Limit erreicht. Fasse ab was abgeschlossen wurde, was nicht geklappt hat und warum. WICHTIG: Du hast KEINE Tools mehr — schreibe KEINE Tool-Aufrufe als Text.]"}]}]
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

    yield {"_full_response": full_response, "_streamed_any": streamed_any}


async def _stream_litellm(
    orch, boss_cfg, boss_id, project_id, content,
    messages, litellm_tools, model_name,
    execution_mode, _usage,
    *, request_user: str | None = None,
):
    """litellm Streaming (Ollama / OpenAI / Anthropic API-Key / MiniMax) mit Tool-Loop."""
    import litellm
    import os as _os
    from .orchestrator_llm import _resolve_model, _provider_call_kwargs, _is_direct_minimax_model

    model, api_base = _resolve_model(model_name, boss_cfg.llm.ollama_base_url)
    # #616: Streaming-Pfad muss für MiniMax denselben api_base+api_key
    # liefern wie Non-Streaming. Ohne das würde litellm OPENAI_API_KEY ziehen
    # und zum falschen Endpoint schicken.
    _prov_kw_stream = _provider_call_kwargs(model_name, boss_cfg)
    # api_key_env hat Vorrang (analog Non-Streaming): erst Projekt-Key prüfen,
    # Helper-Key nur nutzen, wenn noch nichts gesetzt ist.
    _stream_api_key: str | None = None
    _akv_stream = getattr(getattr(boss_cfg, "llm", None), "api_key_env", "") or ""
    if _akv_stream:
        _resolved_stream = _os.environ.get(_akv_stream, "")
        if _resolved_stream:
            _stream_api_key = _resolved_stream
    if _stream_api_key is None and "api_key" in _prov_kw_stream:
        _stream_api_key = _prov_kw_stream["api_key"]
    _is_anthropic = model.startswith(("anthropic/", "claude-"))
    _use_anthropic_wire_format = _is_anthropic and not _is_direct_minimax_model(model_name, model)
    # P3: cache_control auch fuer MiniMax setzen (LiteLLM Messages-Handler
    # reicht die Marker durch). to_anthropic_format() bleibt bei False,
    # damit tool_result-Bloecke nicht in OpenAI-Chat-Messages landen.
    # Analog zu _llm_call_single in orchestrator_llm.py.
    _use_cache_control = _use_anthropic_wire_format or _is_direct_minimax_model(model_name, model)
    # #628-Followup: Streaming-litellm-Pfad muss auch normalisieren —
    # vorher übersprungen, daher kein konsistentes Pair-Repair/Dedupe.
    from .message_normalization import normalize_messages_for_call
    loop_messages = _apply_cache_control(
        normalize_messages_for_call(list(messages)),
        _use_cache_control,
    )
    # BL-16: Anthropic-Image-Bloecke in User-Messages zu OpenAI image_url
    # konvertieren (nur MiniMax-Pfad, analog zu _llm_call_single).
    if _is_direct_minimax_model(model_name, model):
        from .message_normalization import convert_anthropic_images_to_openai
        loop_messages = convert_anthropic_images_to_openai(loop_messages)
    last_tool_signature: tuple[str, ...] | None = None
    repeated_tool_signature_count = 0
    _tools_disabled = False
    full_response = ""
    streamed_any = False

    for _round in range(boss_cfg.max_tool_rounds):
        # #637-Followup: echte Anthropic-litellm-Calls vor dem Send in
        # Anthropic-Format konvertieren. MiniMax bleibt trotz /anthropic-
        # Endpoint im OpenAI-Chat-Message-Format, weil LiteLLM dort validiert.
        if _use_anthropic_wire_format:
            from .message_normalization import to_anthropic_format
            _sys_split, _send_messages = to_anthropic_format(loop_messages)
        else:
            _sys_split, _send_messages = "", loop_messages

        kwargs = {
            "model":       model,
            "messages":    _send_messages,
            "temperature": boss_cfg.llm.temperature,
            "max_tokens":  boss_cfg.llm.max_tokens,
            "stream":      True,
            "stream_options": {"include_usage": True},
        }
        if _use_anthropic_wire_format and _sys_split:
            kwargs["system"] = _sys_split
        if api_base:
            kwargs["api_base"] = api_base
        # #616: Provider-spezifischer api_base-Override (idempotent zu _resolve_model)
        if "api_base" in _prov_kw_stream:
            kwargs["api_base"] = _prov_kw_stream["api_base"]
        if _stream_api_key:
            kwargs["api_key"] = _stream_api_key
        if litellm_tools and not _tools_disabled:
            kwargs["tools"] = litellm_tools

        round_text = ""
        accumulated_tcs: dict = {}

        try:
            _stream = await litellm.acompletion(**kwargs, drop_params=True)
        except Exception as _e:
            if "does not support tools" in str(_e) and "tools" in kwargs:
                _tools_disabled = True
                kwargs.pop("tools")
                _stream = await litellm.acompletion(**kwargs, drop_params=True)
            else:
                raise

        _budget_abort = False
        async for chunk in _stream:
            if getattr(chunk, "usage", None):
                _input   = getattr(chunk.usage, "prompt_tokens", 0) or 0
                _output  = getattr(chunk.usage, "completion_tokens", 0) or 0
                _c_write = getattr(chunk.usage, "cache_creation_input_tokens", 0) or 0
                _c_read  = getattr(chunk.usage, "cache_read_input_tokens", 0) or 0
                _usage["input"]       += _input
                _usage["output"]      += _output
                _usage["cache_write"] += _c_write
                _usage["cache_read"]  += _c_read
                if _c_write or _c_read:
                    logger.info(
                        "cache [%s] input=%d cache_write=%d cache_read=%d (≈%.0f%% gecacht)",
                        model, _input, _c_write, _c_read,
                        100 * _c_read / max(_input, 1),
                    )
                # #778: Mid-Stream-Abort wenn Usage + History ueber Hard-Limit.
                # Exception FANGEN und als SSE-Event senden (Exception-Bubble → 500).
                from . import tool_registry as _tr_check
                _rl_mid = getattr(_tr_check, "_rate_limiter", None)
                if _rl_mid is not None:
                    try:
                        _rl_mid.check_token_budget(
                            boss_cfg.id,
                            estimated_next_call_tokens=_usage["input"] + _usage["output"],
                        )
                    except TypeError:
                        pass  # MagicMock in tests
                    except Exception as _mid_exc:
                        logger.warning(
                            "Mid-stream token-budget abort [%s]: %s",
                            boss_cfg.id, _mid_exc,
                        )
                        _budget_abort = True
                        try:
                            if hasattr(_stream, "aclose"):
                                await _stream.aclose()
                        except Exception:
                            pass
                        break
            choice = chunk.choices[0]
            delta  = choice.delta
            if delta.content:
                round_text     += delta.content
                full_response  += delta.content
                streamed_any    = True
                yield f"data: {_json.dumps({'text': delta.content})}\n\n"
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

        # #778: Wenn Mid-Stream-Abort ausgeloest wurde, sauber ans Frontend
        # melden und die ganze tool-Round-Schleife beenden. Kein Tool-Call
        # mehr starten, auch wenn im Stream welche ankamen.
        if _budget_abort:
            _abort_msg = (
                f"\n\n⛔ Token-Budget mid-stream ueberschritten (#778). "
                f"Stream abgebrochen. Agent pausiert bis sich das 1-Stunden-Fenster "
                f"leert."
            )
            full_response += _abort_msg
            yield f"data: {_json.dumps({'text': _abort_msg})}\n\n"
            yield f"data: {_json.dumps({'done': True, 'reason': 'token_budget_mid_stream'})}\n\n"
            return

        if not accumulated_tcs:
            break

        tc_list = [accumulated_tcs[i] for i in sorted(accumulated_tcs)]
        _LOOP_EXCLUDE_LITELLM = {"file_write"}
        signature = tuple(
            f"{tc['name']}:{tc['arguments']}"
            for tc in tc_list
            if tc['name'] not in _LOOP_EXCLUDE_LITELLM
        )
        last_tool_signature, repeated_tool_signature_count, should_abort = check_repeated_signature(
            signature, last_tool_signature, repeated_tool_signature_count, threshold=4,
        )
        if should_abort:
            await orch._write_forced_abort_handoff(
                boss_cfg,
                loop_messages,
                reason="signature_abort",
                execution_mode=execution_mode,
            )
            loop_messages.append({
                "role": "user",
                "content": "[System: Wiederholte Tool-Signatur erkannt — kein weiterer Fortschritt möglich. Berichte: 1) Was wurde abgeschlossen? 2) Was ist gescheitert und warum? WICHTIG: Du hast KEINE Tools mehr — schreibe KEINE Tool-Aufrufe als Text, antworte NUR mit normalem Text.]",
            })
            final_resp = await orch._llm_call_single(model_name, boss_cfg, loop_messages, tools=None)
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
                _json.loads(raw)
                return raw
            except _json.JSONDecodeError:
                try:
                    parts = [p for p in raw.split("}{") if p]
                    if len(parts) > 1:
                        merged = {}
                        for p in parts:
                            s = p if p.startswith("{") else "{" + p
                            s = s if s.endswith("}") else s + "}"
                            merged.update(_json.loads(s))
                        return _json.dumps(merged)
                except Exception as merge_err:
                    logger.debug("Failed to merge split JSON objects: %s", merge_err)
                return "{}"

        if round_text:
            loop_messages.append({"role": "assistant", "content": round_text})
            await orch._sessions.append(project_id, MessageRole.ASSISTANT, round_text, agent_id=boss_id)

        # Phase 1: SSE-Events senden + Tools klassifizieren (#418)
        _lm_parsed: dict[str, dict] = {}
        _lm_parallel = []
        _lm_sequential = []
        for tc in tc_list:
            tool_input = _json.loads(_safe_args(tc["arguments"]))
            _lm_parsed[tc["id"]] = tool_input
            _tc_detail = format_tool_detail(tc["name"], tool_input)
            yield f"data: {_json.dumps({'tool_call': tc['name'], 'tool_input': tool_input, 'tool_detail': _tc_detail})}\n\n"
            # Tool-Call Info nur als SSE-Event (wird am Ende der Runde strukturiert gespeichert)
            tool_obj = orch._resolve_allowed_tool(boss_cfg, tc["name"], execution_mode)
            # #528: Tool Policy als Fallback für parallel_safe
            _is_parallel = getattr(tool_obj, "parallel_safe", None) if tool_obj else None
            if _is_parallel is None and tool_obj:
                from .context_lifecycle import get_tool_policy
                _is_parallel = get_tool_policy(tool_obj.id if hasattr(tool_obj, "id") else "").parallel_safe
            if _is_parallel:
                _lm_parallel.append(tc)
            else:
                _lm_sequential.append(tc)

        # Phase 2: Parallel-safe Tools gleichzeitig (#418)
        _lm_results: dict[str, Any] = {}  # tc["id"] → result
        # #641: SSE-Bridge für tool_confirm_required-Events
        _lm_pending_sse: list[dict] = []
        _lm_confirm_signal = lambda ev: _lm_pending_sse.append(ev)
        if _lm_parallel:
            _par_tasks = [
                _asyncio.create_task(execute_tool_call(
                    orch, boss_cfg=boss_cfg, project_id=project_id,
                    tool_name=tc["name"], tool_input=_lm_parsed[tc["id"]],
                    execution_mode=execution_mode,
                    request_user=request_user,
                    tool_call_id=tc["id"],
                    confirm_signal=_lm_confirm_signal,
                ))
                for tc in _lm_parallel
            ]
            _par_wait = 0.0
            while not all(t.done() for t in _par_tasks):
                await _asyncio.sleep(0.2)
                _par_wait += 0.2
                while _lm_pending_sse:
                    yield f"data: {_json.dumps(_lm_pending_sse.pop(0))}\n\n"
                if not all(t.done() for t in _par_tasks) and _par_wait >= _KEEPALIVE_INTERVAL:
                    yield ": keepalive\n\n"
                    _par_wait = 0.0
            for tc, task in zip(_lm_parallel, _par_tasks):
                result, _ = task.result()
                _lm_results[tc["id"]] = result
            if len(_lm_parallel) > 1:
                logger.info("Streaming parallel (litellm): %d Tools (%s)",
                            len(_lm_parallel), ", ".join(tc["name"] for tc in _lm_parallel))

        # Phase 3: Sequentielle Tools nacheinander
        for tc in _lm_sequential:
            _tool_task_lm = _asyncio.create_task(execute_tool_call(
                orch, boss_cfg=boss_cfg, project_id=project_id,
                tool_name=tc["name"], tool_input=_lm_parsed[tc["id"]],
                execution_mode=execution_mode,
                request_user=request_user,
                tool_call_id=tc["id"],
                confirm_signal=_lm_confirm_signal,
            ))
            _lm_wait = 0.0
            while not _tool_task_lm.done():
                await _asyncio.sleep(0.2)
                _lm_wait += 0.2
                while _lm_pending_sse:
                    yield f"data: {_json.dumps(_lm_pending_sse.pop(0))}\n\n"
                if not _tool_task_lm.done() and _lm_wait >= _KEEPALIVE_INTERVAL:
                    yield ": keepalive\n\n"
                    _lm_wait = 0.0
            result, _ = _tool_task_lm.result()
            _lm_results[tc["id"]] = result

        # Phase 4: Ergebnisse in Original-Reihenfolge sammeln
        _tc_results_for_session: list[tuple[str, str, str]] = []  # (tc_id, tc_name, result_str)
        for tc in tc_list:
            result = _lm_results[tc["id"]]
            _img_evt = _extract_tool_image(result, tc["name"])
            if _img_evt:
                yield f"data: {_img_evt}\n\n"
            result_str = format_tool_result(result)
            _tc_results_for_session.append((tc["id"], tc["name"], result_str))

        # #637: Tool-Calls + Results strukturiert an loop_messages anhängen
        # statt als User-Freitext. assistant+tool_calls vor den role:tool-
        # Ergebnissen, damit das Pair konsistent bleibt.
        _tc_calls_for_session = [
            {"id": tc["id"], "type": "function",
             "function": {"name": tc["name"], "arguments": tc.get("arguments", "{}")}}
            for tc in tc_list
        ]
        loop_messages.append({
            "role": "assistant",
            "content": "",
            "tool_calls": _tc_calls_for_session,
        })
        for _tcid, _tcname, _tcresult in _tc_results_for_session:
            loop_messages.append({
                "role": "tool",
                "tool_call_id": _tcid,
                "content": _tcresult,
            })

        # Persistieren in Session (OpenAI-Format, identisch zu OAuth-Pfad)
        await orch._sessions.append(
            project_id, MessageRole.ASSISTANT, "",
            agent_id=boss_id, tool_calls=_tc_calls_for_session,
        )
        for _tcid, _tcname, _tcresult in _tc_results_for_session:
            await orch._sessions.append(
                project_id, MessageRole.TOOL, _tcresult,
                agent_id=boss_id, tool_call_id=_tcid,
                tool_name=_tcname,
            )

    else:
        await orch._write_forced_abort_handoff(
            boss_cfg,
            loop_messages,
            reason=f"max_rounds_hit:{boss_cfg.max_tool_rounds}",
            execution_mode=execution_mode,
        )

    yield {"_full_response": full_response, "_streamed_any": streamed_any}
