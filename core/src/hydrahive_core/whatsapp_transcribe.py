"""
whatsapp_transcribe.py — faster-whisper Transcription für HydraHive WhatsApp

Transkribiert eingehende Audio/PTT-Nachrichten mit faster-whisper (GPU).
Modell wird beim ersten Aufruf geladen und dann im Speicher gehalten.
"""
from __future__ import annotations

import base64
import logging
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# Lazy-loaded model
_model = None
_model_size = "medium"  # medium = gut für Deutsch; large-v3 = maximal genau (braucht ~10 GB VRAM)


def _get_model():
    global _model
    if _model is None:
        from faster_whisper import WhisperModel
        logger.info("Lade faster-whisper Modell '%s' (GPU)…", _model_size)
        try:
            _model = WhisperModel(_model_size, device="cuda", compute_type="float16")
            logger.info("faster-whisper Modell geladen (CUDA/float16)")
        except Exception as e:
            logger.warning("CUDA nicht verfügbar (%s), fallback auf CPU", e)
            _model = WhisperModel(_model_size, device="cpu", compute_type="int8")
            logger.info("faster-whisper Modell geladen (CPU/int8)")
    return _model


def transcribe_audio_b64(data_b64: str, mime_type: str = "audio/ogg") -> str | None:
    """
    Transkribiert Audio aus Base64-Daten.
    Gibt den transkribierten Text zurück oder None bei Fehler.
    """
    try:
        audio_bytes = base64.b64decode(data_b64)
    except Exception as e:
        logger.error("Base64-Dekodierung fehlgeschlagen: %s", e)
        return None

    # Dateiendung aus MIME-Typ ableiten
    ext = _mime_to_ext(mime_type)

    try:
        with tempfile.NamedTemporaryFile(suffix=ext, delete=False) as tmp:
            tmp.write(audio_bytes)
            tmp_path = tmp.name

        model = _get_model()
        segments, info = model.transcribe(
            tmp_path,
            beam_size=5,
            language=None,  # auto-detect
            vad_filter=True,
            vad_parameters={"min_silence_duration_ms": 500},
        )
        text = " ".join(seg.text.strip() for seg in segments).strip()
        logger.info(
            "Transkription fertig: %.1fs Audio, Sprache=%s, %d Zeichen",
            info.duration,
            info.language,
            len(text),
        )
        return text if text else None
    except Exception as e:
        logger.error("Transkription fehlgeschlagen: %s", e)
        return None
    finally:
        try:
            Path(tmp_path).unlink(missing_ok=True)
        except Exception:
            pass


def _mime_to_ext(mime_type: str) -> str:
    mapping = {
        "audio/ogg":  ".ogg",
        "audio/mpeg": ".mp3",
        "audio/mp4":  ".m4a",
        "audio/aac":  ".aac",
        "audio/webm": ".webm",
        "audio/wav":  ".wav",
        "audio/x-wav": ".wav",
    }
    base = mime_type.split(";")[0].strip().lower()
    return mapping.get(base, ".ogg")
