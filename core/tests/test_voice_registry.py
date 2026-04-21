"""Tests für Voice-Provider-Registry (#794 Commit A)."""
from __future__ import annotations

import pytest

from hydrahive_core.voice_providers import (
    AudioFormat,
    STTProvider,
    STTResult,
    TTSProvider,
    TTSResult,
    Voice,
    VoiceProviderRegistry,
    registry,
    setup_voice_registry,
)
from hydrahive_core.voice_providers.wyoming_stt import WyomingSTTProvider
from hydrahive_core.voice_providers.wyoming_tts import WyomingTTSProvider


def test_registry_registers_wyoming_providers_on_import():
    setup_voice_registry()
    assert "wyoming-stt" in registry.list_stt_providers()
    assert "wyoming-tts" in registry.list_tts_providers()


def test_registry_get_wyoming_providers():
    setup_voice_registry()
    stt = registry.get_stt("wyoming-stt")
    tts = registry.get_tts("wyoming-tts")
    assert isinstance(stt, WyomingSTTProvider)
    assert isinstance(tts, WyomingTTSProvider)


def test_registry_get_unknown_raises_keyerror():
    setup_voice_registry()
    with pytest.raises(KeyError):
        registry.get_stt("does-not-exist")
    with pytest.raises(KeyError):
        registry.get_tts("does-not-exist")


def test_registry_defaults_point_to_wyoming():
    setup_voice_registry()
    assert registry.get_default_stt().provider_id == "wyoming-stt"
    assert registry.get_default_tts().provider_id == "wyoming-tts"


def test_registry_set_default_unknown_raises():
    setup_voice_registry()
    with pytest.raises(KeyError):
        registry.set_default("stt", "nope")
    with pytest.raises(ValueError):
        registry.set_default("bogus", "wyoming-stt")


def test_provider_ids_unique_across_types():
    setup_voice_registry()
    all_ids = registry.list_stt_providers() + registry.list_tts_providers()
    assert len(all_ids) == len(set(all_ids))


def test_types_instantiate():
    af = AudioFormat(mime="audio/wav", sample_rate=22050, channels=1, codec="pcm")
    v = Voice(id="x", name="X", language="de", gender=None)
    s = STTResult(text="hi")
    t = TTSResult(audio=b"\x00", format=af)
    assert af.mime == "audio/wav"
    assert v.id == "x"
    assert s.text == "hi"
    assert t.audio == b"\x00"


def test_base_classes_are_abstract():
    with pytest.raises(TypeError):
        STTProvider()  # type: ignore[abstract]
    with pytest.raises(TypeError):
        TTSProvider()  # type: ignore[abstract]


def test_wyoming_tts_voice_list_de():
    setup_voice_registry()
    tts = registry.get_tts("wyoming-tts")
    import asyncio
    voices = asyncio.run(tts.get_voices(language="de"))
    assert any(v.id == "de_DE-thorsten-high" for v in voices)
    voices_en = asyncio.run(tts.get_voices(language="en"))
    assert voices_en == []


def test_wyoming_stt_languages():
    setup_voice_registry()
    stt = registry.get_stt("wyoming-stt")
    import asyncio
    langs = asyncio.run(stt.get_languages())
    assert "de" in langs


def test_fresh_registry_has_no_defaults():
    fresh = VoiceProviderRegistry()
    assert fresh.get_default_stt() is None
    assert fresh.get_default_tts() is None
    assert fresh.list_stt_providers() == []
    assert fresh.list_tts_providers() == []
