"""
whatsapp_transcribe.py — Audio-Transkription für WhatsApp-Nachrichten

Strategie:
  1. Wyoming STT Service (Port 10300) — bevorzugt, gleicher Service wie /voice/stt
  2. faster-whisper direkt — Fallback wenn Wyoming nicht erreichbar

Audio-Konvertierung: OGG/Opus → WAV via ffmpeg (für Wyoming-Kompatibilität).
"""
from __future__ import annotations

import asyncio
import base64
import logging
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)


def _ogg_to_wav(audio_bytes: bytes, mime_type: str = "audio/ogg") -> bytes | None:
    """Konvertiert Audio-Bytes (OGG/MP3/M4A etc.) zu WAV via ffmpeg."""
    ext = _mime_to_ext(mime_type)
    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as src:
            src.write(audio_bytes)
            src_path = src.name
        wav_path = src_path + ".wav"
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", src_path, "-ar", "16000", "-ac", "1", "-f", "wav", wav_path],
            capture_output=True, timeout=30,
        )
        if result.returncode != 0:
            logger.error("ffmpeg Fehler: %s", result.stderr.decode(errors="replace")[:200])
            return None
        wav_bytes = Path(wav_path).read_bytes()
        return wav_bytes
    except Exception as e:
        logger.error("Audio-Konvertierung fehlgeschlagen: %s", e)
        return None
    finally:
        for p in [src_path, wav_path]:
            try:
                Path(p).unlink(missing_ok=True)
            except Exception:
                pass


async def _transcribe_via_wyoming(wav_bytes: bytes) -> str | None:
    """Transkribiert WAV-Audio über den Wyoming STT Service."""
    try:
        from .router_voice import _wyoming_stt, _load_voice_config
        cfg = _load_voice_config()
        text = await _wyoming_stt(wav_bytes, cfg.get("stt_host", "127.0.0.1"), cfg.get("stt_port", 10300))
        return text.strip() if text and text.strip() else None
    except Exception as e:
        logger.warning("Wyoming STT fehlgeschlagen: %s", e)
        return None


def _transcribe_via_whisper(audio_bytes: bytes, mime_type: str) -> str | None:
    """Fallback: Transkribiert direkt mit faster-whisper (braucht GPU/CPU + Modell)."""
    ext = _mime_to_ext(mime_type)
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        logger.error("faster-whisper nicht installiert und Wyoming STT nicht erreichbar")
        return None

    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        # Lazy-Load Modell
        global _whisper_model
        if "_whisper_model" not in globals() or _whisper_model is None:
            try:
                _whisper_model = WhisperModel("medium", device="cuda", compute_type="float16")
            except Exception:
                _whisper_model = WhisperModel("medium", device="cpu", compute_type="int8")

        segments, info = _whisper_model.transcribe(
            tmp_path, beam_size=5, language=None,
            vad_filter=True, vad_parameters={"min_silence_duration_ms": 500},
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        logger.info("Whisper-Transkription: %.1fs, Sprache=%s, %d Zeichen", info.duration, info.language, len(text))
        return text if text else None
    except Exception as e:
        logger.error("Whisper-Transkription fehlgeschlagen: %s", e)
        return None
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass

_whisper_model = None


def transcribe_audio_b64(data_b64: str, mime_type: str = "audio/ogg") -> str | None:
    """
    Transkribiert Audio aus Base64-Daten.
    Versucht zuerst Wyoming STT, dann faster-whisper als Fallback.
    """
    try:
        audio_bytes = base64.b64decode(data_b64)
    except Exception as e:
        logger.error("Base64-Dekodierung fehlgeschlagen: %s", e)
        return None

    # Strategie 1: Wyoming STT (OGG → WAV → Wyoming)
    wav_bytes = _ogg_to_wav(audio_bytes, mime_type)
    if wav_bytes:
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import concurrent.futures
                with concurrent.futures.ThreadPoolExecutor() as pool:
                    result = pool.submit(asyncio.run, _transcribe_via_wyoming(wav_bytes)).result(timeout=30)
            else:
                result = asyncio.run(_transcribe_via_wyoming(wav_bytes))
            if result:
                logger.info("Wyoming-Transkription erfolgreich: %d Zeichen", len(result))
                return result
        except Exception as e:
            logger.warning("Wyoming-Transkription fehlgeschlagen: %s", e)

    # Strategie 2: faster-whisper direkt (Fallback)
    logger.info("Fallback auf faster-whisper direkt")
    return _transcribe_via_whisper(audio_bytes, mime_type)


def _mime_to_ext(mime_type: str) -> str:
    mapping = {
        "audio/ogg":   ".ogg",
        "audio/mpeg":  ".mp3",
        "audio/mp4":   ".m4a",
        "audio/aac":   ".aac",
        "audio/webm":  ".webm",
        "audio/wav":   ".wav",
        "audio/x-wav": ".wav",
    }
    base = mime_type.split(";")[0].strip().lower()
    return mapping.get(base, ".ogg")
