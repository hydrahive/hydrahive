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
import io
import json
import logging
import struct
import wave
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile, File
from fastapi.responses import Response

logger = logging.getLogger(__name__)

VOICE_CONFIG_FILE = Path("/etc/hydrahive/voice.json")

STT_HOST = "127.0.0.1"
STT_PORT = 10300
TTS_HOST = "127.0.0.1"
TTS_PORT = 10200


def _load_voice_config() -> dict:
    if VOICE_CONFIG_FILE.exists():
        try:
            return json.loads(VOICE_CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"stt_host": STT_HOST, "stt_port": STT_PORT,
            "tts_host": TTS_HOST, "tts_port": TTS_PORT,
            "default_agent": "personal_admin"}


# ── Wyoming Protocol Helpers ─────────────────────────────────────────
# Wyoming is a simple line-based protocol: JSON event header + optional binary payload.

async def _wyoming_send_event(writer: asyncio.StreamWriter, event: dict, payload: bytes = b""):
    """Send a Wyoming event (JSON line + optional binary payload)."""
    header = json.dumps(event, separators=(",", ":"))
    writer.write(header.encode("utf-8") + b"\n")
    if payload:
        writer.write(payload)
    await writer.drain()


async def _wyoming_recv_event(reader: asyncio.StreamReader) -> tuple[dict, bytes]:
    """Receive a Wyoming event. Returns (header_dict, payload_bytes)."""
    line = await asyncio.wait_for(reader.readline(), timeout=30)
    if not line:
        raise ConnectionError("Wyoming connection closed")
    header = json.loads(line.decode("utf-8"))
    payload = b""
    payload_length = header.get("data", {}).get("payload_length", 0)
    if payload_length > 0:
        payload = await asyncio.wait_for(reader.readexactly(payload_length), timeout=30)
    return header, payload


async def _wyoming_stt(audio_bytes: bytes, host: str, port: int) -> str:
    """Send audio to Wyoming STT server, return transcribed text."""
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=10
    )
    try:
        # Parse WAV to get audio parameters
        with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
            rate = wf.getframerate()
            width = wf.getsampwidth()
            channels = wf.getnchannels()
            frames = wf.readframes(wf.getnframes())

        # Send Transcribe event
        await _wyoming_send_event(writer, {
            "type": "transcribe",
            "data": {"language": "de"},
        })

        # Send AudioStart
        await _wyoming_send_event(writer, {
            "type": "audio-start",
            "data": {
                "rate": rate,
                "width": width,
                "channels": channels,
            },
        })

        # Send audio in chunks
        chunk_size = rate * width * channels  # ~1 second chunks
        for i in range(0, len(frames), chunk_size):
            chunk = frames[i:i + chunk_size]
            await _wyoming_send_event(writer, {
                "type": "audio-chunk",
                "data": {
                    "rate": rate,
                    "width": width,
                    "channels": channels,
                    "payload_length": len(chunk),
                },
            }, chunk)

        # Send AudioStop
        await _wyoming_send_event(writer, {"type": "audio-stop", "data": {}})

        # Wait for Transcript
        while True:
            event, _ = await _wyoming_recv_event(reader)
            if event.get("type") == "transcript":
                return event.get("data", {}).get("text", "")
            if event.get("type") == "error":
                raise RuntimeError(event.get("data", {}).get("text", "STT error"))
    finally:
        writer.close()
        await writer.wait_closed()


async def _wyoming_tts(text: str, host: str, port: int) -> bytes:
    """Send text to Wyoming TTS server, return WAV audio bytes."""
    reader, writer = await asyncio.wait_for(
        asyncio.open_connection(host, port), timeout=10
    )
    try:
        # Send Synthesize event
        await _wyoming_send_event(writer, {
            "type": "synthesize",
            "data": {"text": text},
        })

        # Collect audio chunks
        audio_chunks: list[bytes] = []
        rate = 22050
        width = 2
        channels = 1

        while True:
            event, payload = await _wyoming_recv_event(reader)
            etype = event.get("type", "")

            if etype == "audio-start":
                rate = event["data"].get("rate", rate)
                width = event["data"].get("width", width)
                channels = event["data"].get("channels", channels)

            elif etype == "audio-chunk":
                audio_chunks.append(payload)

            elif etype == "audio-stop":
                break

            elif etype == "error":
                raise RuntimeError(event.get("data", {}).get("text", "TTS error"))

        # Build WAV
        pcm = b"".join(audio_chunks)
        buf = io.BytesIO()
        with wave.open(buf, "wb") as wf:
            wf.setnchannels(channels)
            wf.setsampwidth(width)
            wf.setframerate(rate)
            wf.writeframes(pcm)
        return buf.getvalue()
    finally:
        writer.close()
        await writer.wait_closed()


# ── Routes ───────────────────────────────────────────────────────────

def register_voice_routes(
    auth_router: APIRouter,
    *,
    require_auth,
    discovery,
    orchestrator,
) -> None:

    async def _agent_respond(text: str, agent_id: str, username: str) -> str:
        """Send text to agent and return response."""
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
            sender=username,
            execution_mode="sync",
        )
        return response

    # ── Text → Agent → Text ─────────────────────────────────────────
    @auth_router.post("/voice")
    async def voice_text(
        body: dict = Body(...),
        auth: tuple[str, str] = Depends(require_auth),
    ):
        text = body.get("text", "").strip()
        if not text:
            raise HTTPException(400, "text erforderlich")
        cfg = _load_voice_config()
        agent_id = body.get("agent_id") or cfg.get("default_agent", "personal_admin")
        response = await _agent_respond(text, agent_id, auth[0])
        return {"text": response, "agent_id": agent_id}

    # ── Audio → Text (STT) ──────────────────────────────────────────
    @auth_router.post("/voice/stt")
    async def voice_stt(
        audio: UploadFile = File(...),
        _auth: tuple[str, str] = Depends(require_auth),
    ):
        cfg = _load_voice_config()
        audio_bytes = await audio.read()
        if not audio_bytes:
            raise HTTPException(400, "Leere Audio-Datei")
        try:
            text = await _wyoming_stt(
                audio_bytes,
                cfg.get("stt_host", STT_HOST),
                cfg.get("stt_port", STT_PORT),
            )
            return {"text": text}
        except (ConnectionRefusedError, OSError):
            raise HTTPException(503, "STT-Service nicht erreichbar — ist die Voice-Extension installiert?")

    # ── Text → Audio (TTS) ──────────────────────────────────────────
    @auth_router.post("/voice/tts")
    async def voice_tts(
        body: dict = Body(...),
        _auth: tuple[str, str] = Depends(require_auth),
    ):
        text = body.get("text", "").strip()
        if not text:
            raise HTTPException(400, "text erforderlich")
        cfg = _load_voice_config()
        try:
            wav = await _wyoming_tts(
                text,
                cfg.get("tts_host", TTS_HOST),
                cfg.get("tts_port", TTS_PORT),
            )
            return Response(content=wav, media_type="audio/wav",
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
            user_text = await _wyoming_stt(
                audio_bytes,
                cfg.get("stt_host", STT_HOST),
                cfg.get("stt_port", STT_PORT),
            )
        except (ConnectionRefusedError, OSError):
            raise HTTPException(503, "STT-Service nicht erreichbar")
        if not user_text:
            raise HTTPException(400, "Kein Text erkannt")

        # 2. Agent
        response = await _agent_respond(user_text, agent_id, auth[0])

        # 3. TTS
        try:
            wav = await _wyoming_tts(
                response,
                cfg.get("tts_host", TTS_HOST),
                cfg.get("tts_port", TTS_PORT),
            )
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
            "installed": Path("/opt/hydrahive-voice").exists(),
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
