"""edge-tts TTS-Provider — Microsoft Neural TTS (kein API-Key nötig).

Registriert sich als Provider-ID ``edge-tts``. WhatsApp-User behalten damit
ihre bestehende KatjaNeural-Stimme.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import subprocess
import tempfile
from pathlib import Path
from typing import ClassVar

from .base import TTSProvider
from .types import AudioFormat, TTSResult, Voice

logger = logging.getLogger(__name__)

DEFAULT_VOICE = "de-DE-KatjaNeural"

_EDGE_VOICES: dict[str, Voice] = {
    "de-DE-KatjaNeural":    Voice(id="de-DE-KatjaNeural",    name="Katja (DE, weiblich)",     language="de", gender="female"),
    "de-DE-ConradNeural":   Voice(id="de-DE-ConradNeural",   name="Conrad (DE, männlich)",    language="de", gender="male"),
    "de-AT-IngridNeural":   Voice(id="de-AT-IngridNeural",   name="Ingrid (AT, weiblich)",    language="de", gender="female"),
    "de-CH-LeniNeural":     Voice(id="de-CH-LeniNeural",     name="Leni (CH, weiblich)",      language="de", gender="female"),
    "en-US-JennyNeural":    Voice(id="en-US-JennyNeural",    name="Jenny (US, female)",       language="en", gender="female"),
    "en-US-GuyNeural":      Voice(id="en-US-GuyNeural",      name="Guy (US, male)",           language="en", gender="male"),
    "en-GB-SoniaNeural":    Voice(id="en-GB-SoniaNeural",    name="Sonia (GB, female)",       language="en", gender="female"),
    "en-GB-RyanNeural":     Voice(id="en-GB-RyanNeural",     name="Ryan (GB, male)",          language="en", gender="male"),
    "fr-FR-DeniseNeural":   Voice(id="fr-FR-DeniseNeural",   name="Denise (FR, féminin)",     language="fr", gender="female"),
    "es-ES-ElviraNeural":   Voice(id="es-ES-ElviraNeural",   name="Elvira (ES, femenino)",    language="es", gender="female"),
    "it-IT-ElsaNeural":     Voice(id="it-IT-ElsaNeural",     name="Elsa (IT, femminile)",     language="it", gender="female"),
    "tr-TR-EmelNeural":     Voice(id="tr-TR-EmelNeural",     name="Emel (TR, kadın)",         language="tr", gender="female"),
    "pl-PL-AgnieszkaNeural":Voice(id="pl-PL-AgnieszkaNeural",name="Agnieszka (PL, kobieta)", language="pl", gender="female"),
    "ru-RU-SvetlanaNeural": Voice(id="ru-RU-SvetlanaNeural", name="Svetlana (RU, женский)",   language="ru", gender="female"),
}


_EMOJI_RE = __import__("re").compile(
    "["
    "\U0001F600-\U0001F64F"
    "\U0001F300-\U0001F5FF"
    "\U0001F680-\U0001F6FF"
    "\U0001F1E0-\U0001F1FF"
    "\U00002702-\U000027B0"
    "\U0000FE00-\U0000FE0F"
    "\U0001F900-\U0001F9FF"
    "\U00002600-\U000026FF"
    "\U0000200D"
    "\U00002B50-\U00002B55"
    "\U000023E9-\U000023F3"
    "\U0000FE0F"
    "]+", flags=__import__("re").UNICODE
)


def _clean_for_tts(text: str) -> str:
    import re
    text = re.sub(r'\*{1,3}(.+?)\*{1,3}', r'\1', text)
    text = re.sub(r'_{1,2}(.+?)_{1,2}', r'\1', text)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    text = re.sub(r'^[\-\*•]\s+', '', text, flags=re.MULTILINE)
    text = _EMOJI_RE.sub('', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)
    return text.strip()


class EdgeTTSProvider(TTSProvider):
    provider_id: ClassVar[str] = "edge-tts"
    provider_name: ClassVar[str] = "Microsoft Edge TTS"

    async def synthesize(self, text: str, *, voice: str | None = None, **opts) -> TTSResult:
        if not text.strip():
            raise ValueError("text darf nicht leer sein")

        voice_id = voice or DEFAULT_VOICE
        cleaned = _clean_for_tts(text.strip())
        if not cleaned:
            raise ValueError("text leer nach Bereinigung")
        if len(cleaned) > 4000:
            cleaned = cleaned[:4000]

        tmp_mp3 = None
        tmp_ogg = None
        try:
            import edge_tts

            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
                tmp_mp3 = f.name
            with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
                tmp_ogg = f.name

            communicate = edge_tts.Communicate(cleaned, voice_id)
            await communicate.save(tmp_mp3)

            result = subprocess.run(
                [
                    "ffmpeg", "-y",
                    "-i", tmp_mp3,
                    "-c:a", "libopus",
                    "-b:a", "32k",
                    "-vbr", "on",
                    "-application", "voip",
                    tmp_ogg,
                ],
                capture_output=True,
                timeout=30,
            )
            if result.returncode != 0:
                raise RuntimeError(f"ffmpeg failed: {result.stderr.decode()}")

            ogg_bytes = Path(tmp_ogg).read_bytes()
            return TTSResult(
                audio=ogg_bytes,
                format=AudioFormat(
                    mime="audio/ogg",
                    sample_rate=48000,
                    channels=1,
                    codec="opus",
                ),
            )
        finally:
            for p in (tmp_mp3, tmp_ogg):
                if p:
                    try:
                        Path(p).unlink(missing_ok=True)
                    except Exception:
                        pass

    async def get_voices(self, language: str | None = None) -> list[Voice]:
        if language is None:
            return list(_EDGE_VOICES.values())
        return [v for v in _EDGE_VOICES.values() if v.language == language]

    async def is_available(self) -> bool:
        try:
            import edge_tts
            return True
        except Exception:
            return False
