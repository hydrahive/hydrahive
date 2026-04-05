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

_EMOJI_RE = __import__("re").compile(
    "["
    "\U0001F600-\U0001F64F"  # Emoticons
    "\U0001F300-\U0001F5FF"  # Symbols & Pictographs
    "\U0001F680-\U0001F6FF"  # Transport & Map
    "\U0001F1E0-\U0001F1FF"  # Flags
    "\U00002702-\U000027B0"  # Dingbats
    "\U0000FE00-\U0000FE0F"  # Variation Selectors
    "\U0001F900-\U0001F9FF"  # Supplemental Symbols
    "\U00002600-\U000026FF"  # Misc Symbols
    "\U0000200D"             # ZWJ
    "\U00002B50-\U00002B55"  # Stars
    "\U000023E9-\U000023F3"  # Media controls
    "\U0000FE0F"             # Variation Selector
    "]+", flags=__import__("re").UNICODE
)


def _clean_for_tts(text: str) -> str:
    """Entfernt Markdown, Emojis und Sonderzeichen die TTS schlecht vorliest."""
    import re
    # Markdown Bold/Italic entfernen
    text = re.sub(r'\*{1,3}(.+?)\*{1,3}', r'\1', text)
    text = re.sub(r'_{1,2}(.+?)_{1,2}', r'\1', text)
    # Markdown Links [text](url) → text
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    # Code-Blöcke und Inline-Code
    text = re.sub(r'```[\s\S]*?```', '', text)
    text = re.sub(r'`([^`]+)`', r'\1', text)
    # Markdown Überschriften
    text = re.sub(r'^#{1,6}\s+', '', text, flags=re.MULTILINE)
    # Aufzählungszeichen
    text = re.sub(r'^[\-\*•]\s+', '', text, flags=re.MULTILINE)
    # Emojis entfernen
    text = _EMOJI_RE.sub('', text)
    # Doppelte Leerzeichen/Zeilenumbrüche bereinigen
    text = re.sub(r'\n{3,}', '\n\n', text)
    text = re.sub(r' {2,}', ' ', text)
    return text.strip()


async def text_to_ogg_b64(text: str, voice: str = DEFAULT_VOICE) -> str | None:
    """
    Konvertiert Text zu OGG/Opus Base64 für WhatsApp Voice Notes.
    Gibt None zurück bei Fehler.
    """
    if not text or not text.strip():
        return None

    # Text für TTS bereinigen — Markdown, Emojis und Sonderzeichen entfernen
    text = _clean_for_tts(text.strip())
    if not text:
        return None
    if len(text) > 4000:
        text = text[:4000]

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
