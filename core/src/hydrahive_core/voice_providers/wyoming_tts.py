"""Wyoming-Piper TTS-Provider."""
from __future__ import annotations

import io
import wave
import asyncio

from .base import TTSProvider
from .types import AudioFormat, TTSResult, Voice
from . import wyoming_shared as ws


_DEFAULT_VOICE = Voice(
    id="de_DE-thorsten-high",
    name="Thorsten (Hoch)",
    language="de",
    gender="male",
)


class WyomingTTSProvider(TTSProvider):
    provider_id = "wyoming-tts"
    provider_name = "Wyoming Piper"

    def _cfg(self) -> tuple[str, int]:
        from ..router_voice import _load_voice_config
        cfg = _load_voice_config()
        return cfg["tts_host"], int(cfg["tts_port"])

    async def synthesize(
        self, text: str, *, voice: str | None = None, **opts
    ) -> TTSResult:
        host, port = self._cfg()
        reader, writer = await ws.open_connection(host, port)
        try:
            await ws.send_event(writer, "synthesize", {"text": text})

            audio_chunks: list[bytes] = []
            rate = 22050
            width = 2
            channels = 1

            while True:
                etype, data, payload = await ws.recv_event(reader)
                if etype == "audio-start":
                    rate = data.get("rate", rate)
                    width = data.get("width", width)
                    channels = data.get("channels", channels)
                elif etype == "audio-chunk":
                    audio_chunks.append(payload)
                elif etype == "audio-stop":
                    break
                elif etype == "error":
                    raise RuntimeError(data.get("text", "TTS error"))

            pcm = b"".join(audio_chunks)

            def _write_wav():
                buf = io.BytesIO()
                with wave.open(buf, "wb") as wf:
                    wf.setnchannels(channels)
                    wf.setsampwidth(width)
                    wf.setframerate(rate)
                    wf.writeframes(pcm)
                return buf.getvalue()

            wav_bytes = await asyncio.to_thread(_write_wav)
            return TTSResult(
                audio=wav_bytes,
                format=AudioFormat(
                    mime="audio/wav",
                    sample_rate=rate,
                    channels=channels,
                    codec="pcm",
                ),
            )
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def get_voices(self, language: str | None = None) -> list[Voice]:
        voices = [_DEFAULT_VOICE]
        if language is None:
            return voices
        return [v for v in voices if v.language == language]

    async def is_available(self) -> bool:
        try:
            host, port = self._cfg()
        except Exception:
            return False
        return await ws.probe_tcp(host, port)
