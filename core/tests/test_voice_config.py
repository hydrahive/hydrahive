"""Tests für VoiceConfigLayer (#794 Commit B)."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from hydrahive_core.voice_providers import registry, setup_voice_registry
from hydrahive_core.voice_providers import config as voice_config_mod


@pytest.fixture
def isolated_voice_config(tmp_path: Path, monkeypatch):
    setup_voice_registry()
    db_path = tmp_path / "voice.db"
    cfg_path = tmp_path / "voice.json"
    monkeypatch.setattr(voice_config_mod, "DB_PATH", db_path)
    monkeypatch.setattr(voice_config_mod, "CONFIG_FILE", cfg_path)
    layer = voice_config_mod.VoiceConfigLayer()
    yield layer
    if layer._db is not None:
        layer._db.close()


def test_default_provider_falls_back_to_registry(isolated_voice_config):
    layer = isolated_voice_config
    assert layer.get_tts_provider_for_user("alice").provider_id == "wyoming-tts"
    assert layer.get_stt_provider_for_user("alice").provider_id == "wyoming-stt"


def test_user_preference_overrides_default(isolated_voice_config):
    layer = isolated_voice_config
    layer.set_user_preference("alice", "tts", "wyoming-tts", voice_id="de_DE-thorsten-high")
    assert layer.get_voice_for_user("alice", "wyoming-tts") == "de_DE-thorsten-high"
    prefs = layer.get_user_preferences("alice")
    assert prefs["tts_provider"] == "wyoming-tts"
    assert prefs["tts_voice"] == "de_DE-thorsten-high"
    assert prefs["stt_provider"] is None


def test_set_preference_unknown_provider_raises(isolated_voice_config):
    layer = isolated_voice_config
    with pytest.raises(KeyError):
        layer.set_user_preference("alice", "tts", "does-not-exist")


def test_set_preference_invalid_type_raises(isolated_voice_config):
    layer = isolated_voice_config
    with pytest.raises(ValueError):
        layer.set_user_preference("alice", "both", "wyoming-tts")


def test_set_user_preference_upsert(isolated_voice_config):
    layer = isolated_voice_config
    layer.set_user_preference("alice", "tts", "wyoming-tts", voice_id="v1")
    layer.set_user_preference("alice", "tts", "wyoming-tts", voice_id="v2")
    assert layer.get_voice_for_user("alice", "wyoming-tts") == "v2"


def test_get_voice_unknown_user_returns_none(isolated_voice_config):
    layer = isolated_voice_config
    assert layer.get_voice_for_user("nobody", "wyoming-tts") is None


def test_global_provider_roundtrip(isolated_voice_config):
    layer = isolated_voice_config
    assert layer.get_global_provider_id("tts") is None
    layer.set_global_provider("tts", "wyoming-tts")
    assert layer.get_global_provider_id("tts") == "wyoming-tts"


def test_global_provider_unknown_raises(isolated_voice_config):
    layer = isolated_voice_config
    with pytest.raises(KeyError):
        layer.set_global_provider("tts", "nope")


def test_schema_is_created(isolated_voice_config):
    layer = isolated_voice_config
    conn = layer._ensure_db()
    rows = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='voice_preferences'"
    ).fetchall()
    assert len(rows) == 1


def test_preferences_isolated_per_user(isolated_voice_config):
    layer = isolated_voice_config
    layer.set_user_preference("alice", "tts", "wyoming-tts", voice_id="va")
    layer.set_user_preference("bob", "tts", "wyoming-tts", voice_id="vb")
    assert layer.get_voice_for_user("alice", "wyoming-tts") == "va"
    assert layer.get_voice_for_user("bob", "wyoming-tts") == "vb"
