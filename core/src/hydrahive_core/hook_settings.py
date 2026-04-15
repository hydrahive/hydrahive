"""
hook_settings.py — Loader für globale Hook-Konfiguration aus settings.json (#654)

Dieses Modul ist reiner Parser und Validator. Es führt KEINE Hooks aus.
Laufzeit-Integration für PreToolUse/PostToolUse (#655) und OnTaskStart/OnTaskDone
(#656) baut später auf diesem Loader auf.

Abgrenzung zu hooks.py (#472): jenes System verarbeitet Per-Agent-Hooks aus
agent.yaml. Dieses Modul deckt globale Hook-Konfiguration auf Host-Ebene ab.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

ALLOWED_EVENTS: frozenset[str] = frozenset(
    {"PreToolUse", "PostToolUse", "OnTaskStart", "OnTaskDone"}
)
ALLOWED_HOOK_TYPES: frozenset[str] = frozenset({"command"})
ALLOWED_HOOK_FIELDS: frozenset[str] = frozenset(
    {"type", "command", "timeout", "disabled", "description"}
)
ALLOWED_MATCHER_FIELDS: frozenset[str] = frozenset({"matcher", "hooks"})
DEFAULT_TIMEOUT_SECONDS = 60


class SettingsValidationError(Exception):
    """settings.json ist strukturell oder semantisch ungültig."""


@dataclass(frozen=True)
class HookDefinition:
    type: str
    command: str
    timeout: int = DEFAULT_TIMEOUT_SECONDS
    disabled: bool = False
    description: str | None = None


@dataclass(frozen=True)
class HookMatcher:
    matcher: str | None
    hooks: tuple[HookDefinition, ...]


@dataclass(frozen=True)
class HookSettings:
    hooks: dict[str, tuple[HookMatcher, ...]] = field(default_factory=dict)

    @property
    def is_empty(self) -> bool:
        return not self.hooks


def _fail(path: str, msg: str) -> None:
    raise SettingsValidationError(f"{path}: {msg}")


def _require_type(value: Any, expected: type, path: str, kind: str) -> None:
    if not isinstance(value, expected):
        _fail(path, f"{kind} must be {expected.__name__}, got {type(value).__name__}")


def _validate_hook(obj: Any, path: str) -> HookDefinition:
    if not isinstance(obj, dict):
        _fail(path, f"hook entry must be object, got {type(obj).__name__}")

    unknown = set(obj.keys()) - ALLOWED_HOOK_FIELDS
    if unknown:
        _fail(path, f"unknown fields: {sorted(unknown)}. allowed: {sorted(ALLOWED_HOOK_FIELDS)}")

    if "type" not in obj:
        _fail(path, "missing required field 'type'")
    htype = obj["type"]
    if htype not in ALLOWED_HOOK_TYPES:
        _fail(path, f"type '{htype}' not in allowed {sorted(ALLOWED_HOOK_TYPES)}")

    if "command" not in obj:
        _fail(f"{path}.command", "missing required field")
    cmd = obj["command"]
    _require_type(cmd, str, f"{path}.command", "command")
    if not cmd.strip():
        _fail(f"{path}.command", "must be non-empty string")

    timeout = obj.get("timeout", DEFAULT_TIMEOUT_SECONDS)
    if not isinstance(timeout, int) or isinstance(timeout, bool):
        _fail(f"{path}.timeout", f"must be int, got {type(timeout).__name__}")
    if timeout <= 0:
        _fail(f"{path}.timeout", f"must be > 0, got {timeout}")

    disabled = obj.get("disabled", False)
    if not isinstance(disabled, bool):
        _fail(f"{path}.disabled", f"must be bool, got {type(disabled).__name__}")

    description = obj.get("description")
    if description is not None:
        _require_type(description, str, f"{path}.description", "description")

    return HookDefinition(
        type=htype,
        command=cmd.strip(),
        timeout=timeout,
        disabled=disabled,
        description=description,
    )


def _validate_matcher(obj: Any, path: str) -> HookMatcher:
    if not isinstance(obj, dict):
        _fail(path, f"matcher entry must be object, got {type(obj).__name__}")

    unknown = set(obj.keys()) - ALLOWED_MATCHER_FIELDS
    if unknown:
        _fail(path, f"unknown fields: {sorted(unknown)}. allowed: {sorted(ALLOWED_MATCHER_FIELDS)}")

    matcher = obj.get("matcher")
    if matcher is not None:
        _require_type(matcher, str, f"{path}.matcher", "matcher")
        try:
            re.compile(matcher)
        except re.error as exc:
            _fail(f"{path}.matcher", f"invalid regex: {exc}")

    if "hooks" not in obj:
        _fail(f"{path}.hooks", "missing required field")
    hooks_raw = obj["hooks"]
    if not isinstance(hooks_raw, list):
        _fail(f"{path}.hooks", f"must be array, got {type(hooks_raw).__name__}")
    if not hooks_raw:
        _fail(f"{path}.hooks", "must contain at least one hook")

    hooks = tuple(
        _validate_hook(h, f"{path}.hooks[{i}]") for i, h in enumerate(hooks_raw)
    )
    return HookMatcher(matcher=matcher, hooks=hooks)


def _validate_event_matchers(event: str, value: Any) -> tuple[HookMatcher, ...]:
    path = f"hooks.{event}"
    if not isinstance(value, list):
        _fail(path, f"must be array, got {type(value).__name__}")
    return tuple(
        _validate_matcher(m, f"{path}[{i}]") for i, m in enumerate(value)
    )


def _resolve_settings_path(explicit: Path | None) -> Path:
    if explicit is not None:
        return Path(explicit)
    env = os.environ.get("HYDRAHIVE_SETTINGS_FILE")
    if env:
        return Path(env)
    return Path("/etc/hydrahive/settings.json")


def load_hook_settings(path: Path | None = None) -> HookSettings:
    """
    Lädt und validiert settings.json. Fehlende Datei ist kein Fehler (leeres Resultat).

    Pfad-Auflösung:
      1. expliziter `path`-Parameter
      2. env HYDRAHIVE_SETTINGS_FILE
      3. /etc/hydrahive/settings.json

    Strenge Validierung: unbekannte Felder, unbekannte Events oder Hook-Typen
    führen zu SettingsValidationError. Deaktivierte Hooks werden geladen
    (disabled=True) und nicht gefiltert — Policy liegt bei der Runtime.
    """
    resolved = _resolve_settings_path(path)
    if not resolved.exists():
        return HookSettings()

    try:
        raw_text = resolved.read_text(encoding="utf-8")
    except OSError as exc:
        raise SettingsValidationError(f"{resolved}: cannot read file: {exc}") from exc

    try:
        data = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        raise SettingsValidationError(
            f"{resolved}: invalid JSON at line {exc.lineno} col {exc.colno}: {exc.msg}"
        ) from exc

    if not isinstance(data, dict):
        raise SettingsValidationError(
            f"{resolved}: top-level must be object, got {type(data).__name__}"
        )

    hooks_section = data.get("hooks")
    if hooks_section is None:
        return HookSettings()
    if not isinstance(hooks_section, dict):
        raise SettingsValidationError(
            f"{resolved}:hooks must be object, got {type(hooks_section).__name__}"
        )

    normalized: dict[str, tuple[HookMatcher, ...]] = {}
    for event, value in hooks_section.items():
        if event not in ALLOWED_EVENTS:
            raise SettingsValidationError(
                f"{resolved}:hooks: unknown event '{event}'. "
                f"allowed: {sorted(ALLOWED_EVENTS)} (PascalCase, case-sensitive)"
            )
        normalized[event] = _validate_event_matchers(event, value)

    return HookSettings(hooks=normalized)
