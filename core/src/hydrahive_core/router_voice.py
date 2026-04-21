"""
router_voice.py — Voice Interface API (#131)

POST /voice              — Text → Agent → Text (minimal, für XiaoZhi/Custom-Clients)
POST /voice/stt          — Audio (WAV) → Text (via faster-whisper, Wyoming-Protokoll)
POST /voice/tts          — Text → Audio WAV (via Piper, Wyoming-Protokoll)
POST /voice/pipeline     — Audio → Agent → Audio (Full Voice Pipeline)
GET  /voice/status       — STT/TTS Service-Status
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile, File
from fastapi.responses import Response
from pydantic import BaseModel

from .voice_providers import registry as voice_registry


class VoiceTextRequest(BaseModel):
    text: str
    agent_id: str | None = None


class VoiceTtsRequest(BaseModel):
    text: str

from .settings import settings

logger = logging.getLogger(__name__)

VOICE_CONFIG_FILE = settings.voice_config

STT_HOST = "127.0.0.1"
STT_PORT = 10300
TTS_HOST = "127.0.0.1"
TTS_PORT = 10200


def _load_voice_config() -> dict:
    cfg = {"stt_host": STT_HOST, "stt_port": STT_PORT,
           "tts_host": TTS_HOST, "tts_port": TTS_PORT,
           "default_agent": "personal_admin"}
    if VOICE_CONFIG_FILE.exists():
        try:
            raw = json.loads(VOICE_CONFIG_FILE.read_text(encoding="utf-8"))
            cfg.update(raw)
        except Exception:
            pass
    # #318: Installer schreibt stt_url/tts_url — daraus host/port ableiten
    for prefix in ("stt", "tts"):
        url_key = f"{prefix}_url"
        if url_key in cfg and isinstance(cfg[url_key], str):
            try:
                from urllib.parse import urlparse
                parsed = urlparse(cfg[url_key])
                if parsed.hostname:
                    cfg[f"{prefix}_host"] = parsed.hostname
                if parsed.port:
                    cfg[f"{prefix}_port"] = parsed.port
            except Exception:
                pass
    return cfg


# Wyoming-Logik lebt ab Commit A von #794 in voice_providers/wyoming_*.py.
# Der Router ruft nur noch die Provider-Registry auf.


# ── Routes ───────────────────────────────────────────────────────────

def register_voice_routes(
    auth_router: APIRouter,
    *,
    require_auth,
    discovery,
    orchestrator,
    group_service=None,
) -> None:

    async def _agent_respond(text: str, agent_id: str, auth: tuple[str, str]) -> str:
        """Send text to agent and return response. Prüft Agent-Zugriff."""
        username, role = auth
        # #3: Access-Check wie in Agent-Chat-Routen
        if role != "admin" and group_service and not group_service.has_agent_access(username, agent_id):
            raise HTTPException(403, f"Keine Berechtigung für Agent '{agent_id}'")
        agent_cfg = discovery.get(agent_id)
        if not agent_cfg:
            raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")

        from .project_config import ProjectConfig, ProjectIdentity, ProjectAgents
        virtual_cfg = ProjectConfig(
            id=agent_id,
            identity=ProjectIdentity(name=agent_cfg.identity),
            agents=ProjectAgents(boss=agent_id, workers=[]),
        )
        response, _ = await orchestrator.handle_message(
            project_id=f"voice_{agent_id}",
            project_cfg=virtual_cfg,
            content=text,
            sender=auth[0],
            execution_mode="sync",
        )
        return response

    # ── Text → Agent → Text ─────────────────────────────────────────
    @auth_router.post("/voice")
    async def voice_text(
        req: VoiceTextRequest,
        auth: tuple[str, str] = Depends(require_auth),
    ):
        text = req.text.strip()
        if not text:
            raise HTTPException(400, "text erforderlich")
        cfg = _load_voice_config()
        agent_id = req.agent_id or cfg.get("default_agent", "personal_admin")
        response = await _agent_respond(text, agent_id, auth)
        return {"text": response, "agent_id": agent_id}

    # ── Audio → Text (STT) ──────────────────────────────────────────
    @auth_router.post("/voice/stt")
    async def voice_stt(
        audio: UploadFile = File(...),
        _auth: tuple[str, str] = Depends(require_auth),
    ):
        audio_bytes = await audio.read()
        if not audio_bytes:
            raise HTTPException(400, "Leere Audio-Datei")
        try:
            stt = voice_registry.get_stt("wyoming-stt")
            result = await stt.recognize(audio_bytes, language="de")
            return {"text": result.text}
        except (ConnectionRefusedError, OSError):
            raise HTTPException(503, "STT-Service nicht erreichbar — ist die Voice-Extension installiert?")

    # ── Text → Audio (TTS) ──────────────────────────────────────────
    @auth_router.post("/voice/tts")
    async def voice_tts(
        req: VoiceTtsRequest,
        _auth: tuple[str, str] = Depends(require_auth),
    ):
        text = req.text.strip()
        if not text:
            raise HTTPException(400, "text erforderlich")
        try:
            tts = voice_registry.get_tts("wyoming-tts")
            result = await tts.synthesize(text)
            return Response(content=result.audio, media_type=result.format.mime,
                            headers={"Content-Disposition": "inline; filename=voice.wav"})
        except (ConnectionRefusedError, OSError):
            raise HTTPException(503, "TTS-Service nicht erreichbar — ist die Voice-Extension installiert?")

    # ── Full Pipeline: Audio → Agent → Audio ─────────────────────────
    @auth_router.post("/voice/pipeline")
    async def voice_pipeline(
        audio: UploadFile = File(...),
        agent_id: str = "",
        auth: tuple[str, str] = Depends(require_auth),
    ):
        cfg = _load_voice_config()
        agent_id = agent_id or cfg.get("default_agent", "personal_admin")

        audio_bytes = await audio.read()
        if not audio_bytes:
            raise HTTPException(400, "Leere Audio-Datei")

        # 1. STT
        try:
            stt = voice_registry.get_stt("wyoming-stt")
            stt_result = await stt.recognize(audio_bytes, language="de")
            user_text = stt_result.text
        except (ConnectionRefusedError, OSError):
            raise HTTPException(503, "STT-Service nicht erreichbar")
        if not user_text:
            raise HTTPException(400, "Kein Text erkannt")

        # 2. Agent
        response = await _agent_respond(user_text, agent_id, auth)

        # 3. TTS
        try:
            tts = voice_registry.get_tts("wyoming-tts")
            tts_result = await tts.synthesize(response)
            wav = tts_result.audio
        except (ConnectionRefusedError, OSError):
            raise HTTPException(503, "TTS-Service nicht erreichbar")

        return Response(
            content=wav,
            media_type="audio/wav",
            headers={
                "Content-Disposition": "inline; filename=voice.wav",
                "X-Voice-Input": user_text,
                "X-Voice-Agent": agent_id,
            },
        )

    # ── Status ───────────────────────────────────────────────────────
    @auth_router.get("/voice/status")
    async def voice_status(_auth: tuple[str, str] = Depends(require_auth)):
        cfg = _load_voice_config()
        stt_host = cfg.get("stt_host", STT_HOST)
        stt_port = cfg.get("stt_port", STT_PORT)
        tts_host = cfg.get("tts_host", TTS_HOST)
        tts_port = cfg.get("tts_port", TTS_PORT)

        result = {
            "installed": settings.voice_install_dir.exists(),
            "stt": {"host": stt_host, "port": stt_port, "available": False},
            "tts": {"host": tts_host, "port": tts_port, "available": False},
            "default_agent": cfg.get("default_agent", "personal_admin"),
        }

        for key, host, port in [("stt", stt_host, stt_port), ("tts", tts_host, tts_port)]:
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(host, port), timeout=3
                )
                writer.close()
                await writer.wait_closed()
                result[key]["available"] = True
            except Exception:
                pass

        return result
