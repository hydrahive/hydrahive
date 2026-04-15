"""
hook_runtime.py — Runtime für PreToolUse/PostToolUse command-Hooks (#655)

Baut auf hook_settings.py (#654) auf. Führt deklarierte command-Hooks
aus und liefert eine Policy-Entscheidung (allow/warn/block).

Semantik
--------
PreToolUse kann blockieren. PostToolUse blockiert NICHT rückwirkend —
Fehler, Timeouts oder block-Requests werden nur geloggt/als Warnung
durchgereicht.

Isolation
---------
Hooks laufen als asyncio-Subprocess mit shlex.split(cmd) — kein
shell=True. Shell-Features (&&, |, $()) erfordern explizites
`bash -c "..."` im Command.

Sicherheitsmodell
-----------------
Command-Hooks laufen als aktueller Service-User. Sie MÜSSEN als
vertrauenswürdige lokale Admin-Konfiguration behandelt werden
(settings.json ist root-owned unter /etc/hydrahive/). HOME und PATH
werden bewusst durchgereicht, damit Standard-Tools wie `jq`, `curl`,
Python-Wrapper ohne Absolutpfade funktionieren. V1 hat KEINE
Privilege-Isolation (kein Sandboxing, kein seccomp, kein User-Drop).

mtime-Cache
-----------
settings.json wird pro Aufruf via stat() geprüft. Bei mtime-Änderung
wird automatisch neu geladen — kein Prozessneustart nötig.
reload_hook_runtime() setzt den Cache explizit zurück (für Tests).
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .hook_settings import (
    HookDefinition,
    HookMatcher,
    HookSettings,
    load_hook_settings,
)

logger = logging.getLogger(__name__)

MAX_STRING_LEN = 2048
MAX_PAYLOAD_BYTES = 64 * 1024

# Secret-Pattern wie in scripts/scan-secrets.sh (#657), aber als Python-Regex.
# Format: [REDACTED:<kind>:<last4>] — Pfade/Zeilen bleiben lesbar.
_REDACT_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(gh[opsur]_[A-Za-z0-9]{28,})([A-Za-z0-9]{4})"), r"[REDACTED:gh_token:\2]"),
    (re.compile(r"(github_pat_[A-Za-z0-9_]{74,})([A-Za-z0-9_]{4})"), r"[REDACTED:gh_pat:\2]"),
    (re.compile(r"(sk-ant-[A-Za-z0-9_-]{16,})([A-Za-z0-9_-]{4})"), r"[REDACTED:sk-ant:\2]"),
    # OpenAI API-Keys (sk-live/sk-test/sk-proj, Bindestrich) und
    # Stripe-Keys (sk_live/sk_test, Unterstrich) — beide Separatoren abgedeckt.
    (re.compile(r"(sk[_-](?:live|test|proj)[_-][A-Za-z0-9_-]{16,})([A-Za-z0-9_-]{4})"), r"[REDACTED:sk_key:\2]"),
    (re.compile(r"(AKIA[0-9A-Z]{12})([0-9A-Z]{4})"), r"[REDACTED:AKIA:\2]"),
    (re.compile(r"(ASIA[0-9A-Z]{12})([0-9A-Z]{4})"), r"[REDACTED:ASIA:\2]"),
    (re.compile(r"(xox[baprs]-[A-Za-z0-9-]{10,})([A-Za-z0-9]{4})"), r"[REDACTED:xox:\2]"),
    (re.compile(r"(AIza[0-9A-Za-z_-]{31})([0-9A-Za-z_-]{4})"), r"[REDACTED:AIza:\2]"),
    (re.compile(r"(ya29\.[0-9A-Za-z_-]{16,})([0-9A-Za-z_-]{4})"), r"[REDACTED:ya29:\2]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_=.-]*"), "[REDACTED:JWT]"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "[REDACTED:KEY]"),
)


def _redact_str(s: str) -> str:
    for pat, repl in _REDACT_PATTERNS:
        s = pat.sub(repl, s)
    if len(s) > MAX_STRING_LEN:
        s = s[:MAX_STRING_LEN] + "…[truncated]"
    return s


def _redact_value(v: Any) -> Any:
    if isinstance(v, str):
        return _redact_str(v)
    if isinstance(v, list):
        return [_redact_value(x) for x in v]
    if isinstance(v, dict):
        return {k: _redact_value(val) for k, val in v.items()}
    return v


# ── Settings mit mtime-Cache ──────────────────────────────────────────────────

_cache: dict[str, tuple[float, HookSettings]] = {}


def _resolved_settings_path() -> Path:
    env = os.environ.get("HYDRAHIVE_SETTINGS_FILE")
    return Path(env) if env else Path("/etc/hydrahive/settings.json")


def get_hook_settings() -> HookSettings:
    """
    Liefert aktuell gültige HookSettings. Prüft mtime pro Aufruf —
    bei Änderung wird transparent neu geladen, kein Prozessneustart nötig.
    Fehlende Datei → leeres HookSettings(), kein Fehler.
    """
    path = _resolved_settings_path()
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:
        _cache.pop(str(path), None)
        return HookSettings()
    except OSError as exc:
        logger.warning("settings.json stat failed (%s): %s", path, exc)
        return HookSettings()

    cached = _cache.get(str(path))
    if cached is not None and cached[0] == mtime:
        return cached[1]

    settings = load_hook_settings(path)
    _cache[str(path)] = (mtime, settings)
    return settings


def reload_hook_runtime() -> None:
    """Cache-Reset — nur für Tests / manuelle Invalidierung."""
    _cache.clear()


# ── Ergebnis-Typen ────────────────────────────────────────────────────────────

@dataclass
class PreHookDecision:
    action: str  # "allow" | "block"
    message: str | None = None
    warnings: list[str] = field(default_factory=list)


@dataclass
class PostHookReport:
    warnings: list[str] = field(default_factory=list)


# ── Hook-Ausführung ───────────────────────────────────────────────────────────

def _minimal_env(event: str, tool_name: str) -> dict[str, str]:
    """Bewusst kleines Env: HOME + PATH durchgereicht, Event/Tool als Marker.

    Siehe Modul-Docstring: V1 hat keine Privilege-Isolation. Hooks müssen
    als vertrauenswürdige Admin-Config behandelt werden.
    """
    return {
        "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/tmp"),
        "HYDRAHIVE_HOOK_EVENT": event,
        "HYDRAHIVE_HOOK_TOOL": tool_name,
    }


async def _run_one_hook(
    hook: HookDefinition,
    payload: dict,
    event: str,
    tool_name: str,
) -> tuple[str, int | None, str, str]:
    """
    Führt einen command-Hook aus.
    Returns (outcome, exit_code, stdout, stderr) wobei outcome in:
      "ok"        → Prozess normal beendet
      "timeout"   → Timeout, Prozess gekillt
      "exec_fail" → konnte Prozess nicht starten
    Alle stdout/stderr sind bereits redacted.
    """
    try:
        argv = shlex.split(hook.command)
    except ValueError as exc:
        return ("exec_fail", None, "", f"invalid command syntax: {exc}")
    if not argv:
        return ("exec_fail", None, "", "empty command after shlex.split")

    payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    if len(payload_bytes) > MAX_PAYLOAD_BYTES:
        payload_bytes = payload_bytes[:MAX_PAYLOAD_BYTES]

    try:
        proc = await asyncio.create_subprocess_exec(
            *argv,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=_minimal_env(event, tool_name),
        )
    except (OSError, FileNotFoundError) as exc:
        return ("exec_fail", None, "", f"could not spawn: {exc}")

    try:
        stdout_b, stderr_b = await asyncio.wait_for(
            proc.communicate(input=payload_bytes),
            timeout=hook.timeout,
        )
    except asyncio.TimeoutError:
        try:
            proc.kill()
        except ProcessLookupError:
            pass
        try:
            await proc.wait()
        except Exception:
            pass
        return ("timeout", None, "", "")

    stdout = _redact_str(stdout_b.decode("utf-8", errors="replace"))
    stderr = _redact_str(stderr_b.decode("utf-8", errors="replace"))
    return ("ok", proc.returncode, stdout, stderr)


def _parse_hook_output(stdout: str) -> tuple[str | None, str]:
    """
    Parst Hook-stdout. Returns (action, message).
      action: "allow" | "warn" | "block" | None (= leer)
      message: Freitext
    Raises ValueError bei nicht-leerer, unparsbarer Ausgabe.
    """
    text = stdout.strip()
    if not text:
        return (None, "")
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON output: {exc.msg}") from exc
    if not isinstance(data, dict):
        raise ValueError(f"output must be object, got {type(data).__name__}")
    action = data.get("action")
    if action is not None and action not in {"allow", "warn", "block"}:
        raise ValueError(f"unknown action '{action}' (allowed: allow|warn|block)")
    message = data.get("message", "")
    if not isinstance(message, str):
        message = str(message)
    return (action, message)


def _matches(matcher: HookMatcher, tool_name: str) -> bool:
    if matcher.matcher is None:
        return True
    try:
        return re.fullmatch(matcher.matcher, tool_name) is not None
    except re.error:
        # Sollte durch hook_settings schon validiert sein; defensiv False.
        return False


def _collect_hooks(settings: HookSettings, event: str, tool_name: str) -> list[HookDefinition]:
    matchers = settings.hooks.get(event, ())
    out: list[HookDefinition] = []
    for m in matchers:
        if not _matches(m, tool_name):
            continue
        for h in m.hooks:
            if h.disabled:
                logger.debug("hook %s disabled, skipping (event=%s tool=%s)", h.command, event, tool_name)
                continue
            out.append(h)
    return out


def _build_payload(event: str, tool_name: str, tool_input: dict, context: dict | None) -> dict:
    ctx = dict(context or {})
    # Context-Strings ebenfalls redacted (user/agent/project-IDs sind typ.
    # harmlos, aber konsistent).
    ctx_redacted = {k: _redact_value(v) for k, v in ctx.items()}
    return {
        "event": event,
        "tool_name": tool_name,
        "tool_input": _redact_value(tool_input),
        "context": ctx_redacted,
    }


async def run_pretool_hooks(
    tool_name: str,
    tool_input: dict,
    context: dict | None = None,
) -> PreHookDecision:
    """
    Führt konfigurierte PreToolUse-Hooks für `tool_name` der Reihe nach aus.
    Erster Block gewinnt (fail-fast). Fehlende settings.json → allow (no-op).
    """
    settings = get_hook_settings()
    if settings.is_empty:
        return PreHookDecision(action="allow")

    hooks = _collect_hooks(settings, "PreToolUse", tool_name)
    if not hooks:
        return PreHookDecision(action="allow")

    payload = _build_payload("PreToolUse", tool_name, tool_input, context)
    warnings: list[str] = []

    for hook in hooks:
        outcome, rc, stdout, stderr = await _run_one_hook(hook, payload, "PreToolUse", tool_name)
        cmd_short = shlex.split(hook.command)[0] if hook.command else "<empty>"

        if outcome == "timeout":
            msg = f"PreToolUse hook timeout ({hook.timeout}s): {cmd_short}"
            logger.warning(msg)
            return PreHookDecision(action="block", message=msg, warnings=warnings)

        if outcome == "exec_fail":
            msg = f"PreToolUse hook failed to execute: {cmd_short}: {stderr.strip()}"
            logger.warning(msg)
            return PreHookDecision(action="block", message=msg, warnings=warnings)

        if rc != 0:
            msg = f"PreToolUse hook exit {rc}: {cmd_short}"
            if stderr.strip():
                msg += f" | stderr: {stderr.strip()[:200]}"
            logger.warning(msg)
            return PreHookDecision(action="block", message=msg, warnings=warnings)

        try:
            action, message = _parse_hook_output(stdout)
        except ValueError as exc:
            msg = f"PreToolUse hook invalid output: {cmd_short}: {exc}"
            logger.warning(msg)
            return PreHookDecision(action="block", message=msg, warnings=warnings)

        if action == "block":
            logger.info("PreToolUse block: tool=%s hook=%s msg=%s", tool_name, cmd_short, message)
            return PreHookDecision(
                action="block",
                message=message or f"blocked by {cmd_short}",
                warnings=warnings,
            )
        if action == "warn":
            w = message or f"warning from {cmd_short}"
            logger.info("PreToolUse warn: tool=%s %s", tool_name, w)
            warnings.append(w)
        # allow / None → weiter

    return PreHookDecision(action="allow", warnings=warnings)


async def run_posttool_hooks(
    tool_name: str,
    tool_input: dict,
    result: Any,
    is_error: bool,
    context: dict | None = None,
) -> PostHookReport:
    """
    Führt PostToolUse-Hooks aus. Blockiert NIE — Fehler/Timeouts/Block-
    Requests werden als Warnings geloggt und gesammelt.
    """
    settings = get_hook_settings()
    if settings.is_empty:
        return PostHookReport()

    hooks = _collect_hooks(settings, "PostToolUse", tool_name)
    if not hooks:
        return PostHookReport()

    # Result-Größe begrenzen + redacten
    try:
        result_preview = _redact_value(result if isinstance(result, (dict, list, str)) else str(result))
    except Exception:
        result_preview = "<unrepresentable>"

    payload = _build_payload("PostToolUse", tool_name, tool_input, context)
    payload["result"] = result_preview
    payload["is_error"] = bool(is_error)
    warnings: list[str] = []

    for hook in hooks:
        outcome, rc, stdout, stderr = await _run_one_hook(hook, payload, "PostToolUse", tool_name)
        cmd_short = shlex.split(hook.command)[0] if hook.command else "<empty>"

        if outcome == "timeout":
            w = f"PostToolUse hook timeout ({hook.timeout}s): {cmd_short}"
            logger.warning(w)
            warnings.append(w)
            continue

        if outcome == "exec_fail":
            w = f"PostToolUse hook exec failed: {cmd_short}: {stderr.strip()}"
            logger.warning(w)
            warnings.append(w)
            continue

        if rc != 0:
            w = f"PostToolUse hook exit {rc}: {cmd_short}"
            if stderr.strip():
                w += f" | stderr: {stderr.strip()[:200]}"
            logger.warning(w)
            warnings.append(w)
            continue

        try:
            action, message = _parse_hook_output(stdout)
        except ValueError as exc:
            w = f"PostToolUse hook invalid output: {cmd_short}: {exc}"
            logger.warning(w)
            warnings.append(w)
            continue

        if action in ("block", "warn"):
            w = message or f"PostToolUse {action} from {cmd_short}"
            logger.info("PostToolUse %s: tool=%s %s", action, tool_name, w)
            warnings.append(w)

    return PostHookReport(warnings=warnings)
