"""
test_unrestricted_root_block.py — #747 root-Fallback hart geblockt

Vor dem Fix: shell_exec im unrestricted-Modus ohne proj_<id>-User fiel
still auf `sudo bash -c` als root zurueck. Jetzt: error-dict, kein
Subprocess. Env-Override HYDRAHIVE_UNRESTRICTED_ALLOW_ROOT=1 fuer
Dev-Notfaelle.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest

from hydrahive_core.tool_registry import ShellExecTool


def _run_unrestricted(project_id: str = "testproj", command: str = "echo hi"):
    """Ruft shell_exec im unrestricted-Modus auf, mit gemocktem pwd.getpwnam,
    das immer KeyError wirft (proj_<id>-User existiert nicht)."""
    tool = ShellExecTool()
    with mock.patch("pwd.getpwnam", side_effect=KeyError("no such user")), \
         mock.patch("asyncio.create_subprocess_shell") as subproc_mock:
        result = asyncio.run(tool.execute(
            agent_id="test_agent",
            project_id=project_id,
            command=command,
            _execution_mode="unrestricted",
        ))
    return result, subproc_mock


# ============================================================= Hard-Block


def test_unrestricted_without_proj_user_returns_error():
    """Fehlender proj_<id>-User ohne Env-Override → error, kein subprocess."""
    result, subproc = _run_unrestricted()
    assert "error" in result
    assert "verweigert" in result["error"].lower()
    assert "proj_testproj" in result["error"]
    assert result.get("exit_code") == -1
    # Subprocess darf NICHT gestartet werden
    subproc.assert_not_called()


def test_unrestricted_error_verweist_auf_provision_und_elevated():
    """Error-Message soll die beiden validen Auswege nennen."""
    result, _ = _run_unrestricted()
    err = result["error"].lower()
    assert "provision" in err
    assert "elevated" in err


def test_unrestricted_without_project_id_returns_error():
    """Ohne project_id → auch error (kein proj_<id>-User konstruierbar)."""
    tool = ShellExecTool()
    with mock.patch("asyncio.create_subprocess_shell") as subproc_mock:
        result = asyncio.run(tool.execute(
            agent_id="test_agent",
            project_id="",
            command="echo hi",
            _execution_mode="unrestricted",
        ))
    assert "error" in result
    assert "verweigert" in result["error"].lower()
    subproc_mock.assert_not_called()


# ============================================================= Env-Override


def test_env_override_allows_root_fallback(monkeypatch):
    """HYDRAHIVE_UNRESTRICTED_ALLOW_ROOT=1 → root-Fallback wieder aktiv."""
    monkeypatch.setenv("HYDRAHIVE_UNRESTRICTED_ALLOW_ROOT", "1")
    tool = ShellExecTool()

    async def fake_wait(*args, **kwargs):
        return (b"hi\n", b"")

    fake_proc = mock.MagicMock()
    fake_proc.communicate = mock.AsyncMock(return_value=(b"hi\n", b""))
    fake_proc.returncode = 0

    async def fake_create_subprocess_shell(*args, **kwargs):
        return fake_proc

    with mock.patch("pwd.getpwnam", side_effect=KeyError("no such user")), \
         mock.patch("asyncio.create_subprocess_shell", side_effect=fake_create_subprocess_shell) as subproc_mock:
        result = asyncio.run(tool.execute(
            agent_id="test_agent",
            project_id="testproj",
            command="echo hi",
            _execution_mode="unrestricted",
        ))

    # Kein error-Feld im Erfolgsfall
    assert "error" not in result, f"Unexpected error: {result}"
    # Subprocess wurde gestartet
    subproc_mock.assert_called_once()
    # Mit sudo bash -c (root) — nicht sudo -u proj_user
    exec_cmd = subproc_mock.call_args.args[0]
    assert "sudo bash -c" in exec_cmd
    assert "-u proj_" not in exec_cmd


def test_env_override_nicht_gesetzt_blockt_weiterhin(monkeypatch):
    """Env-Override nur bei exakt '1' aktiv. Leer oder 'true' → Block bleibt."""
    monkeypatch.setenv("HYDRAHIVE_UNRESTRICTED_ALLOW_ROOT", "true")
    result, subproc = _run_unrestricted()
    assert "error" in result
    subproc.assert_not_called()


# ============================================================= Proj-User existiert → unveraendert


def test_proj_user_exists_runs_as_user():
    """Wenn proj_<id>-User existiert, laeuft unrestricted wie vorher (sudo -u)."""
    tool = ShellExecTool()

    fake_proc = mock.MagicMock()
    fake_proc.communicate = mock.AsyncMock(return_value=(b"ok\n", b""))
    fake_proc.returncode = 0

    async def fake_create_subprocess_shell(*args, **kwargs):
        return fake_proc

    with mock.patch("pwd.getpwnam", return_value=mock.MagicMock()), \
         mock.patch("asyncio.create_subprocess_shell", side_effect=fake_create_subprocess_shell) as subproc_mock:
        result = asyncio.run(tool.execute(
            agent_id="test_agent",
            project_id="valid_proj",
            command="echo ok",
            _execution_mode="unrestricted",
        ))

    assert "error" not in result
    exec_cmd = subproc_mock.call_args.args[0]
    assert "sudo -n -u" in exec_cmd
    assert "proj_valid_proj" in exec_cmd
    # Kein stiller root-Fallback
    assert "sudo bash -c" not in exec_cmd or "-u proj_valid_proj" in exec_cmd
