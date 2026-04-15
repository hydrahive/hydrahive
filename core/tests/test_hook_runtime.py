"""Tests für hook_runtime (#655) — PreToolUse/PostToolUse Runtime."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hydrahive_core.hook_runtime import (
    PostHookReport,
    PreHookDecision,
    _redact_str,
    get_hook_settings,
    reload_hook_runtime,
    run_posttool_hooks,
    run_pretool_hooks,
)


# ── Helpers ──────────────────────────────────────────────────────────────────

def write_hook_script(dir: Path, name: str, body: str) -> Path:
    p = dir / name
    p.write_text(body, encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


def write_settings(dir: Path, hooks_cfg: dict) -> Path:
    p = dir / "settings.json"
    p.write_text(json.dumps({"hooks": hooks_cfg}), encoding="utf-8")
    return p


@pytest.fixture(autouse=True)
def _reset_cache():
    reload_hook_runtime()
    yield
    reload_hook_runtime()


@pytest.fixture
def configure(monkeypatch, tmp_path):
    def _conf(hooks_cfg: dict) -> Path:
        settings = write_settings(tmp_path, hooks_cfg)
        monkeypatch.setenv("HYDRAHIVE_SETTINGS_FILE", str(settings))
        reload_hook_runtime()
        return settings
    return _conf


# ── 1. Keine settings.json → allow (no-op) ────────────────────────────────────
async def test_no_settings_file_is_noop(monkeypatch, tmp_path):
    monkeypatch.setenv("HYDRAHIVE_SETTINGS_FILE", str(tmp_path / "nope.json"))
    reload_hook_runtime()
    decision = await run_pretool_hooks("Bash", {"cmd": "ls"})
    assert isinstance(decision, PreHookDecision)
    assert decision.action == "allow"
    assert decision.warnings == []
    report = await run_posttool_hooks("Bash", {}, result="ok", is_error=False)
    assert isinstance(report, PostHookReport)
    assert report.warnings == []


# ── 2. allow-Output ──────────────────────────────────────────────────────────
async def test_allow_output(tmp_path, configure):
    h = write_hook_script(tmp_path, "allow.sh",
                          "#!/usr/bin/env bash\necho '{\"action\":\"allow\"}'\n")
    configure({"PreToolUse": [{"hooks": [{"type": "command", "command": str(h)}]}]})
    d = await run_pretool_hooks("Bash", {})
    assert d.action == "allow"
    assert d.warnings == []


# ── 3. warn-Output ───────────────────────────────────────────────────────────
async def test_warn_output(tmp_path, configure):
    h = write_hook_script(tmp_path, "warn.sh",
                          "#!/usr/bin/env bash\necho '{\"action\":\"warn\",\"message\":\"careful\"}'\n")
    configure({"PreToolUse": [{"hooks": [{"type": "command", "command": str(h)}]}]})
    d = await run_pretool_hooks("Bash", {})
    assert d.action == "allow"
    assert "careful" in d.warnings


# ── 4. block PreToolUse ──────────────────────────────────────────────────────
async def test_block_pretool(tmp_path, configure):
    h = write_hook_script(tmp_path, "block.sh",
                          "#!/usr/bin/env bash\necho '{\"action\":\"block\",\"message\":\"nope\"}'\n")
    configure({"PreToolUse": [{"hooks": [{"type": "command", "command": str(h)}]}]})
    d = await run_pretool_hooks("Bash", {})
    assert d.action == "block"
    assert d.message == "nope"


# ── 5. block PostToolUse → nur warn, kein rückwirkender Block ────────────────
async def test_block_posttool_is_warn(tmp_path, configure):
    h = write_hook_script(tmp_path, "block.sh",
                          "#!/usr/bin/env bash\necho '{\"action\":\"block\",\"message\":\"late\"}'\n")
    configure({"PostToolUse": [{"hooks": [{"type": "command", "command": str(h)}]}]})
    report = await run_posttool_hooks("Bash", {}, result="ok", is_error=False)
    assert any("late" in w for w in report.warnings)


# ── 6. Exit != 0 ─────────────────────────────────────────────────────────────
async def test_exit_nonzero(tmp_path, configure):
    h = write_hook_script(tmp_path, "fail.sh",
                          "#!/usr/bin/env bash\necho 'something went wrong' >&2\nexit 7\n")
    configure({"PreToolUse": [{"hooks": [{"type": "command", "command": str(h)}]}]})
    d = await run_pretool_hooks("Bash", {})
    assert d.action == "block"
    assert "exit 7" in (d.message or "")

    configure({"PostToolUse": [{"hooks": [{"type": "command", "command": str(h)}]}]})
    report = await run_posttool_hooks("Bash", {}, result="ok", is_error=False)
    assert any("exit 7" in w for w in report.warnings)


# ── 7. Timeout ───────────────────────────────────────────────────────────────
async def test_timeout_pretool(tmp_path, configure):
    h = write_hook_script(tmp_path, "slow.sh",
                          "#!/usr/bin/env bash\nsleep 5\n")
    configure({"PreToolUse": [{"hooks": [{"type": "command", "command": str(h), "timeout": 1}]}]})
    d = await run_pretool_hooks("Bash", {})
    assert d.action == "block"
    assert "timeout" in (d.message or "").lower()


async def test_timeout_posttool(tmp_path, configure):
    h = write_hook_script(tmp_path, "slow.sh",
                          "#!/usr/bin/env bash\nsleep 5\n")
    configure({"PostToolUse": [{"hooks": [{"type": "command", "command": str(h), "timeout": 1}]}]})
    report = await run_posttool_hooks("Bash", {}, result="ok", is_error=False)
    assert any("timeout" in w.lower() for w in report.warnings)


# ── 8. Unparsbares JSON ──────────────────────────────────────────────────────
async def test_invalid_json_pretool_blocks(tmp_path, configure):
    h = write_hook_script(tmp_path, "junk.sh",
                          "#!/usr/bin/env bash\necho 'not json at all'\n")
    configure({"PreToolUse": [{"hooks": [{"type": "command", "command": str(h)}]}]})
    d = await run_pretool_hooks("Bash", {})
    assert d.action == "block"
    assert "invalid" in (d.message or "").lower()


async def test_invalid_json_posttool_warns(tmp_path, configure):
    h = write_hook_script(tmp_path, "junk.sh",
                          "#!/usr/bin/env bash\necho 'not json at all'\n")
    configure({"PostToolUse": [{"hooks": [{"type": "command", "command": str(h)}]}]})
    report = await run_posttool_hooks("Bash", {}, result="ok", is_error=False)
    assert any("invalid" in w.lower() for w in report.warnings)


# ── 9. Disabled Hook übersprungen ────────────────────────────────────────────
async def test_disabled_skipped(tmp_path, configure):
    h = write_hook_script(tmp_path, "block.sh",
                          "#!/usr/bin/env bash\necho '{\"action\":\"block\"}'\n")
    configure({"PreToolUse": [{"hooks": [
        {"type": "command", "command": str(h), "disabled": True}
    ]}]})
    d = await run_pretool_hooks("Bash", {})
    assert d.action == "allow"


# ── 10. Matcher ──────────────────────────────────────────────────────────────
async def test_matcher_filters(tmp_path, configure):
    h = write_hook_script(tmp_path, "block.sh",
                          "#!/usr/bin/env bash\necho '{\"action\":\"block\"}'\n")
    configure({"PreToolUse": [{"matcher": "Bash",
                               "hooks": [{"type": "command", "command": str(h)}]}]})
    assert (await run_pretool_hooks("Write", {})).action == "allow"
    assert (await run_pretool_hooks("Bash", {})).action == "block"


# ── 11. Erster Block stoppt Kette ────────────────────────────────────────────
async def test_failfast_pretool(tmp_path, configure):
    canary = tmp_path / "second_ran"
    h1 = write_hook_script(tmp_path, "first.sh",
                           "#!/usr/bin/env bash\necho '{\"action\":\"block\",\"message\":\"stop\"}'\n")
    h2 = write_hook_script(
        tmp_path, "second.sh",
        f"#!/usr/bin/env bash\ntouch {canary}\necho '{{\"action\":\"allow\"}}'\n",
    )
    configure({"PreToolUse": [
        {"hooks": [{"type": "command", "command": str(h1)}]},
        {"hooks": [{"type": "command", "command": str(h2)}]},
    ]})
    d = await run_pretool_hooks("Bash", {})
    assert d.action == "block"
    assert not canary.exists(), "zweiter Hook darf nach Block nicht laufen"


async def test_posttool_all_run(tmp_path, configure):
    a = tmp_path / "a_ran"
    b = tmp_path / "b_ran"
    h1 = write_hook_script(
        tmp_path, "a.sh",
        f"#!/usr/bin/env bash\ntouch {a}\necho '{{\"action\":\"warn\",\"message\":\"w1\"}}'\n",
    )
    h2 = write_hook_script(
        tmp_path, "b.sh",
        f"#!/usr/bin/env bash\ntouch {b}\necho '{{\"action\":\"warn\",\"message\":\"w2\"}}'\n",
    )
    configure({"PostToolUse": [{"hooks": [
        {"type": "command", "command": str(h1)},
        {"type": "command", "command": str(h2)},
    ]}]})
    report = await run_posttool_hooks("Bash", {}, result="ok", is_error=False)
    assert a.exists() and b.exists()
    assert "w1" in report.warnings and "w2" in report.warnings


# ── 12. Redaction — Secrets werden vor stdin redacted ────────────────────────
async def test_redaction_before_stdin(tmp_path, configure):
    # Hook schreibt stdin in Datei — wir prüfen, dass Secret nicht als Klartext ankommt.
    dump = tmp_path / "stdin.json"
    h = write_hook_script(
        tmp_path, "echo.sh",
        f"#!/usr/bin/env bash\ncat > {dump}\necho '{{\"action\":\"allow\"}}'\n",
    )
    configure({"PreToolUse": [{"hooks": [{"type": "command", "command": str(h)}]}]})
    # realistisch aussehender Fake-PAT (40 chars total, 36 body)
    fake = "ghp_" + "A" * 32 + "WXYZ"
    d = await run_pretool_hooks("Bash", {"command": f"curl -H 'Auth: {fake}'"})
    assert d.action == "allow"
    content = dump.read_text(encoding="utf-8")
    assert "A" * 32 not in content, "Klartext-Token ist im Hook-stdin angekommen"
    assert "REDACTED:gh_token" in content


def test_redact_str_targeted_only():
    # zielfremde lange hex-Strings werden NICHT redacted
    out = _redact_str("deadbeef1234567890abcdef1234567890")
    assert "REDACTED" not in out


# ── 13. shlex.split — Argumente kommen an ────────────────────────────────────
async def test_command_with_arguments(tmp_path, configure):
    dump = tmp_path / "args.txt"
    h = write_hook_script(
        tmp_path, "dump_args.sh",
        f'#!/usr/bin/env bash\nprintf "%s\\n" "$@" > {dump}\necho \'{{"action":"allow"}}\'\n',
    )
    cmd = f"{h} --flag value with-spaces"
    configure({"PreToolUse": [{"hooks": [{"type": "command", "command": cmd}]}]})
    d = await run_pretool_hooks("Bash", {})
    assert d.action == "allow"
    lines = dump.read_text().strip().splitlines()
    assert lines == ["--flag", "value", "with-spaces"]


# ── 14. mtime-Cache reloadt bei Änderung ─────────────────────────────────────
async def test_mtime_cache_reloads(tmp_path, monkeypatch):
    h_allow = write_hook_script(tmp_path, "allow.sh",
                                "#!/usr/bin/env bash\necho '{\"action\":\"allow\"}'\n")
    h_block = write_hook_script(tmp_path, "block.sh",
                                "#!/usr/bin/env bash\necho '{\"action\":\"block\"}'\n")
    settings_path = tmp_path / "settings.json"
    settings_path.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"hooks": [{"type": "command", "command": str(h_allow)}]}
    ]}}), encoding="utf-8")
    monkeypatch.setenv("HYDRAHIVE_SETTINGS_FILE", str(settings_path))
    reload_hook_runtime()

    assert (await run_pretool_hooks("Bash", {})).action == "allow"

    # Datei überschreiben mit block-Hook und mtime anheben
    settings_path.write_text(json.dumps({"hooks": {"PreToolUse": [
        {"hooks": [{"type": "command", "command": str(h_block)}]}
    ]}}), encoding="utf-8")
    # mtime sicher in Zukunft setzen (falls fs Sekunden-Auflösung hat)
    new_mtime = settings_path.stat().st_mtime + 5
    os.utime(settings_path, (new_mtime, new_mtime))

    assert (await run_pretool_hooks("Bash", {})).action == "block"


# ── 15. Integration: execute_tool_call mit PreToolUse-Block ──────────────────
async def test_integration_pretool_blocks_execution(tmp_path, monkeypatch):
    h = write_hook_script(tmp_path, "block.sh",
                          "#!/usr/bin/env bash\necho '{\"action\":\"block\",\"message\":\"no-go\"}'\n")
    settings = write_settings(tmp_path, {"PreToolUse": [
        {"hooks": [{"type": "command", "command": str(h)}]}
    ]})
    monkeypatch.setenv("HYDRAHIVE_SETTINGS_FILE", str(settings))
    reload_hook_runtime()

    from hydrahive_core.orchestrator_tools import execute_tool_call

    # Mock orchestrator minimal
    orch = MagicMock()
    orch._execute_tool = AsyncMock()
    orch._resolve_allowed_tool = MagicMock(return_value=MagicMock(name="tool"))
    sess = MagicMock(); sess.id = "s1"
    orch._sessions = MagicMock()
    orch._sessions.get_active = MagicMock(return_value=sess)

    boss_cfg = MagicMock()
    boss_cfg.mcp_servers = None
    boss_cfg.id = "agent1"
    boss_cfg.risk_policy = "trusted"  # überspringt CONFIRM

    result, is_error = await execute_tool_call(
        orch,
        boss_cfg=boss_cfg,
        project_id="p1",
        tool_name="Bash",
        tool_input={"command": "ls"},
    )
    assert is_error is True
    assert result.get("risk") == "hook_block"
    assert "no-go" in (result.get("hint") or "")
    orch._execute_tool.assert_not_called()


# ── 16. Integration: PreToolUse-Runtime-Exception → fail-closed block ────────
async def test_integration_pretool_runtime_error_fails_closed(tmp_path, monkeypatch):
    monkeypatch.setenv("HYDRAHIVE_SETTINGS_FILE", str(tmp_path / "missing.json"))
    reload_hook_runtime()

    # Token zur Laufzeit konkateniert, damit der Secret-Scanner den
    # Quelltext nicht als 40-char PAT matcht. Redaction greift trotzdem,
    # da hook_runtime die zusammengesetzte Exception-Message sieht.
    _fake_body = ("SECRET" * 6) + "01AB"
    _fake_token = "ghp_" + _fake_body
    async def _boom(*a, **kw):
        raise RuntimeError(f"hook subsystem exploded {_fake_token}")
    monkeypatch.setattr("hydrahive_core.hook_runtime.run_pretool_hooks", _boom)

    from hydrahive_core.orchestrator_tools import execute_tool_call
    orch = MagicMock()
    orch._execute_tool = AsyncMock()
    orch._resolve_allowed_tool = MagicMock(return_value=MagicMock(name="tool"))
    sess = MagicMock(); sess.id = "s1"
    orch._sessions = MagicMock()
    orch._sessions.get_active = MagicMock(return_value=sess)

    boss_cfg = MagicMock()
    boss_cfg.mcp_servers = None
    boss_cfg.id = "agent1"
    boss_cfg.risk_policy = "trusted"

    result, is_error = await execute_tool_call(
        orch, boss_cfg=boss_cfg, project_id="p1",
        tool_name="Bash", tool_input={"command": "ls"},
    )
    assert is_error is True
    assert result.get("risk") == "hook_error"
    orch._execute_tool.assert_not_called()
    # Hint ist redacted — Token aus Exception-Message darf nicht klartext sein
    hint = result.get("hint") or ""
    assert _fake_token not in hint, f"Klartext-Token im Hint: {hint!r}"
    assert "SECRETSECRETSECRET" not in hint
    assert "REDACTED:gh_token" in hint, f"Token nicht redacted: {hint!r}"


# ── 17. Integration: PostToolUse-Runtime-Exception → non-blocking ────────────
async def test_integration_posttool_runtime_error_non_blocking(tmp_path, monkeypatch):
    monkeypatch.setenv("HYDRAHIVE_SETTINGS_FILE", str(tmp_path / "missing.json"))
    reload_hook_runtime()

    async def _boom(*a, **kw):
        raise RuntimeError("post broke")
    monkeypatch.setattr("hydrahive_core.hook_runtime.run_posttool_hooks", _boom)

    from hydrahive_core.orchestrator_tools import execute_tool_call
    orch = MagicMock()
    orch._execute_tool = AsyncMock(return_value={"ok": True, "value": 42})
    orch._resolve_allowed_tool = MagicMock(return_value=MagicMock(name="tool"))
    sess = MagicMock(); sess.id = "s1"
    orch._sessions = MagicMock()
    orch._sessions.get_active = MagicMock(return_value=sess)

    boss_cfg = MagicMock()
    boss_cfg.mcp_servers = None
    boss_cfg.id = "agent1"
    boss_cfg.risk_policy = "trusted"

    result, is_error = await execute_tool_call(
        orch, boss_cfg=boss_cfg, project_id="p1",
        tool_name="Bash", tool_input={"command": "ls"},
    )
    assert is_error is False
    assert result == {"ok": True, "value": 42}, "Tool-Ergebnis muss erhalten bleiben"
    orch._execute_tool.assert_called_once()


# ── 18. Integration: ohne settings.json läuft Tool normal ────────────────────
async def test_integration_no_settings_runs_tool(tmp_path, monkeypatch):
    monkeypatch.setenv("HYDRAHIVE_SETTINGS_FILE", str(tmp_path / "missing.json"))
    reload_hook_runtime()

    from hydrahive_core.orchestrator_tools import execute_tool_call

    orch = MagicMock()
    orch._execute_tool = AsyncMock(return_value={"ok": True})
    orch._resolve_allowed_tool = MagicMock(return_value=MagicMock(name="tool"))
    sess = MagicMock(); sess.id = "s1"
    orch._sessions = MagicMock()
    orch._sessions.get_active = MagicMock(return_value=sess)

    boss_cfg = MagicMock()
    boss_cfg.mcp_servers = None
    boss_cfg.id = "agent1"
    boss_cfg.risk_policy = "trusted"

    result, is_error = await execute_tool_call(
        orch, boss_cfg=boss_cfg, project_id="p1",
        tool_name="Bash", tool_input={"command": "ls"},
    )
    assert is_error is False
    orch._execute_tool.assert_called_once()
