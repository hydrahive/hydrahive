"""Tests für AudioFormat.normalize() + synthesize_for_user (#798)."""
from __future__ import annotations

import pytest
from pathlib import Path

from hydrahive_core.voice_providers import AudioFormat, TTSResult, AudioFormatMismatchError
from hydrahive_core.voice_providers.config import VoiceConfigLayer
from hydrahive_core.voice_providers import config as voice_config_mod, setup_voice_registry


def test_normalize_whatsapp_already_ogg_opus():
    af = AudioFormat(mime="audio/ogg", sample_rate=48000, channels=1, codec="opus")
    result = AudioFormat.normalize(b"ogg_data", af, target="whatsapp")
    assert result.audio == b"ogg_data"
    assert result.format.codec == "opus"


def test_normalize_telegram_already_mp3():
    af = AudioFormat(mime="audio/mpeg", sample_rate=32000, channels=1, codec="mp3")
    result = AudioFormat.normalize(b"mp3_data", af, target="telegram")
    assert result.audio == b"mp3_data"
    assert result.format.mime == "audio/mpeg"


def test_normalize_web_already_wav():
    af = AudioFormat(mime="audio/wav", sample_rate=22050, channels=1, codec="pcm")
    result = AudioFormat.normalize(b"wav_data", af, target="web")
    assert result.audio == b"wav_data"
    assert result.format.codec == "pcm"


def test_normalize_unknown_target_falls_back_to_web():
    af = AudioFormat(mime="audio/wav", sample_rate=22050, channels=1, codec="pcm")
    result = AudioFormat.normalize(b"wav_data", af, target="unknown")
    assert result.format.mime == "audio/wav"


def test_normalize_whatsapp_ogg_passthrough_is_noop():
    af = AudioFormat(mime="audio/ogg", sample_rate=48000, channels=1, codec="opus")
    result = AudioFormat.normalize(b"real_ogg_bytes", af, target="whatsapp")
    assert result.audio == b"real_ogg_bytes"
    assert result.format.mime == "audio/ogg"
    assert result.format.codec == "opus"


@pytest.fixture
def isolated_config_layer(tmp_path: Path, monkeypatch):
    setup_voice_registry()
    db_path = tmp_path / "voice.db"
    cfg_path = tmp_path / "voice.json"
    monkeypatch.setattr(voice_config_mod, "DB_PATH", db_path)
    monkeypatch.setattr(voice_config_mod, "CONFIG_FILE", cfg_path)
    layer = VoiceConfigLayer()
    yield layer
    if layer._db is not None:
        layer._db.close()


@pytest.mark.asyncio
async def test_synthesize_for_user_routes_via_registry(isolated_config_layer):
    layer = isolated_config_layer
    layer.set_global_provider("tts", "edge-tts")
    result = await layer.synthesize_for_user(
        "alice", "Hallo Welt", integration="whatsapp", voice="de-DE-KatjaNeural"
    )
    assert result.audio != b""
    assert result.format.mime == "audio/ogg"
    assert result.format.codec == "opus"


@pytest.mark.asyncio
async def test_synthesize_for_user_telegram_target(isolated_config_layer):
    layer = isolated_config_layer
    layer.set_global_provider("tts", "edge-tts")
    result = await layer.synthesize_for_user(
        "alice", "Test", integration="telegram", voice="de-DE-KatjaNeural"
    )
    assert result.audio != b""
    assert result.format.mime in ("audio/ogg", "audio/mpeg")


@pytest.mark.asyncio
async def test_synthesize_for_user_web_target(isolated_config_layer):
    layer = isolated_config_layer
    layer.set_global_provider("tts", "edge-tts")
    result = await layer.synthesize_for_user(
        "alice", "Test", integration="web", voice="de-DE-KatjaNeural"
    )
    assert result.audio != b""
