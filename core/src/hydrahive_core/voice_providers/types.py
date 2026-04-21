"""Typ-Definitionen für Voice-Provider (TTS/STT)."""
from __future__ import annotations

import io
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

AudioTarget = Literal["whatsapp", "telegram", "web"]


@dataclass(frozen=True)
class AudioFormat:
    mime: str
    sample_rate: int = 0
    channels: int = 1
    codec: str = ""

    @staticmethod
    def normalize(
        audio_bytes: bytes, fmt: AudioFormat, *, target: AudioTarget = "web"
    ) -> TTSResult:
        """Konvertiert Audio in das für die Integration passende Format."""
        if target == "whatsapp":
            if fmt.mime == "audio/ogg" and fmt.codec == "opus":
                return TTSResult(audio=audio_bytes, format=fmt)
            _tmp_in = None
            _tmp_out = None
            try:
                suffix_in = ".mp3" if fmt.mime == "audio/mpeg" else ".wav" if "wav" in fmt.mime else ".bin"
                with tempfile.NamedTemporaryFile(suffix=suffix_in, delete=False) as f:
                    _tmp_in = f.name
                    f.write(audio_bytes)
                with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
                    _tmp_out = f.name
                res = subprocess.run(
                    ["ffmpeg", "-y", "-i", _tmp_in,
                     "-c:a", "libopus", "-b:a", "32k", "-vbr", "on",
                     "-application", "voip", _tmp_out],
                    capture_output=True, timeout=30,
                )
                if res.returncode != 0:
                    raise AudioFormatMismatchError(f"ffmpeg whatsapp conversion failed: {res.stderr.decode()}")
                out_bytes = Path(_tmp_out).read_bytes()
                return TTSResult(
                    audio=out_bytes,
                    format=AudioFormat(mime="audio/ogg", sample_rate=48000, channels=1, codec="opus"),
                )
            finally:
                for p in (_tmp_in, _tmp_out):
                    if p:
                        try:
                            Path(p).unlink(missing_ok=True)
                        except Exception:
                            pass
        elif target == "telegram":
            if fmt.mime in ("audio/ogg", "audio/mpeg"):
                return TTSResult(audio=audio_bytes, format=fmt)
            _tmp_in = None
            _tmp_out = None
            try:
                suffix_in = ".mp3" if fmt.mime == "audio/mpeg" else ".wav"
                with tempfile.NamedTemporaryFile(suffix=suffix_in, delete=False) as f:
                    _tmp_in = f.name
                    f.write(audio_bytes)
                with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as f:
                    _tmp_out = f.name
                res = subprocess.run(
                    ["ffmpeg", "-y", "-i", _tmp_in,
                     "-c:a", "libopus", "-b:a", "32k", "-vbr", "on",
                     "-application", "voip", _tmp_out],
                    capture_output=True, timeout=30,
                )
                if res.returncode != 0:
                    raise AudioFormatMismatchError(f"ffmpeg telegram conversion failed: {res.stderr.decode()}")
                out_bytes = Path(_tmp_out).read_bytes()
                return TTSResult(
                    audio=out_bytes,
                    format=AudioFormat(mime="audio/ogg", sample_rate=48000, channels=1, codec="opus"),
                )
            finally:
                for p in (_tmp_in, _tmp_out):
                    if p:
                        try:
                            Path(p).unlink(missing_ok=True)
                        except Exception:
                            pass
        else:
            if fmt.mime == "audio/wav" and fmt.codec == "pcm":
                return TTSResult(audio=audio_bytes, format=fmt)
            _tmp_in = None
            try:
                suffix_in = ".mp3" if fmt.mime == "audio/mpeg" else ".ogg" if "ogg" in fmt.mime else ".bin"
                with tempfile.NamedTemporaryFile(suffix=suffix_in, delete=False) as f:
                    _tmp_in = f.name
                    f.write(audio_bytes)
                res = subprocess.run(
                    ["ffmpeg", "-y", "-i", _tmp_in, "-f", "wav", "-"],
                    capture_output=True, timeout=30,
                )
                if res.returncode != 0:
                    raise AudioFormatMismatchError(f"ffmpeg wav conversion failed: {res.stderr.decode()}")
                return TTSResult(
                    audio=res.stdout,
                    format=AudioFormat(mime="audio/wav", sample_rate=22050, channels=1, codec="pcm"),
                )
            finally:
                if _tmp_in:
                    try:
                        Path(_tmp_in).unlink(missing_ok=True)
                    except Exception:
                        pass


@dataclass(frozen=True)
class Voice:
    id: str
    name: str
    language: str
    gender: str | None = None


@dataclass
class STTResult:
    text: str
    language: str | None = None
    confidence: float | None = None


@dataclass
class TTSResult:
    audio: bytes
    format: AudioFormat
    duration_sec: float | None = None


class AudioFormatMismatchError(Exception):
    pass
