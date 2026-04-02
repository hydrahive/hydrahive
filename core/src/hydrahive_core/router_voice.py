"""
router_voice.py — Voice Interface API (#131)

POST /voice              — Text → Agent → Text (minimal, für XiaoZhi/Custom-Clients)
POST /voice/stt          — Audio (WAV/OPUS) → Text (via faster-whisper)
POST /voice/tts          — Text → Audio WAV (via Piper)
POST /voice/pipeline     — Audio → Agent → Audio (Full Voice Pipeline)
GET  /voice/status       — STT/TTS Service-Status
"""
from __future__ import annotations

import logging
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile, File
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)

VOICE_CONFIG_FILE = Path("/etc/hydrahive/voice.json")

# Default-Ports für STT/TTS Docker-Services
STT_URL = "http://127.0.0.1:10300"
TTS_URL = "http://127.0.0.1:10200"


def _load_voice_config() -> dict:
    import json
    if VOICE_CONFIG_FILE.exists():
        try:
            return json.loads(VOICE_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"stt_url": STT_URL, "tts_url": TTS_URL, "default_agent": "personal_admin"}


def register_voice_routes(
    auth_router: APIRouter,
    *,
    require_auth,
    discovery,
    orchestrator,
) -> None:

    # ── Text → Agent → Text ─────────────────────────────────────────
    @auth_router.post("/voice")
    async def voice_text(
        body: dict = Body(...),
        auth: tuple[str, str] = Depends(require_auth),
    ):
        """Minimal voice endpoint: text in → agent response out."""
        text = body.get("text", "").strip()
        if not text:
            raise HTTPException(400, "text erforderlich")

        cfg = _load_voice_config()
        agent_id = body.get("agent_id") or cfg.get("default_agent", "personal_admin")

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
        return {"text": response, "agent_id": agent_id}

    # ── Audio → Text (STT) ──────────────────────────────────────────
    @auth_router.post("/voice/stt")
    async def voice_stt(
        audio: UploadFile = File(...),
        _auth: tuple[str, str] = Depends(require_auth),
    ):
        """Send audio file → get transcribed text via faster-whisper."""
        import httpx

        cfg = _load_voice_config()
        stt_url = cfg.get("stt_url", STT_URL)

        audio_bytes = await audio.read()
        if not audio_bytes:
            raise HTTPException(400, "Leere Audio-Datei")

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{stt_url}/api/speech-to-text",
                    content=audio_bytes,
                    headers={
                        "Content-Type": audio.content_type or "audio/wav",
                    },
                )
                if resp.status_code != 200:
                    raise HTTPException(502, f"STT-Fehler: HTTP {resp.status_code}")
                data = resp.json()
                return {"text": data.get("text", ""), "language": data.get("language", "de")}
        except httpx.ConnectError:
            raise HTTPException(503, "STT-Service nicht erreichbar — ist die Voice-Extension installiert?")

    # ── Text → Audio (TTS) ──────────────────────────────────────────
    @auth_router.post("/voice/tts")
    async def voice_tts(
        body: dict = Body(...),
        _auth: tuple[str, str] = Depends(require_auth),
    ):
        """Send text → get audio WAV via Piper TTS."""
        import httpx

        text = body.get("text", "").strip()
        if not text:
            raise HTTPException(400, "text erforderlich")

        cfg = _load_voice_config()
        tts_url = cfg.get("tts_url", TTS_URL)

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    f"{tts_url}/api/text-to-speech",
                    json={"text": text},
                )
                if resp.status_code != 200:
                    raise HTTPException(502, f"TTS-Fehler: HTTP {resp.status_code}")
                return StreamingResponse(
                    iter([resp.content]),
                    media_type="audio/wav",
                    headers={"Content-Disposition": "inline; filename=voice.wav"},
                )
        except httpx.ConnectError:
            raise HTTPException(503, "TTS-Service nicht erreichbar — ist die Voice-Extension installiert?")

    # ── Full Pipeline: Audio → Agent → Audio ─────────────────────────
    @auth_router.post("/voice/pipeline")
    async def voice_pipeline(
        audio: UploadFile = File(...),
        agent_id: str = "",
        auth: tuple[str, str] = Depends(require_auth),
    ):
        """Full voice pipeline: audio in → STT → agent → TTS → audio out."""
        import httpx

        cfg = _load_voice_config()
        stt_url = cfg.get("stt_url", STT_URL)
        tts_url = cfg.get("tts_url", TTS_URL)
        agent_id = agent_id or cfg.get("default_agent", "personal_admin")

        # 1. STT
        audio_bytes = await audio.read()
        if not audio_bytes:
            raise HTTPException(400, "Leere Audio-Datei")

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                stt_resp = await client.post(
                    f"{stt_url}/api/speech-to-text",
                    content=audio_bytes,
                    headers={"Content-Type": audio.content_type or "audio/wav"},
                )
        except httpx.ConnectError:
            raise HTTPException(503, "STT-Service nicht erreichbar")

        if stt_resp.status_code != 200:
            raise HTTPException(502, f"STT-Fehler: HTTP {stt_resp.status_code}")

        user_text = stt_resp.json().get("text", "").strip()
        if not user_text:
            raise HTTPException(400, "Kein Text erkannt")

        # 2. Agent
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
            content=user_text,
            sender=auth[0],
            execution_mode="sync",
        )

        # 3. TTS
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                tts_resp = await client.post(
                    f"{tts_url}/api/text-to-speech",
                    json={"text": response},
                )
        except httpx.ConnectError:
            raise HTTPException(503, "TTS-Service nicht erreichbar")

        if tts_resp.status_code != 200:
            raise HTTPException(502, f"TTS-Fehler: HTTP {tts_resp.status_code}")

        return StreamingResponse(
            iter([tts_resp.content]),
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
        """Check STT/TTS service availability."""
        import httpx

        cfg = _load_voice_config()
        result = {
            "installed": Path("/opt/hydrahive-voice").exists(),
            "stt": {"url": cfg.get("stt_url", STT_URL), "available": False},
            "tts": {"url": cfg.get("tts_url", TTS_URL), "available": False},
            "default_agent": cfg.get("default_agent", "personal_admin"),
        }

        async with httpx.AsyncClient(timeout=5) as client:
            try:
                r = await client.get(f"{cfg.get('stt_url', STT_URL)}/api/info")
                result["stt"]["available"] = r.status_code == 200
                if r.status_code == 200:
                    result["stt"]["info"] = r.json()
            except Exception:
                pass

            try:
                r = await client.get(f"{cfg.get('tts_url', TTS_URL)}/api/info")
                result["tts"]["available"] = r.status_code == 200
                if r.status_code == 200:
                    result["tts"]["info"] = r.json()
            except Exception:
                pass

        return result
