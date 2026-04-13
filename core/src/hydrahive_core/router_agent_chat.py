from __future__ import annotations

import datetime
from pathlib import Path

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException
from pydantic import BaseModel

from .execution_mode_policy import resolve_request_execution_mode
from .learning_memory import append_learning_snapshot


class AgentMemoryRequest(BaseModel):
    filename: str = "session"
    content: str
    mode: str = "overwrite"


class SessionImportRequest(BaseModel):
    session_b64: str


def _save_session_transcript(agent_dir: Path, context: list[dict], agent_id: str) -> None:
    """Speichert vollständiges Transkript + kurzen Session-Inject (analog OpenClaw)."""
    if not context:
        return

    now = datetime.datetime.now()
    ts = now.strftime("%Y-%m-%d_%H-%M")

    # Vollständiges Transkript → transcripts/
    transcripts_dir = agent_dir / "transcripts"
    transcripts_dir.mkdir(exist_ok=True)
    lines = [f"# Session-Transkript {ts}\n"]
    for msg in context:
        role = msg.get("role", "")
        content = msg.get("content", "") or ""
        if isinstance(content, list):
            content = " ".join(
                p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
            )
        content = content.strip()
        if not content:
            continue
        if role == "user":
            lines.append(f"**User:** {content}")
        elif role == "assistant":
            lines.append(f"**{agent_id}:** {content}")
    transcript_file = transcripts_dir / f"{ts}.md"
    transcript_file.write_text("\n\n".join(lines), encoding="utf-8")
    transcript_file.chmod(0o600)

    # Kurzfassung → memory/_last_session.md (Session-Inject)
    memory_dir = agent_dir / "memory"
    memory_dir.mkdir(exist_ok=True)
    short = [f"# Letzte Session (vor Clear, {now.strftime('%Y-%m-%d %H:%M')})\n"]
    for msg in context:
        role = msg.get("role", "")
        content = msg.get("content", "") or ""
        if isinstance(content, list):
            content = " ".join(
                p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
            )
        content = content.strip()
        if role == "user" and content:
            short.append(f"**User:** {content[:400]}")
        elif role == "assistant" and content:
            short.append(f"**{agent_id}:** {content[:400]}")
    last_session = memory_dir / "_last_session.md"
    last_session.write_text("\n\n".join(short), encoding="utf-8")
    last_session.chmod(0o600)


def register_agent_chat_routes(
    app: FastAPI,
    auth_router: APIRouter,
    *,
    require_auth,
    require_auth_or_localhost,
    check_message_rate,
    discovery,
    agent_sessions,
    agent_orchestrator,
    agents_dir: str,
    audit_log,
    logger,
    incoming_message_model,
    group_service=None,
) -> None:
    def _check_agent_access(agent_id: str, auth: tuple[str, str]) -> None:
        """Prüft ob der User Zugriff auf den Agent hat (Owner/Gruppe/Admin)."""
        username, role = auth
        if role == "admin":
            return
        if group_service and not group_service.has_agent_access(username, agent_id):
            raise HTTPException(403, f"Keine Berechtigung für Agent '{agent_id}'")

    @auth_router.get("/agents/{agent_id}/session/history")
    def agent_session_history(agent_id: str, limit: int = 50, _a: tuple[str, str] = Depends(require_auth)):
        _check_agent_access(agent_id, _a)
        context = agent_sessions.get_history(agent_id, max_messages=limit)
        session = agent_sessions.get_active(agent_id)
        return {
            "session_id": session.id if session else None,
            "messages": context,
            "count": len(context),
        }

    @auth_router.get("/agents/{agent_id}/sessions")
    def agent_list_sessions(agent_id: str, limit: int = 30, _a: tuple[str, str] = Depends(require_auth)):
        _check_agent_access(agent_id, _a)
        return {"sessions": agent_sessions.list_sessions(agent_id, limit)}

    @auth_router.get("/agents/{agent_id}/sessions/{session_id}")
    def agent_get_session(agent_id: str, session_id: str, _a: tuple[str, str] = Depends(require_auth)):
        _check_agent_access(agent_id, _a)
        session = agent_sessions.get_session_by_id(agent_id, session_id)
        if not session:
            raise HTTPException(404, "Session nicht gefunden")
        def _msg(m):
            return {
                "role": m.role.value if hasattr(m.role, "value") else m.role,
                "content": m.content,
                "timestamp": m.timestamp,
                "agent_id": m.agent_id,
            }
        return {"id": session.id, "messages": [_msg(m) for m in session.messages],
                "started_at": session.started_at, "ended_at": session.ended_at}

    @auth_router.post("/agents/{agent_id}/memory", status_code=201)
    def write_agent_memory(agent_id: str, req: AgentMemoryRequest, _a: tuple = Depends(require_auth)):
        import re as _re

        _check_agent_access(agent_id, _a)
        # #277: agent_id gegen Path Traversal absichern
        if not _re.match(r"^[a-z0-9_-]+$", agent_id):
            raise HTTPException(400, "Ungültige agent_id")
        filename = req.filename.strip().removesuffix(".md")
        content = req.content.strip()
        mode = req.mode
        if not _re.match(r"^[a-z0-9_-]+$", filename):
            raise HTTPException(400, "Ungültiger Dateiname (nur a-z, 0-9, -, _)")
        if not content:
            raise HTTPException(400, "content fehlt")
        agent_dir = Path(agents_dir) / agent_id
        if not agent_dir.exists():
            raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")
        memory_dir = agent_dir / "memory"
        memory_dir.mkdir(exist_ok=True)
        p = memory_dir / f"{filename}.md"
        p.open("a" if mode == "append" else "w", encoding="utf-8").write(content)
        p.chmod(0o600)
        return {"saved": True, "filename": f"{filename}.md", "bytes": len(content.encode())}

    @auth_router.post("/agents/{agent_id}/sessions/{session_id}/resume")
    async def agent_resume_session(agent_id: str, session_id: str, _a: tuple[str, str] = Depends(require_auth)):
        _check_agent_access(agent_id, _a)
        session = await agent_sessions.resume_session(agent_id, session_id)
        if not session:
            raise HTTPException(404, "Session nicht gefunden")
        def _msg(m):
            return {
                "role": m.role.value if hasattr(m.role, "value") else m.role,
                "content": m.content,
                "timestamp": m.timestamp,
                "agent_id": m.agent_id,
            }
        return {
            "resumed": True,
            "id": session.id,
            "messages": [_msg(m) for m in session.messages],
        }

    # #424: Session Export/Import (Teleportation)
    @auth_router.get("/agents/{agent_id}/sessions/{session_id}/export")
    def agent_export_session(agent_id: str, session_id: str, _a: tuple[str, str] = Depends(require_auth)):
        """Session als JSON exportieren — für Transfer zwischen Instanzen."""
        _check_agent_access(agent_id, _a)
        session = agent_sessions.get_session_by_id(agent_id, session_id)
        if not session:
            raise HTTPException(404, "Session nicht gefunden")
        import base64, json
        data = json.dumps(session.to_dict(), ensure_ascii=False)
        return {"session_b64": base64.b64encode(data.encode()).decode(), "agent_id": agent_id, "message_count": len(session.messages)}

    @auth_router.post("/agents/{agent_id}/sessions/import")
    async def agent_import_session(agent_id: str, req: SessionImportRequest, _a: tuple[str, str] = Depends(require_auth)):
        """Session aus base64-Export importieren."""
        _check_agent_access(agent_id, _a)
        import base64, json
        from ..hydrahive_core.session_manager import Session
        try:
            raw = json.loads(base64.b64decode(req.session_b64))
            session = Session.from_dict(raw)
            session.project_id = agent_id  # Re-scope auf diesen Agent
            await agent_sessions.replace_messages(agent_id, session.messages)
            return {"imported": True, "message_count": len(session.messages)}
        except Exception as e:
            raise HTTPException(400, f"Import fehlgeschlagen: {e}")

    # #448: Session Search — Volltext über alle Sessions
    @auth_router.get("/agents/{agent_id}/sessions/search")
    def agent_search_sessions(agent_id: str, q: str = "", _a: tuple[str, str] = Depends(require_auth)):
        """Volltextsuche über alle Sessions eines Agenten."""
        _check_agent_access(agent_id, _a)
        if not q or len(q) < 2:
            raise HTTPException(400, "Suchbegriff mindestens 2 Zeichen")
        results = []
        q_lower = q.lower()
        for session_info in agent_sessions.list_sessions(agent_id, limit=50):
            session = agent_sessions.get_session_by_id(agent_id, session_info["id"])
            if not session:
                continue
            matches = []
            for m in session.messages:
                if q_lower in m.content.lower():
                    matches.append({
                        "role": m.role.value if hasattr(m.role, "value") else m.role,
                        "content": m.content[:200],
                        "timestamp": m.timestamp,
                    })
            if matches:
                results.append({
                    "session_id": session.id,
                    "started_at": session.started_at,
                    "match_count": len(matches),
                    "matches": matches[:5],  # Max 5 Treffer pro Session
                })
        return {"query": q, "results": results, "total_matches": sum(r["match_count"] for r in results)}

    @auth_router.delete("/agents/{agent_id}/session")
    async def agent_session_clear(agent_id: str, _a: tuple = Depends(require_auth)):
        _check_agent_access(agent_id, _a)
        context = agent_sessions.get_context(agent_id, max_messages=200)
        _save_session_transcript(Path(agents_dir) / agent_id, context, agent_id)
        await agent_sessions.end_session(agent_id)
        return {"cleared": True}

    @auth_router.post("/agents/{agent_id}/session/compact")
    async def agent_session_compact(agent_id: str, _a: tuple = Depends(require_auth)):
        _check_agent_access(agent_id, _a)
        from .orchestrator import _load_claude_oauth_token
        from .session_manager import Message, MessageRole

        context = agent_sessions.get_context(agent_id, max_messages=200)
        if not context:
            return {"compacted": False, "reason": "Keine Nachrichten vorhanden"}

        lines = []
        for m in context:
            role = m.get("role", "")
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
            if role in ("user", "assistant") and content:
                lines.append(f"{role.upper()}: {content[:2000]}")
        conversation_text = "\n\n".join(lines)[:60000]
        if not conversation_text:
            return {"compacted": False, "reason": "Kein kompaktierbarer Inhalt"}

        summary_prompt = (
            "Fasse das folgende Gespräch zwischen User und Agent präzise auf Deutsch zusammen. "
            "Behalte alle wichtigen Fakten, Entscheidungen, Zwischenergebnisse und offenen Fragen. "
            "Schreibe die Zusammenfassung so, dass der Agent danach nahtlos weiterarbeiten kann. "
            "Maximal 800 Wörter.\n\n---\n\n" + conversation_text
        )

        oauth_token = _load_claude_oauth_token()
        summary = ""
        if oauth_token:
            try:
                import anthropic as _anthropic
                from .provider_config import ANTHROPIC_OAUTH_HEADERS, ANTHROPIC_OAUTH_IDENTITY
                client = _anthropic.AsyncAnthropic(
                    api_key="",
                    auth_token=oauth_token,
                    default_headers=ANTHROPIC_OAUTH_HEADERS,
                )
                resp = await client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=1200,
                    system=[{"type": "text", "text": ANTHROPIC_OAUTH_IDENTITY}],
                    messages=[{"role": "user", "content": summary_prompt}],
                )
                summary = resp.content[0].text if resp.content else ""
            except Exception as e:
                logger.error("compact: LLM-Fehler: %s", e)
                return {"compacted": False, "reason": f"LLM-Fehler: {e}"}
        else:
            return {"compacted": False, "reason": "Kein OAuth-Token konfiguriert"}

        if not summary:
            return {"compacted": False, "reason": "Leere Zusammenfassung vom LLM"}

        msg_count = len(context)
        summary_user = Message.create(
            MessageRole.USER,
            f"[Zusammenfassung der bisherigen Konversation ({msg_count} Nachrichten)]\n\n{summary}",
        )
        summary_asst = Message.create(
            MessageRole.ASSISTANT,
            "Verstanden. Ich habe die Zusammenfassung der bisherigen Konversation gelesen und kann nahtlos weiterarbeiten.",
        )
        await agent_sessions.replace_messages(agent_id, [summary_user, summary_asst])
        learning_snapshot = None
        try:
            learning_snapshot = append_learning_snapshot(
                Path(agents_dir) / agent_id,
                summary,
                logger=logger,
            )
        except Exception as e:
            logger.warning("compact: Lernnotiz konnte nicht gespeichert werden: %s", e)
        logger.info("compact: %s — %d Nachrichten → 2 (Zusammenfassung)", agent_id, msg_count)
        return {
            "compacted": True,
            "original_count": msg_count,
            "summary": summary,
            "learning_snapshot": str(learning_snapshot) if learning_snapshot else None,
        }

    @app.post("/agents/{agent_id}/message")
    async def agent_message_sync(
        agent_id: str,
        body: dict = Body(...),
        _a: tuple[str, str] = Depends(require_auth_or_localhost),
    ):
        from .project_config import ProjectAgents as _PA
        from .project_config import ProjectConfig as _PC
        from .project_config import ProjectIdentity as _PI

        req = incoming_message_model.model_validate(body)
        execution_mode = resolve_request_execution_mode(
            _a,
            req.execution_mode,
            audit_log=audit_log,
            audit_target=agent_id,
            audit_source="agents.message",
        )
        # #278/#283: sender immer aus Auth ableiten, nie aus Body
        _username, _ = _a
        sender = _username if _username != "internal" else (req.sender or "user")
        check_message_rate(sender, agent_id)
        # Gruppen-Berechtigung prüfen (#165)
        if group_service and not group_service.has_agent_access(_username, agent_id):
            raise HTTPException(403, f"Keine Berechtigung für Agent '{agent_id}'")
        cfg = discovery.get(agent_id)
        if not cfg:
            raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")

        virtual_cfg = _PC(
            id=agent_id,
            identity=_PI(name=cfg.identity),
            agents=_PA(boss=agent_id, workers=[]),
        )
        # project_id immer auf agent_id setzen (kein client-controlled override)
        project_id = agent_id
        response, _ = await agent_orchestrator.handle_message(
            project_id=project_id,
            project_cfg=virtual_cfg,
            content=req.content,
            sender=sender,
            execution_mode=execution_mode,
        )
        return {"response": response, "agent_id": agent_id}

    @app.post("/agents/{agent_id}/message/stream")
    async def agent_message_stream(
        agent_id: str,
        body: dict = Body(...),
        _a: tuple[str, str] = Depends(require_auth_or_localhost),
    ):
        from fastapi.responses import StreamingResponse as _SR
        from .project_config import ProjectAgents as _PA
        from .project_config import ProjectConfig as _PC
        from .project_config import ProjectIdentity as _PI

        req = incoming_message_model.model_validate(body)
        execution_mode = resolve_request_execution_mode(
            _a,
            req.execution_mode,
            audit_log=audit_log,
            audit_target=agent_id,
            audit_source="agents.message.stream",
        )
        # #278/#283: sender immer aus Auth ableiten
        _username, _ = _a
        sender = _username if _username != "internal" else (req.sender or "user")
        check_message_rate(sender, agent_id)
        # Gruppen-Berechtigung prüfen (#165)
        if group_service and not group_service.has_agent_access(_username, agent_id):
            raise HTTPException(403, f"Keine Berechtigung für Agent '{agent_id}'")
        cfg = discovery.get(agent_id)
        if not cfg:
            raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")

        virtual_cfg = _PC(
            id=agent_id,
            identity=_PI(name=cfg.identity),
            agents=_PA(boss=agent_id, workers=[]),
        )

        # #414: Images als Content-Blocks für Vision
        _user_content = req.content
        _images = getattr(req, "images", None) or []
        if _images:
            _content_blocks = []
            for img in _images[:5]:  # max 5 Bilder
                _content_blocks.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": img.get("media_type", "image/png"), "data": img["data"]},
                })
            _content_blocks.append({"type": "text", "text": req.content or "Was siehst du auf diesem Bild?"})
            _user_content = _content_blocks

        async def event_stream():
            async for chunk in agent_orchestrator.handle_message_stream(
                project_id=agent_id,
                project_cfg=virtual_cfg,
                content=_user_content,
                sender=sender,
                execution_mode=execution_mode,
            ):
                yield chunk

        return _SR(event_stream(), media_type="text/event-stream",
                   headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @auth_router.post("/agents/{agent_id}/interrupt")
    async def interrupt_agent_stream(
        agent_id: str,
        _a: tuple[str, str] = Depends(require_auth),
    ):
        """Bricht einen laufenden ask_agent-Request ab (#34)."""
        _check_agent_access(agent_id, _a)  # #307
        from .tool_registry import set_interrupt as _set_interrupt
        _set_interrupt(agent_id)
        return {"ok": True, "agent_id": agent_id}

    @auth_router.get("/agents/{agent_id}/tamagotchi/state")
    async def tamagotchi_state(
        agent_id: str,
        _a: tuple[str, str] = Depends(require_auth),
    ):
        """Aktueller Companion-Zustand (Happy/Hunger/Energy + Mood + Age)."""
        from .tamagotchi_service import tamagotchi_service
        return tamagotchi_service.snapshot_dict(_a[0])

    @auth_router.post("/agents/{agent_id}/tamagotchi/interact")
    async def tamagotchi_interact(
        agent_id: str,
        body: dict = Body(...),
        _a: tuple[str, str] = Depends(require_auth),
    ):
        """Interaktion: pet | feed | sleep | wake. Gibt neuen Zustand zurück."""
        from .tamagotchi_service import tamagotchi_service, derive_mood, status_hint
        kind = (body.get("kind") or "").lower().strip()
        if kind not in {"pet", "feed", "sleep", "wake"}:
            raise HTTPException(400, f"Unbekannte Interaktion '{kind}'")
        try:
            state = tamagotchi_service.interact(_a[0], kind)
        except ValueError as e:
            raise HTTPException(400, str(e))
        from dataclasses import asdict
        d = asdict(state)
        d["mood"] = derive_mood(state)
        d["status_hint"] = status_hint(state)
        d["age_days"] = state.age_days()
        return d

    @auth_router.post("/agents/{agent_id}/tamagotchi")
    async def tamagotchi_comment(
        agent_id: str,
        body: dict = Body(...),
        _a: tuple[str, str] = Depends(require_auth),
    ):
        """Easter-Egg: leichter LLM-Call für den Floating Companion. Berührt keine Session."""
        _check_agent_access(agent_id, _a)  # #307
        from .orchestrator import _load_claude_oauth_token
        from .tamagotchi_service import tamagotchi_service, status_hint

        context = body.get("context", "")
        lang = body.get("lang", "de")
        lang_name = {"de": "German", "en": "English", "fr": "French", "es": "Spanish"}.get(lang, "English")
        # Aktueller Zustand fließt in den Prompt ein, damit die Kommentare zum
        # Mood passen ("du bist gerade hungrig und traurig" → LLM jammert).
        state = tamagotchi_service.get_state(_a[0])
        mood_hint = status_hint(state)
        system_prompt = (
            "You are a tiny, cute companion living in the corner of a screen. "
            "Comment briefly and wittily on what's happening. "
            f"{mood_hint} Let your mood shape the tone of your comment. "
            "Rules: Max 1 sentence, max 15 words. Be cute and a little cheeky. "
            "Use emoticons occasionally. No markdown, no code. "
            f"IMPORTANT: You MUST respond in {lang_name} only."
        )
        try:
            oauth_token = _load_claude_oauth_token()
            if not oauth_token:
                return {"comment": ""}
            import anthropic as _anthropic
            from .provider_config import ANTHROPIC_OAUTH_HEADERS, ANTHROPIC_OAUTH_IDENTITY
            client = _anthropic.AsyncAnthropic(
                api_key="",
                auth_token=oauth_token,
                default_headers=ANTHROPIC_OAUTH_HEADERS,
            )
            resp = await client.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=60,
                system=[
                    {"type": "text", "text": ANTHROPIC_OAUTH_IDENTITY},
                    {"type": "text", "text": system_prompt},
                ],
                messages=[{"role": "user", "content": context}],
            )
            text = (resp.content[0].text if resp.content else "").strip()[:80]
            return {"comment": text}
        except Exception as e:
            logger.warning("Tamagotchi LLM call failed: %s", e)
            return {"comment": ""}

    @auth_router.get("/agents/{agent_id}/logs")
    def get_agent_logs(agent_id: str, lines: int = 100, _a: tuple[str, str] = Depends(require_auth)):
        import subprocess as _sub

        if not discovery.get(agent_id) and not (Path(agents_dir) / agent_id).exists():
            raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")

        lines = max(10, min(lines, 1000))
        try:
            result = _sub.run(
                [
                    "journalctl",
                    "-u", "hydrahive-core",
                    "-n", str(lines),
                    "--no-pager",
                    "--output=short-iso",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            all_lines = result.stdout.splitlines()
            agent_lower = agent_id.lower().replace("-", "_")
            filtered = [
                l for l in all_lines
                if agent_lower in l.lower()
                or agent_id.lower() in l.lower()
                or "hydrahive_core" in l.lower()
            ]
            if len(filtered) < 5:
                filtered = all_lines
            return {
                "agent_id": agent_id,
                "lines": filtered[-lines:],
                "count": len(filtered),
                "source": "journalctl -u hydrahive-core",
            }
        except FileNotFoundError:
            return {
                "agent_id": agent_id,
                "lines": ["[journalctl nicht verfuegbar]"],
                "count": 1,
                "source": "unavailable",
            }
        except _sub.TimeoutExpired:
            raise HTTPException(504, "Timeout beim Lesen der Logs")
