"""
whatsapp_tts.py — Text-to-Speech für HydraHive WhatsApp

Konvertiert Antwort-Text zu OGG/Opus Audio via edge-tts (Microsoft Neural TTS).
Kein API-Key erforderlich.

Standard-Stimme: de-DE-KatjaNeural (Deutsch, weiblich, natürlich)
"""
from __future__ import annotations

import asyncio
import base64
import logging
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Microsoft Neural TTS Stimme — weitere Optionen:
# de-DE-KatjaNeural, de-DE-ConradNeural, de-AT-IngridNeural, de-CH-LeniNeural
DEFAULT_VOICE = "de-DE-KatjaNeural"


async def text_to_ogg_b64(text: str, voice: str = DEFAULT_VOICE) -> str | None:
    """
    Konvertiert Text zu OGG/Opus Base64 für WhatsApp Voice Notes.
    Gibt None zurück bei Fehler.
    """
    if not text or not text.strip():
        return None

    # Zu langen Text kürzen (TTS macht bei >5000 Zeichen Probleme)
    text = text.strip()
    if len(text) > 4000:
        text = text[:4000] + "…"

    tmp_mp3 = None
    tmp_ogg = None
    try:
        import edge_tts

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            tmp_mp3 = f.name
        with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
            tmp_ogg = f.name

        # TTS → MP3
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(tmp_mp3)

        # MP3 → OGG/Opus (WhatsApp-kompatibel)
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
            logger.error("ffmpeg TTS-Konvertierung fehlgeschlagen: %s", result.stderr.decode())
            return None

        audio_bytes = Path(tmp_ogg).read_bytes()
        logger.info("TTS fertig: %d Zeichen → %d KB Audio", len(text), len(audio_bytes) // 1024)
        return base64.b64encode(audio_bytes).decode()

    except Exception as e:
        logger.error("TTS-Fehler: %s", e)
        return None
    finally:
        for p in (tmp_mp3, tmp_ogg):
            if p:
                try:
                    Path(p).unlink(missing_ok=True)
                except Exception:
                    pass
