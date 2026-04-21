"""Typ-Definitionen für Voice-Provider (TTS/STT)."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AudioFormat:
    mime: str
    sample_rate: int = 0
    channels: int = 1
    codec: str = ""


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
