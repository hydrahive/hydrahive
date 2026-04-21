"""Voice-Provider-Registry für TTS/STT.

Singleton `registry` wird beim Import über `setup_voice_registry()` mit den
Default-Providern (Wyoming-STT/TTS) befüllt. Weitere Provider werden in
späteren Issues hinzugefügt (#795 MiniMax T2A, #796 MiniMax ASR).
"""
from __future__ import annotations

import asyncio
import threading
from typing import cast

from .base import STTProvider, TTSProvider
from .types import AudioFormat, AudioFormatMismatchError, STTResult, TTSResult, Voice

__all__ = [
    "AudioFormat",
    "AudioFormatMismatchError",
    "STTProvider",
    "STTResult",
    "TTSProvider",
    "TTSResult",
    "Voice",
    "VoiceProviderRegistry",
    "registry",
    "setup_voice_registry",
]


class VoiceProviderRegistry:
    def __init__(self) -> None:
        self._stt: dict[str, STTProvider] = {}
        self._tts: dict[str, TTSProvider] = {}
        self._default_stt: str | None = None
        self._default_tts: str | None = None
        self._lock = asyncio.Lock()
        self._sync_lock = threading.Lock()

    def register(self, provider: STTProvider | TTSProvider) -> None:
        with self._sync_lock:
            if isinstance(provider, STTProvider):
                self._stt[provider.provider_id] = provider
                if self._default_stt is None:
                    self._default_stt = provider.provider_id
            elif isinstance(provider, TTSProvider):
                self._tts[provider.provider_id] = provider
                if self._default_tts is None:
                    self._default_tts = provider.provider_id
            else:
                raise TypeError(
                    f"Unknown provider type: {type(provider).__name__}"
                )

    def get_stt(self, provider_id: str) -> STTProvider:
        return self._stt[provider_id]

    def get_tts(self, provider_id: str) -> TTSProvider:
        return self._tts[provider_id]

    def list_stt_providers(self) -> list[str]:
        return list(self._stt.keys())

    def list_tts_providers(self) -> list[str]:
        return list(self._tts.keys())

    def get_default_stt(self) -> STTProvider | None:
        if self._default_stt is None:
            return None
        return self._stt.get(self._default_stt)

    def get_default_tts(self) -> TTSProvider | None:
        if self._default_tts is None:
            return None
        return self._tts.get(self._default_tts)

    def set_default(self, provider_type: str, provider_id: str) -> None:
        with self._sync_lock:
            if provider_type == "stt":
                if provider_id not in self._stt:
                    raise KeyError(f"STT provider not registered: {provider_id}")
                self._default_stt = provider_id
            elif provider_type == "tts":
                if provider_id not in self._tts:
                    raise KeyError(f"TTS provider not registered: {provider_id}")
                self._default_tts = provider_id
            else:
                raise ValueError(
                    f"provider_type must be 'stt' or 'tts', got {provider_type!r}"
                )


registry = VoiceProviderRegistry()
_setup_done = False
_setup_lock = threading.Lock()


def setup_voice_registry() -> VoiceProviderRegistry:
    global _setup_done
    with _setup_lock:
        if _setup_done:
            return registry
        from .wyoming_stt import WyomingSTTProvider
        from .wyoming_tts import WyomingTTSProvider
        registry.register(WyomingSTTProvider())
        registry.register(WyomingTTSProvider())
        _setup_done = True
    return registry


setup_voice_registry()
