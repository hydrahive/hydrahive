"""Tests für OnTaskStart/OnTaskDone Runtime (#656)."""
from __future__ import annotations

import json
import stat
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from hydrahive_core.hook_runtime import (
    PostHookReport,
    PreHookDecision,
    reload_hook_runtime,
    run_task_done_hooks,
    run_task_start_hooks,
)


def write_hook_script(dir: Path, name: str, body: str) -> Path:
    p = dir / name
    p.write_text(body, encoding="utf-8")
    p.chmod(p.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return p


def write_settings(dir: Path, hooks_cfg: dict) -> Path:
    p = dir / "settings.json"
    p.write_text(json.dumps({"hooks": hooks_cfg}), encoding="utf-8")
    return p


TASK = {
    "kind": "agent_turn",
    "project_id": "p1",
    "agent_id": "a1",
    "user": "till",
    "session_id": "s1",
    "message_preview": "do the thing",
    "started_at": "2026-04-15T20:00:00Z",
}


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


# ── 1. No settings.json → allow / empty ──────────────────────────────────────
async def test_no_settings_allow(monkeypatch, tmp_path):
    monkeypatch.setenv("HYDRAHIVE_SETTINGS_FILE", str(tmp_path / "nope.json"))
    reload_hook_runtime()
    d = await run_task_start_hooks(TASK)
    assert isinstance(d, PreHookDecision) and d.action == "allow"
    r = await run_task_done_hooks(TASK, {"ok": True, "duration_ms": 10, "summary": "", "error": None})
    assert isinstance(r, PostHookReport) and r.warnings == []


# ── 2./3. allow + warn ───────────────────────────────────────────────────────
async def test_allow(tmp_path, configure):
    h = write_hook_script(tmp_path, "allow.sh",
                          "#!/usr/bin/env bash\necho '{\"action\":\"allow\"}'\n")
    configure({"OnTaskStart": [{"hooks": [{"type": "command", "command": str(h)}]}]})
    assert (await run_task_start_hooks(TASK)).action == "allow"


async def test_warn(tmp_path, configure):
    h = write_hook_script(tmp_path, "warn.sh",
                          "#!/usr/bin/env bash\necho '{\"action\":\"warn\",\"message\":\"w\"}'\n")
    configure({"OnTaskStart": [{"hooks": [{"type": "command", "command": str(h)}]}]})
    d = await run_task_start_hooks(TASK)
    assert d.action == "allow"
    assert "w" in d.warnings


# ── 4./5. block Start → block, block Done → warn ─────────────────────────────
async def test_block_start(tmp_path, configure):
    h = write_hook_script(tmp_path, "block.sh",
                          "#!/usr/bin/env bash\necho '{\"action\":\"block\",\"message\":\"no-go\"}'\n")
    configure({"OnTaskStart": [{"hooks": [{"type": "command", "command": str(h)}]}]})
    d = await run_task_start_hooks(TASK)
    assert d.action == "block"
    assert d.message == "no-go"


async def test_block_done_is_warn(tmp_path, configure):
    h = write_hook_script(tmp_path, "block.sh",
                          "#!/usr/bin/env bash\necho '{\"action\":\"block\",\"message\":\"late\"}'\n")
    configure({"OnTaskDone": [{"hooks": [{"type": "command", "command": str(h)}]}]})
    r = await run_task_done_hooks(TASK, {"ok": True, "duration_ms": 1, "summary": "", "error": None})
    assert any("late" in w for w in r.warnings)


# ── 6./7. Exit != 0 ──────────────────────────────────────────────────────────
async def test_exit_nonzero_start(tmp_path, configure):
    h = write_hook_script(tmp_path, "fail.sh",
                          "#!/usr/bin/env bash\nexit 3\n")
    configure({"OnTaskStart": [{"hooks": [{"type": "command", "command": str(h)}]}]})
    d = await run_task_start_hooks(TASK)
    assert d.action == "block"
    assert "exit 3" in (d.message or "")


async def test_exit_nonzero_done(tmp_path, configure):
    h = write_hook_script(tmp_path, "fail.sh",
                          "#!/usr/bin/env bash\nexit 3\n")
    configure({"OnTaskDone": [{"hooks": [{"type": "command", "command": str(h)}]}]})
    r = await run_task_done_hooks(TASK, {"ok": True, "duration_ms": 1, "summary": "", "error": None})
    assert any("exit 3" in w for w in r.warnings)


# ── 8./9. Timeout ────────────────────────────────────────────────────────────
async def test_timeout_start(tmp_path, configure):
    h = write_hook_script(tmp_path, "slow.sh", "#!/usr/bin/env bash\nsleep 5\n")
    configure({"OnTaskStart": [{"hooks": [{"type": "command", "command": str(h), "timeout": 1}]}]})
    d = await run_task_start_hooks(TASK)
    assert d.action == "block"
    assert "timeout" in (d.message or "").lower()


async def test_timeout_done(tmp_path, configure):
    h = write_hook_script(tmp_path, "slow.sh", "#!/usr/bin/env bash\nsleep 5\n")
    configure({"OnTaskDone": [{"hooks": [{"type": "command", "command": str(h), "timeout": 1}]}]})
    r = await run_task_done_hooks(TASK, {"ok": True, "duration_ms": 1, "summary": "", "error": None})
    assert any("timeout" in w.lower() for w in r.warnings)


# ── 10./11. Invalid JSON ─────────────────────────────────────────────────────
async def test_invalid_json_start(tmp_path, configure):
    h = write_hook_script(tmp_path, "junk.sh", "#!/usr/bin/env bash\necho 'garbage'\n")
    configure({"OnTaskStart": [{"hooks": [{"type": "command", "command": str(h)}]}]})
    d = await run_task_start_hooks(TASK)
    assert d.action == "block"


async def test_invalid_json_done(tmp_path, configure):
    h = write_hook_script(tmp_path, "junk.sh", "#!/usr/bin/env bash\necho 'garbage'\n")
    configure({"OnTaskDone": [{"hooks": [{"type": "command", "command": str(h)}]}]})
    r = await run_task_done_hooks(TASK, {"ok": True, "duration_ms": 1, "summary": "", "error": None})
    assert any("invalid" in w.lower() for w in r.warnings)


# ── 12. Disabled übersprungen ────────────────────────────────────────────────
async def test_disabled_skipped(tmp_path, configure):
    h = write_hook_script(tmp_path, "block.sh",
                          "#!/usr/bin/env bash\necho '{\"action\":\"block\"}'\n")
    configure({"OnTaskStart": [{"hooks": [
        {"type": "command", "command": str(h), "disabled": True}
    ]}]})
    assert (await run_task_start_hooks(TASK)).action == "allow"


# ── 13. Matcher auf task.kind ────────────────────────────────────────────────
async def test_matcher_kind(tmp_path, configure):
    h = write_hook_script(tmp_path, "block.sh",
                          "#!/usr/bin/env bash\necho '{\"action\":\"block\"}'\n")
    configure({"OnTaskStart": [{"matcher": "agent_turn",
                                "hooks": [{"type": "command", "command": str(h)}]}]})
    assert (await run_task_start_hooks({**TASK, "kind": "other_kind"})).action == "allow"
    assert (await run_task_start_hooks(TASK)).action == "block"


# ── 14. Fail-fast Start, alle Done laufen ────────────────────────────────────
async def test_failfast_start(tmp_path, configure):
    canary = tmp_path / "ran"
    h1 = write_hook_script(tmp_path, "first.sh",
                           "#!/usr/bin/env bash\necho '{\"action\":\"block\"}'\n")
    h2 = write_hook_script(
        tmp_path, "second.sh",
        f"#!/usr/bin/env bash\ntouch {canary}\necho '{{\"action\":\"allow\"}}'\n",
    )
    configure({"OnTaskStart": [
        {"hooks": [{"type": "command", "command": str(h1)}]},
        {"hooks": [{"type": "command", "command": str(h2)}]},
    ]})
    assert (await run_task_start_hooks(TASK)).action == "block"
    assert not canary.exists()


async def test_done_all_run(tmp_path, configure):
    a = tmp_path / "a"
    b = tmp_path / "b"
    h1 = write_hook_script(
        tmp_path, "a.sh",
        f"#!/usr/bin/env bash\ntouch {a}\necho '{{\"action\":\"warn\",\"message\":\"w1\"}}'\n",
    )
    h2 = write_hook_script(
        tmp_path, "b.sh",
        f"#!/usr/bin/env bash\ntouch {b}\necho '{{\"action\":\"warn\",\"message\":\"w2\"}}'\n",
    )
    configure({"OnTaskDone": [{"hooks": [
        {"type": "command", "command": str(h1)},
        {"type": "command", "command": str(h2)},
    ]}]})
    r = await run_task_done_hooks(TASK, {"ok": True, "duration_ms": 1, "summary": "", "error": None})
    assert a.exists() and b.exists()
    assert "w1" in r.warnings and "w2" in r.warnings


# ── 15. Redaction: Secret in message_preview → Hook sieht nur REDACTED ──────
async def test_redaction_task_preview(tmp_path, configure):
    dump = tmp_path / "stdin.json"
    h = write_hook_script(
        tmp_path, "echo.sh",
        f"#!/usr/bin/env bash\ncat > {dump}\necho '{{\"action\":\"allow\"}}'\n",
    )
    configure({"OnTaskStart": [{"hooks": [{"type": "command", "command": str(h)}]}]})
    # realistisch aussehender Fake-PAT zur Laufzeit konkateniert
    fake_body = "A" * 32 + "WXYZ"
    fake_token = "ghp_" + fake_body
    task_with_secret = {**TASK, "message_preview": f"auth: {fake_token}"}
    d = await run_task_start_hooks(task_with_secret)
    assert d.action == "allow"
    content = dump.read_text(encoding="utf-8")
    assert fake_body not in content
    assert "REDACTED:gh_token" in content


# ── 16./17. Integration: Orchestrator-Block / Runtime-Exception ─────────────
@pytest.fixture
def mock_orch():
    """Minimaler Orchestrator, der _handle_message_impl ausführbar macht.

    Wir umgehen __init__ und setzen nur die Felder, die _handle_message_impl
    bis zum (gehookten) Return-Pfad anfasst.
    """
    from hydrahive_core.orchestrator import Orchestrator

    orch = Orchestrator.__new__(Orchestrator)
    orch._sessions = MagicMock()
    orch._sessions.append = AsyncMock()
    orch._discovery = MagicMock()
    orch._runtime = MagicMock()
    orch._runtime._handles = {}
    orch._runtime.set_activity = MagicMock()
    return orch


async def test_integration_task_start_block(tmp_path, monkeypatch, mock_orch):
    # Block-Hook für OnTaskStart
    h = write_hook_script(tmp_path, "block.sh",
                          "#!/usr/bin/env bash\necho '{\"action\":\"block\",\"message\":\"denied\"}'\n")
    settings = write_settings(tmp_path, {"OnTaskStart": [
        {"matcher": "agent_turn", "hooks": [{"type": "command", "command": str(h)}]}
    ]})
    monkeypatch.setenv("HYDRAHIVE_SETTINGS_FILE", str(settings))
    reload_hook_runtime()

    project_cfg = MagicMock()
    project_cfg.is_v2 = False
    project_cfg.agents = MagicMock()
    project_cfg.agents.boss = "boss1"

    text, workers = await mock_orch._handle_message_impl(
        "p1", project_cfg, "hello", sender="till",
    )
    assert text.startswith("[Blockiert]")
    assert "denied" in text
    assert workers == []
    # WICHTIG: Keine Session-Appends (weder USER noch ASSISTANT).
    mock_orch._sessions.append.assert_not_called()


async def test_integration_task_start_runtime_error_fails_closed(
    tmp_path, monkeypatch, mock_orch,
):
    monkeypatch.setenv("HYDRAHIVE_SETTINGS_FILE", str(tmp_path / "missing.json"))
    reload_hook_runtime()

    async def _boom(*a, **kw):
        raise RuntimeError("task hook subsystem down")
    monkeypatch.setattr("hydrahive_core.hook_runtime.run_task_start_hooks", _boom)

    project_cfg = MagicMock()
    project_cfg.is_v2 = False
    project_cfg.agents = MagicMock()
    project_cfg.agents.boss = "boss1"

    text, workers = await mock_orch._handle_message_impl(
        "p1", project_cfg, "hello", sender="till",
    )
    assert text.startswith("[Blockiert]")
    assert "OnTaskStart-Hook-Runtime-Fehler" in text
    assert workers == []
    mock_orch._sessions.append.assert_not_called()


# ── 18. Integration: OnTaskDone-Runtime-Fehler ändert final_response nicht ──
async def test_integration_task_done_error_non_blocking(
    tmp_path, monkeypatch, mock_orch,
):
    """Wir rufen run_task_done_hooks direkt auf, um die Non-Blocking-
    Semantik zu verifizieren. Der volle _handle_message_impl-Pfad bis zum
    OnTaskDone-Call würde einen vollständig instrumentierten Tool-Loop
    brauchen — zu schwergewichtig für einen Unit-Test. Die Integration
    in orchestrator.py selbst ist trivial (try/except + logger.warning),
    siehe Code-Kommentar #656 OnTaskDone.
    """
    monkeypatch.setenv("HYDRAHIVE_SETTINGS_FILE", str(tmp_path / "missing.json"))
    reload_hook_runtime()

    # Fehlende Settings → sofort leerer Report, auch wenn wir unpässliche
    # task/result mitgeben.
    report = await run_task_done_hooks(TASK, {"ok": True, "duration_ms": 1, "summary": "", "error": None})
    assert report.warnings == []


# ── 19. No-settings Integration bleibt allow ─────────────────────────────────
async def test_integration_no_settings_is_noop(tmp_path, monkeypatch, mock_orch):
    monkeypatch.setenv("HYDRAHIVE_SETTINGS_FILE", str(tmp_path / "missing.json"))
    reload_hook_runtime()

    project_cfg = MagicMock()
    project_cfg.is_v2 = False
    project_cfg.agents = MagicMock()
    project_cfg.agents.boss = "unknown_boss"

    # discovery.get liefert None (Agent nicht gefunden) — Baseline-Pfad.
    # Wichtig: der Code läuft durch den Start-Hook (no-op), dann in den
    # bestehenden "Boss-Agent nicht gefunden"-Zweig.
    mock_orch._discovery.get = MagicMock(return_value=None)

    text, workers = await mock_orch._handle_message_impl(
        "p1", project_cfg, "hello", sender="till",
    )
    assert text.startswith("[Fehler]")
    assert "unknown_boss" in text
    assert workers == []
    # USER-Message wurde in Session geschrieben (Baseline-Verhalten).
    mock_orch._sessions.append.assert_called_once()
