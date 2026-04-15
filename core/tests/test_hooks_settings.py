"""Tests für hook_settings Loader (#654)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from hydrahive_core.hook_settings import (
    ALLOWED_EVENTS,
    DEFAULT_TIMEOUT_SECONDS,
    HookSettings,
    SettingsValidationError,
    load_hook_settings,
)


def _write(tmp_path: Path, payload: dict | list | str) -> Path:
    p = tmp_path / "settings.json"
    if isinstance(payload, str):
        p.write_text(payload, encoding="utf-8")
    else:
        p.write_text(json.dumps(payload), encoding="utf-8")
    return p


# ── 1. Fehlende Datei ────────────────────────────────────────────────────────
def test_missing_file_returns_empty(tmp_path: Path) -> None:
    result = load_hook_settings(tmp_path / "does-not-exist.json")
    assert isinstance(result, HookSettings)
    assert result.is_empty
    assert result.hooks == {}


# ── 2. Nicht-JSON ────────────────────────────────────────────────────────────
def test_invalid_json_raises(tmp_path: Path) -> None:
    p = _write(tmp_path, "{not valid json")
    with pytest.raises(SettingsValidationError, match="invalid JSON"):
        load_hook_settings(p)


# ── 3. Top-Level Array ───────────────────────────────────────────────────────
def test_toplevel_not_object(tmp_path: Path) -> None:
    p = _write(tmp_path, [1, 2, 3])
    with pytest.raises(SettingsValidationError, match="top-level must be object"):
        load_hook_settings(p)


# ── 4. Minimal gültig ────────────────────────────────────────────────────────
def test_valid_minimal(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash|Write",
                        "hooks": [{"type": "command", "command": "/bin/true"}],
                    }
                ]
            }
        },
    )
    result = load_hook_settings(p)
    assert set(result.hooks.keys()) == {"PreToolUse"}
    matchers = result.hooks["PreToolUse"]
    assert len(matchers) == 1
    assert matchers[0].matcher == "Bash|Write"
    assert len(matchers[0].hooks) == 1
    h = matchers[0].hooks[0]
    assert h.type == "command"
    assert h.command == "/bin/true"
    assert h.timeout == DEFAULT_TIMEOUT_SECONDS
    assert h.disabled is False


# ── 5. Unbekanntes Event ─────────────────────────────────────────────────────
def test_unknown_event_rejected(tmp_path: Path) -> None:
    p = _write(tmp_path, {"hooks": {"FooBar": []}})
    with pytest.raises(SettingsValidationError, match="unknown event 'FooBar'"):
        load_hook_settings(p)


# ── 5b. Falsche Schreibweise (kein lowercase-Mapping) ────────────────────────
@pytest.mark.parametrize("bad", ["pretooluse", "pre_tool_use", "PRETOOLUSE", "preToolUse"])
def test_case_sensitive_event_names(tmp_path: Path, bad: str) -> None:
    p = _write(tmp_path, {"hooks": {bad: []}})
    with pytest.raises(SettingsValidationError, match=f"unknown event '{bad}'"):
        load_hook_settings(p)


# ── 6. Nicht erlaubter Hook-Typ ──────────────────────────────────────────────
def test_unknown_hook_type(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        {
            "hooks": {
                "PreToolUse": [
                    {"hooks": [{"type": "http", "command": "ignored"}]}
                ]
            }
        },
    )
    with pytest.raises(SettingsValidationError, match="type 'http' not in allowed"):
        load_hook_settings(p)


# ── 7. command fehlt / leer ──────────────────────────────────────────────────
def test_command_missing(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        {"hooks": {"PreToolUse": [{"hooks": [{"type": "command"}]}]}},
    )
    with pytest.raises(SettingsValidationError, match="command.*missing required field"):
        load_hook_settings(p)


def test_command_empty(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        {"hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "   "}]}]}},
    )
    with pytest.raises(SettingsValidationError, match="non-empty"):
        load_hook_settings(p)


# ── 8. Negativer Timeout ─────────────────────────────────────────────────────
def test_negative_timeout(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        {
            "hooks": {
                "PreToolUse": [
                    {"hooks": [{"type": "command", "command": "x", "timeout": -1}]}
                ]
            }
        },
    )
    with pytest.raises(SettingsValidationError, match="timeout.*> 0"):
        load_hook_settings(p)


def test_bool_not_accepted_as_timeout(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        {
            "hooks": {
                "PreToolUse": [
                    {"hooks": [{"type": "command", "command": "x", "timeout": True}]}
                ]
            }
        },
    )
    with pytest.raises(SettingsValidationError, match="timeout.*must be int"):
        load_hook_settings(p)


# ── 9. Ungültige Matcher-Regex ───────────────────────────────────────────────
def test_invalid_matcher_regex(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "[unclosed",
                        "hooks": [{"type": "command", "command": "x"}],
                    }
                ]
            }
        },
    )
    with pytest.raises(SettingsValidationError, match="invalid regex"):
        load_hook_settings(p)


# ── 10. Unbekanntes Feld im Hook (strict) ────────────────────────────────────
def test_unknown_hook_field_rejected(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "hooks": [
                            {"type": "command", "command": "x", "extra": "nope"}
                        ]
                    }
                ]
            }
        },
    )
    with pytest.raises(SettingsValidationError, match="unknown fields.*extra"):
        load_hook_settings(p)


def test_unknown_matcher_field_rejected(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": ".*",
                        "hooks": [{"type": "command", "command": "x"}],
                        "extra": "nope",
                    }
                ]
            }
        },
    )
    with pytest.raises(SettingsValidationError, match="unknown fields.*extra"):
        load_hook_settings(p)


# ── 11. disabled wird geladen, nicht gefiltert ───────────────────────────────
def test_disabled_hook_loaded(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        {
            "hooks": {
                "PostToolUse": [
                    {
                        "hooks": [
                            {"type": "command", "command": "x", "disabled": True},
                            {"type": "command", "command": "y"},
                        ]
                    }
                ]
            }
        },
    )
    result = load_hook_settings(p)
    hooks = result.hooks["PostToolUse"][0].hooks
    assert len(hooks) == 2
    assert hooks[0].disabled is True
    assert hooks[1].disabled is False


# ── 12. Mehrere Events + Matcher + Hooks ─────────────────────────────────────
def test_multiple_events_and_matchers(tmp_path: Path) -> None:
    p = _write(
        tmp_path,
        {
            "hooks": {
                "PreToolUse": [
                    {
                        "matcher": "Bash",
                        "hooks": [{"type": "command", "command": "/a"}],
                    },
                    {
                        "matcher": "Write",
                        "hooks": [
                            {"type": "command", "command": "/b", "timeout": 10},
                            {"type": "command", "command": "/c"},
                        ],
                    },
                ],
                "OnTaskDone": [
                    {"hooks": [{"type": "command", "command": "/d"}]}
                ],
            }
        },
    )
    result = load_hook_settings(p)
    assert set(result.hooks.keys()) == {"PreToolUse", "OnTaskDone"}
    pre = result.hooks["PreToolUse"]
    assert len(pre) == 2
    assert pre[1].hooks[0].timeout == 10
    assert result.hooks["OnTaskDone"][0].matcher is None


# ── 13. Env-Override ─────────────────────────────────────────────────────────
def test_env_override(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    p = _write(tmp_path, {"hooks": {"OnTaskStart": [
        {"hooks": [{"type": "command", "command": "/x"}]}
    ]}})
    monkeypatch.setenv("HYDRAHIVE_SETTINGS_FILE", str(p))
    result = load_hook_settings()
    assert "OnTaskStart" in result.hooks


def test_explicit_path_beats_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    env_path = tmp_path / "env.json"
    env_path.write_text(json.dumps({"hooks": {"OnTaskStart": []}}), encoding="utf-8")
    monkeypatch.setenv("HYDRAHIVE_SETTINGS_FILE", str(env_path))
    explicit = tmp_path / "explicit.json"
    explicit.write_text(json.dumps({"hooks": {"PreToolUse": []}}), encoding="utf-8")
    result = load_hook_settings(explicit)
    assert set(result.hooks.keys()) == {"PreToolUse"}


# ── 14. hooks.<event> muss Array sein ────────────────────────────────────────
def test_event_value_must_be_array(tmp_path: Path) -> None:
    p = _write(tmp_path, {"hooks": {"PreToolUse": "oops"}})
    with pytest.raises(SettingsValidationError, match="must be array"):
        load_hook_settings(p)


# ── 15. hooks-Section optional ───────────────────────────────────────────────
def test_no_hooks_section(tmp_path: Path) -> None:
    p = _write(tmp_path, {"other": "stuff"})
    result = load_hook_settings(p)
    assert result.is_empty


# ── 16. Erlaubte Events sind vollständig ─────────────────────────────────────
def test_allowed_events_exact() -> None:
    assert ALLOWED_EVENTS == frozenset(
        {"PreToolUse", "PostToolUse", "OnTaskStart", "OnTaskDone"}
    )
