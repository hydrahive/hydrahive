"""Tests für MinimaxTTSProvider (#795)."""
from __future__ import annotations

import asyncio

import httpx
import pytest

from hydrahive_core.voice_providers.minimax_tts import (
    MinimaxTTSError,
    MinimaxTTSProvider,
    SUPPORTED_VOICES,
)
from hydrahive_core.voice_providers.types import AudioFormat, TTSResult


def test_provider_ids():
    p = MinimaxTTSProvider()
    assert p.provider_id == "minimax-t2a"
    assert p.provider_name == "MiniMax T2A"


@pytest.mark.asyncio
async def test_get_voices_all():
    p = MinimaxTTSProvider()
    voices = await p.get_voices()
    assert len(voices) == len(SUPPORTED_VOICES)
    ids = {v.id for v in voices}
    assert "male-qn-qingse" in ids
    assert "female-shaonv" in ids


@pytest.mark.asyncio
async def test_get_voices_de_filters_multilingual():
    p = MinimaxTTSProvider()
    de = await p.get_voices(language="de")
    # Alle sind "mul", also sollten alle für "de" zurückkommen
    assert len(de) == len(SUPPORTED_VOICES)


@pytest.mark.asyncio
async def test_synthesize_empty_text_raises():
    p = MinimaxTTSProvider()
    with pytest.raises(MinimaxTTSError, match="leer"):
        await p.synthesize("   ")


@pytest.mark.asyncio
async def test_synthesize_missing_key_raises(monkeypatch):
    p = MinimaxTTSProvider()
    monkeypatch.setattr(p, "_api_key", lambda: None)
    with pytest.raises(MinimaxTTSError, match="API-Key"):
        await p.synthesize("Hallo")


@pytest.mark.asyncio
async def test_synthesize_unknown_voice_raises(monkeypatch):
    p = MinimaxTTSProvider()
    monkeypatch.setattr(p, "_api_key", lambda: "test-key")
    with pytest.raises(MinimaxTTSError, match="Unbekannte Voice-ID"):
        await p.synthesize("Hallo", voice="does-not-exist")


@pytest.mark.asyncio
async def test_synthesize_happy_path(monkeypatch):
    """Mockt httpx.AsyncClient.post mit hex-encoded 'MP3'-Bytes."""
    fake_mp3 = b"\xff\xfb\x90\x44test-audio"
    fake_hex = fake_mp3.hex()

    class FakeResp:
        status_code = 200
        def json(self):
            return {"data": {"audio": fake_hex}, "base_resp": {"status_code": 0}}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): return FakeResp()

    monkeypatch.setattr("hydrahive_core.voice_providers.minimax_tts.httpx.AsyncClient", FakeClient)

    p = MinimaxTTSProvider()
    monkeypatch.setattr(p, "_api_key", lambda: "test-key")
    monkeypatch.setattr(p, "_base_url", lambda: "https://api.minimax.io/v1")

    result = await p.synthesize("Hallo Welt", voice="female-shaonv")
    assert isinstance(result, TTSResult)
    assert result.audio == fake_mp3
    assert result.format.mime == "audio/mpeg"
    assert result.format.codec == "mp3"


@pytest.mark.asyncio
async def test_synthesize_base_resp_error(monkeypatch):
    class FakeResp:
        status_code = 200
        def json(self):
            return {"data": {}, "base_resp": {"status_code": 1002, "status_msg": "quota exceeded"}}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): return FakeResp()

    monkeypatch.setattr("hydrahive_core.voice_providers.minimax_tts.httpx.AsyncClient", FakeClient)

    p = MinimaxTTSProvider()
    monkeypatch.setattr(p, "_api_key", lambda: "test-key")
    monkeypatch.setattr(p, "_base_url", lambda: "https://api.minimax.io/v1")

    with pytest.raises(MinimaxTTSError, match="quota exceeded"):
        await p.synthesize("Hallo")


@pytest.mark.asyncio
async def test_synthesize_http_401_raises(monkeypatch):
    class FakeResp:
        status_code = 401
        def json(self): return {}

    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k): return FakeResp()

    monkeypatch.setattr("hydrahive_core.voice_providers.minimax_tts.httpx.AsyncClient", FakeClient)

    p = MinimaxTTSProvider()
    monkeypatch.setattr(p, "_api_key", lambda: "test-key")
    monkeypatch.setattr(p, "_base_url", lambda: "https://api.minimax.io/v1")

    with pytest.raises(MinimaxTTSError, match="401"):
        await p.synthesize("Hallo")


@pytest.mark.asyncio
async def test_synthesize_timeout_raises(monkeypatch):
    class FakeClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        async def post(self, *a, **k):
            raise httpx.TimeoutException("timeout")

    monkeypatch.setattr("hydrahive_core.voice_providers.minimax_tts.httpx.AsyncClient", FakeClient)

    p = MinimaxTTSProvider()
    monkeypatch.setattr(p, "_api_key", lambda: "test-key")
    monkeypatch.setattr(p, "_base_url", lambda: "https://api.minimax.io/v1")

    with pytest.raises(MinimaxTTSError, match="timeout"):
        await p.synthesize("Hallo")


@pytest.mark.asyncio
async def test_is_available_without_key(monkeypatch):
    p = MinimaxTTSProvider()
    monkeypatch.setattr(p, "_api_key", lambda: None)
    assert await p.is_available() is False


@pytest.mark.asyncio
async def test_is_available_with_key(monkeypatch):
    p = MinimaxTTSProvider()
    monkeypatch.setattr(p, "_api_key", lambda: "test-key")
    assert await p.is_available() is True
