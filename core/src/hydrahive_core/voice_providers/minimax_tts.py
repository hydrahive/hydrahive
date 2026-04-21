"""MiniMax T2A v2 TTS-Provider (#795).

Endpoint: POST {base_url}/t2a_v2
Base-URL wird vom Media-Resolver geliefert (https://api.minimax.io/v1).
Response-Payload liefert hex-encoded Audio-Bytes (format="mp3" oder "pcm").
"""
from __future__ import annotations

import json
import logging
from typing import ClassVar

import httpx

from .base import TTSProvider
from .types import AudioFormat, TTSResult, Voice

logger = logging.getLogger(__name__)


MINIMAX_T2A_ENDPOINT = "/t2a_v2"
# Default-Modell: speech-02-hd. speech-02-turbo ist im Token-Plan NICHT enthalten
# (MiniMax-Fehler: "your current token plan not support model, speech-02-turbo").
# Übersteuerbar via voice.json → "minimax_tts_model".
DEFAULT_MODEL = "speech-02-hd"
DEFAULT_VOICE_ID = "male-qn-qingse"
DEFAULT_TIMEOUT_SECONDS = 30


# Hard-coded Voice-Katalog — MiniMax-Stimmen sind multilingual (inkl. DE).
# Sprache "mul" = multilingual; Filterung per language="de" liefert alle "mul"-Voices.
SUPPORTED_VOICES: dict[str, dict] = {
    "male-qn-qingse":    {"name": "Male — Jugendlich",  "gender": "male",   "language": "mul"},
    "male-qn-jingying":  {"name": "Male — Seriös",      "gender": "male",   "language": "mul"},
    "male-qn-badao":     {"name": "Male — Dominant",    "gender": "male",   "language": "mul"},
    "female-shaonv":     {"name": "Female — Jung",      "gender": "female", "language": "mul"},
    "female-yujie":      {"name": "Female — Elegant",   "gender": "female", "language": "mul"},
    "female-chengshu":   {"name": "Female — Reif",      "gender": "female", "language": "mul"},
    "female-tianmei":    {"name": "Female — Sanft",     "gender": "female", "language": "mul"},
    "presenter_male":    {"name": "Presenter — Male",   "gender": "male",   "language": "mul"},
    "presenter_female":  {"name": "Presenter — Female", "gender": "female", "language": "mul"},
}


class MinimaxTTSError(Exception):
    """Client-seitiger Fehler (Auth, Request-Shape, API-Rückgabe)."""


class MinimaxTTSProvider(TTSProvider):
    provider_id: ClassVar[str] = "minimax-t2a"
    provider_name: ClassVar[str] = "MiniMax T2A"

    def __init__(self, model: str = DEFAULT_MODEL) -> None:
        self._model = model

    # ── Key + Base-URL-Lookup (delegiert an den bestehenden Resolver) ──

    def _api_key(self) -> str | None:
        from ..minimax_image import _minimax_image_api_key
        return _minimax_image_api_key()

    def _base_url(self) -> str:
        from ..orchestrator_llm import _minimax_media_base_url
        return _minimax_media_base_url()

    # ── Provider-API ──────────────────────────────────────────────────

    async def synthesize(
        self, text: str, *, voice: str | None = None, **opts
    ) -> TTSResult:
        if not text.strip():
            raise MinimaxTTSError("text darf nicht leer sein")

        api_key = self._api_key()
        if not api_key:
            raise MinimaxTTSError("MiniMax API-Key fehlt")

        voice_id = voice or DEFAULT_VOICE_ID
        if voice_id not in SUPPORTED_VOICES:
            raise MinimaxTTSError(f"Unbekannte Voice-ID: {voice_id}")

        speed = float(opts.get("speed", 1.0))
        pitch = int(opts.get("pitch", 0))
        vol = float(opts.get("vol", 1.0))
        sample_rate = int(opts.get("sample_rate", 32000))
        bitrate = int(opts.get("bitrate", 128000))

        url = self._base_url().rstrip("/") + MINIMAX_T2A_ENDPOINT
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self._model,
            "text": text,
            "voice_setting": {
                "voice_id": voice_id,
                "speed": speed,
                "vol": vol,
                "pitch": pitch,
            },
            "audio_setting": {
                "sample_rate": sample_rate,
                "bitrate": bitrate,
                "format": "mp3",
                "channel": 1,
            },
        }

        async with httpx.AsyncClient(timeout=DEFAULT_TIMEOUT_SECONDS) as client:
            try:
                resp = await client.post(url, headers=headers, json=payload)
            except httpx.TimeoutException:
                raise MinimaxTTSError("TTS request timeout") from None
            except httpx.HTTPError as exc:
                raise MinimaxTTSError(f"TTS request failed: {type(exc).__name__}") from None

        if resp.status_code == 401:
            raise MinimaxTTSError("MiniMax API-Key abgelehnt (401)")
        if resp.status_code == 429:
            raise MinimaxTTSError("MiniMax rate limit (429) — später erneut")
        if resp.status_code >= 400:
            raise MinimaxTTSError(f"MiniMax API HTTP {resp.status_code}")

        try:
            body = resp.json()
        except (ValueError, json.JSONDecodeError):
            raise MinimaxTTSError("MiniMax response non-JSON") from None

        data = body.get("data") if isinstance(body, dict) else None
        audio_hex = data.get("audio") if isinstance(data, dict) else None
        if not isinstance(audio_hex, str) or not audio_hex:
            if isinstance(body, dict):
                br = body.get("base_resp") or {}
                msg = str(br.get("status_msg") or "").strip()
                if msg:
                    raise MinimaxTTSError(f"MiniMax: {msg[:200]}")
            raise MinimaxTTSError("MiniMax response missing data.audio")

        try:
            audio_bytes = bytes.fromhex(audio_hex)
        except ValueError:
            raise MinimaxTTSError("MiniMax data.audio hex decode failed") from None

        if not audio_bytes:
            raise MinimaxTTSError("MiniMax returned empty audio")

        return TTSResult(
            audio=audio_bytes,
            format=AudioFormat(
                mime="audio/mpeg",
                sample_rate=sample_rate,
                channels=1,
                codec="mp3",
            ),
        )

    async def get_voices(self, language: str | None = None) -> list[Voice]:
        out = [
            Voice(
                id=vid,
                name=meta["name"],
                language=meta["language"],
                gender=meta.get("gender"),
            )
            for vid, meta in SUPPORTED_VOICES.items()
        ]
        if language is None:
            return out
        # mul-Stimmen gelten für alle Sprachen
        return [v for v in out if v.language in ("mul", language)]

    async def is_available(self) -> bool:
        return self._api_key() is not None
