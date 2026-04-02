"""
OpenAI-kompatibler Endpunkt für HydraHive.
POST /v1/chat/completions → leitet an einen HydraHive-Agent weiter.

Authentifizierung: Bearer-Token (HYDRAHIVE_OPENAI_API_KEY env-Variable,
Standard: "hydrahive"). Damit kann der XiaoZhi-Server ohne HydraHive-Account
sprechen.

Das `model`-Feld der Anfrage wird als Agent-ID verwendet.
"""

from __future__ import annotations

import json
import os
import time
import uuid

from fastapi import Header, HTTPException, Request
from fastapi.responses import StreamingResponse

_OPENAI_API_KEY = os.environ.get("HYDRAHIVE_OPENAI_API_KEY", "hydrahive")


def _check_bearer(authorization: str | None) -> None:
    if not authorization:
        raise HTTPException(401, "Authorization header fehlt")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or token != _OPENAI_API_KEY:
        raise HTTPException(401, "Ungültiger API-Key")


def register_openai_compat_routes(app, *, discovery, orchestrator):
    """Registriert /v1/chat/completions direkt an der FastAPI-App (kein Auth-Router)."""

    @app.post("/v1/chat/completions")
    async def openai_chat_completions(
        request: Request,
        authorization: str | None = Header(default=None),
    ):
        _check_bearer(authorization)

        body = await request.json()
        model = body.get("model", "")
        messages = body.get("messages", [])
        stream = body.get("stream", False)

        # Letzten User-Text extrahieren
        user_text = ""
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, list):
                    content = " ".join(
                        p.get("text", "") for p in content
                        if isinstance(p, dict) and p.get("type") == "text"
                    )
                user_text = content.strip()
                break

        if not user_text:
            raise HTTPException(400, "Keine User-Nachricht gefunden")

        # Agent-Config laden
        cfg = discovery.get(model)
        if not cfg:
            raise HTTPException(404, f"Agent '{model}' nicht gefunden")

        from .project_config import ProjectAgents as _PA
        from .project_config import ProjectConfig as _PC
        from .project_config import ProjectIdentity as _PI

        virtual_cfg = _PC(
            id=model,
            identity=_PI(name=cfg.identity),
            agents=_PA(boss=model, workers=[]),
        )

        cmpl_id = f"chatcmpl-{uuid.uuid4().hex[:12]}"
        created = int(time.time())

        if stream:
            async def sse_stream():
                async for raw_chunk in orchestrator.handle_message_stream(
                    project_id=model,
                    project_cfg=virtual_cfg,
                    content=user_text,
                    sender="voice",
                ):
                    # HydraHive yieldet: data: {"text": "..."}\n\n
                    # oder:              data: {"done": true}\n\n
                    if not raw_chunk.startswith("data: "):
                        continue
                    payload_str = raw_chunk[6:].strip()
                    try:
                        payload = json.loads(payload_str)
                    except Exception:
                        continue

                    if payload.get("done"):
                        stop_chunk = {
                            "id": cmpl_id,
                            "object": "chat.completion.chunk",
                            "created": created,
                            "model": model,
                            "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
                        }
                        yield f"data: {json.dumps(stop_chunk)}\n\n"
                        yield "data: [DONE]\n\n"
                        return

                    text = payload.get("text", "")
                    if not text:
                        continue

                    chunk = {
                        "id": cmpl_id,
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": model,
                        "choices": [{"index": 0, "delta": {"content": text}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(chunk)}\n\n"

            return StreamingResponse(
                sse_stream(),
                media_type="text/event-stream",
                headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
            )

        else:
            # Non-streaming: komplette Antwort sammeln
            full_text = ""
            async for raw_chunk in orchestrator.handle_message_stream(
                project_id=model,
                project_cfg=virtual_cfg,
                content=user_text,
                sender="voice",
            ):
                if not raw_chunk.startswith("data: "):
                    continue
                payload_str = raw_chunk[6:].strip()
                try:
                    payload = json.loads(payload_str)
                except Exception:
                    continue
                if payload.get("done"):
                    break
                full_text += payload.get("text", "")

            return {
                "id": cmpl_id,
                "object": "chat.completion",
                "created": created,
                "model": model,
                "choices": [{
                    "index": 0,
                    "message": {"role": "assistant", "content": full_text},
                    "finish_reason": "stop",
                }],
                "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            }

    @app.get("/v1/models")
    async def openai_list_models(
        authorization: str | None = Header(default=None),
    ):
        _check_bearer(authorization)
        agents = discovery.list_all() if hasattr(discovery, "list_all") else []
        return {
            "object": "list",
            "data": [
                {"id": a, "object": "model", "created": 0, "owned_by": "hydrahive"}
                for a in agents
            ],
        }
