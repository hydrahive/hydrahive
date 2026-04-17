"""#682: tri-state Merge für base_url in _merge_provider_config.

Pydantic-Model hat base_url als Optional: None=nicht-ändern, ""=löschen,
Wert=setzen. Der Handler delegiert an _merge_provider_config — hier
direkt getestet, ohne FastAPI-Setup.
"""
from __future__ import annotations

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.router_llm import _merge_provider_config


def test_base_url_none_keeps_existing_override():
    existing = {"api_key": "secret", "enabled": True, "base_url": "https://api.minimax.chat/v1"}
    merged = _merge_provider_config(existing, api_key=None, enabled=True, base_url=None)
    assert merged["base_url"] == "https://api.minimax.chat/v1"
    assert merged["api_key"] == "secret"


def test_base_url_none_without_existing_stays_absent():
    existing = {"api_key": "secret", "enabled": True}
    merged = _merge_provider_config(existing, api_key=None, enabled=True, base_url=None)
    assert "base_url" not in merged


def test_base_url_empty_removes_existing_override():
    existing = {"api_key": "secret", "enabled": True, "base_url": "https://api.minimax.chat/v1"}
    merged = _merge_provider_config(existing, api_key=None, enabled=True, base_url="")
    assert "base_url" not in merged
    assert merged["api_key"] == "secret"


def test_base_url_empty_on_entry_without_override_is_noop():
    existing = {"api_key": "secret", "enabled": True}
    merged = _merge_provider_config(existing, api_key=None, enabled=True, base_url="")
    assert "base_url" not in merged


def test_base_url_value_sets_override():
    existing = {"api_key": "secret", "enabled": True}
    merged = _merge_provider_config(
        existing, api_key=None, enabled=True, base_url="https://api.minimax.chat/v1"
    )
    assert merged["base_url"] == "https://api.minimax.chat/v1"


def test_base_url_value_replaces_existing_override():
    existing = {"api_key": "secret", "enabled": True, "base_url": "https://custom.example.com/v1"}
    merged = _merge_provider_config(
        existing, api_key=None, enabled=True, base_url="https://api.minimax.chat/v1"
    )
    assert merged["base_url"] == "https://api.minimax.chat/v1"


def test_api_key_none_keeps_existing():
    existing = {"api_key": "secret", "enabled": True}
    merged = _merge_provider_config(existing, api_key=None, enabled=True, base_url=None)
    assert merged["api_key"] == "secret"


def test_api_key_none_on_missing_initializes_empty():
    existing = {"enabled": True}
    merged = _merge_provider_config(existing, api_key=None, enabled=True, base_url=None)
    assert merged["api_key"] == ""


def test_api_key_value_sets():
    existing = {"api_key": "old", "enabled": True}
    merged = _merge_provider_config(existing, api_key="new", enabled=True, base_url=None)
    assert merged["api_key"] == "new"


def test_enabled_toggle_preserves_other_fields():
    existing = {"api_key": "secret", "enabled": True, "base_url": "https://api.minimax.chat/v1"}
    merged = _merge_provider_config(existing, api_key=None, enabled=False, base_url=None)
    assert merged["enabled"] is False
    assert merged["api_key"] == "secret"
    assert merged["base_url"] == "https://api.minimax.chat/v1"


def test_does_not_mutate_input():
    existing = {"api_key": "secret", "enabled": True, "base_url": "https://api.minimax.chat/v1"}
    snapshot = dict(existing)
    _merge_provider_config(existing, api_key="new", enabled=False, base_url="")
    assert existing == snapshot
