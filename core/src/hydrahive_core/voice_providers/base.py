"""Abstrakte Basisklassen für STT/TTS-Provider."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import ClassVar

from .types import STTResult, TTSResult, Voice


class STTProvider(ABC):
    provider_id: ClassVar[str]
    provider_name: ClassVar[str]

    @abstractmethod
    async def recognize(
        self, audio_bytes: bytes, *, language: str = "de"
    ) -> STTResult: ...

    @abstractmethod
    async def get_languages(self) -> list[str]: ...

    async def is_available(self) -> bool:
        return True


class TTSProvider(ABC):
    provider_id: ClassVar[str]
    provider_name: ClassVar[str]

    @abstractmethod
    async def synthesize(
        self, text: str, *, voice: str | None = None, **opts
    ) -> TTSResult: ...

    @abstractmethod
    async def get_voices(self, language: str | None = None) -> list[Voice]: ...

    async def is_available(self) -> bool:
        return True
