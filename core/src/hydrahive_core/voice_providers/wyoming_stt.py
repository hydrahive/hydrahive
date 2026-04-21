"""Wyoming-Whisper STT-Provider."""
from __future__ import annotations

import io
import wave
import asyncio

from .base import STTProvider
from .types import STTResult
from . import wyoming_shared as ws


class WyomingSTTProvider(STTProvider):
    provider_id = "wyoming-stt"
    provider_name = "Wyoming Whisper"

    def _cfg(self) -> tuple[str, int]:
        from ..router_voice import _load_voice_config
        cfg = _load_voice_config()
        return cfg["stt_host"], int(cfg["stt_port"])

    async def recognize(
        self, audio_bytes: bytes, *, language: str = "de"
    ) -> STTResult:
        host, port = self._cfg()
        reader, writer = await ws.open_connection(host, port)
        try:
            def _read_wav():
                with wave.open(io.BytesIO(audio_bytes), "rb") as wf:
                    return (
                        wf.getframerate(),
                        wf.getsampwidth(),
                        wf.getnchannels(),
                        wf.readframes(wf.getnframes()),
                    )
            rate, width, channels, frames = await asyncio.to_thread(_read_wav)

            await ws.send_event(writer, "transcribe", {"language": language})
            await ws.send_event(
                writer, "audio-start",
                {"rate": rate, "width": width, "channels": channels},
            )

            chunk_size = rate * width * channels
            for i in range(0, len(frames), chunk_size):
                chunk = frames[i:i + chunk_size]
                await ws.send_event(
                    writer, "audio-chunk",
                    {"rate": rate, "width": width, "channels": channels},
                    chunk,
                )

            await ws.send_event(writer, "audio-stop")

            while True:
                etype, data, _ = await ws.recv_event(reader)
                if etype == "transcript":
                    return STTResult(
                        text=data.get("text", ""), language=language
                    )
                if etype == "error":
                    raise RuntimeError(data.get("text", "STT error"))
        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass

    async def get_languages(self) -> list[str]:
        return ["de", "en"]

    async def is_available(self) -> bool:
        try:
            host, port = self._cfg()
        except Exception:
            return False
        return await ws.probe_tcp(host, port)
