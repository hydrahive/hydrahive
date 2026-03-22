from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, FastAPI, HTTPException


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
    logger,
    incoming_message_model,
) -> None:
    @auth_router.get("/agents/{agent_id}/session/history")
    def agent_session_history(agent_id: str, limit: int = 50, _a: tuple[str, str] = Depends(require_auth)):
        context = agent_sessions.get_context(agent_id, max_messages=limit)
        session = agent_sessions.get_active(agent_id)
        return {
            "session_id": session.id if session else None,
            "messages": context,
            "count": len(context),
        }

    @auth_router.post("/agents/{agent_id}/memory", status_code=201)
    def write_agent_memory(agent_id: str, body: dict, _a: tuple = Depends(require_auth)):
        import re as _re

        filename = str(body.get("filename", "session")).strip().removesuffix(".md")
        content = str(body.get("content", "")).strip()
        mode = str(body.get("mode", "overwrite"))
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
        return {"saved": True, "filename": f"{filename}.md", "bytes": len(content.encode())}

    @auth_router.delete("/agents/{agent_id}/session")
    def agent_session_clear(agent_id: str, _a: tuple = Depends(require_auth)):
        agent_sessions.end_session(agent_id)
        return {"cleared": True}

    @auth_router.post("/agents/{agent_id}/session/compact")
    async def agent_session_compact(agent_id: str, _a: tuple = Depends(require_auth)):
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
                client = _anthropic.AsyncAnthropic(
                    api_key="",
                    auth_token=oauth_token,
                    default_headers={
                        "anthropic-beta": "claude-code-20250219,oauth-2025-04-20",
                        "user-agent": "claude-cli/2.1.62",
                        "x-app": "cli",
                    },
                )
                resp = await client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=1200,
                    system=[{"type": "text", "text": "You are Claude Code, Anthropic's official CLI for Claude."}],
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
        agent_sessions.replace_messages(agent_id, [summary_user, summary_asst])
        logger.info("compact: %s — %d Nachrichten → 2 (Zusammenfassung)", agent_id, msg_count)
        return {"compacted": True, "original_count": msg_count, "summary": summary}

    @app.post("/agents/{agent_id}/message")
    async def agent_message_sync(
        agent_id: str,
        req: incoming_message_model,
        _a: tuple[str, str] = Depends(require_auth_or_localhost),
    ):
        from .project_config import ProjectAgents as _PA
        from .project_config import ProjectConfig as _PC
        from .project_config import ProjectIdentity as _PI

        check_message_rate(req.sender, agent_id)
        cfg = discovery.get(agent_id)
        if not cfg:
            raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")

        virtual_cfg = _PC(
            id=agent_id,
            identity=_PI(name=cfg.identity),
            agents=_PA(boss=agent_id, workers=[]),
        )
        response, _ = await agent_orchestrator.handle_message(
            project_id=agent_id,
            project_cfg=virtual_cfg,
            content=req.content,
            sender=req.sender,
            execution_mode="safe" if _a[0] != "internal" else None,
        )
        return {"response": response, "agent_id": agent_id}

    @app.post("/agents/{agent_id}/message/stream")
    async def agent_message_stream(
        agent_id: str,
        req: incoming_message_model,
        _a: tuple[str, str] = Depends(require_auth_or_localhost),
    ):
        from fastapi.responses import StreamingResponse as _SR
        from .project_config import ProjectAgents as _PA
        from .project_config import ProjectConfig as _PC
        from .project_config import ProjectIdentity as _PI

        check_message_rate(req.sender, agent_id)
        cfg = discovery.get(agent_id)
        if not cfg:
            raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")

        virtual_cfg = _PC(
            id=agent_id,
            identity=_PI(name=cfg.identity),
            agents=_PA(boss=agent_id, workers=[]),
        )

        async def event_stream():
            async for chunk in agent_orchestrator.handle_message_stream(
                project_id=agent_id,
                project_cfg=virtual_cfg,
                content=req.content,
                sender=req.sender,
                execution_mode="safe" if _a[0] != "internal" else None,
            ):
                yield chunk

        return _SR(event_stream(), media_type="text/event-stream",
                   headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

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
                    "-u", "octopos-core",
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
                or "octopos_core" in l.lower()
            ]
            if len(filtered) < 5:
                filtered = all_lines
            return {
                "agent_id": agent_id,
                "lines": filtered[-lines:],
                "count": len(filtered),
                "source": "journalctl -u octopos-core",
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
