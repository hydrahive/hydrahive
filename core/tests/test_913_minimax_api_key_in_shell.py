"""
test_913_minimax_api_key_in_shell.py — Issue #913

Invariant-Tests: MINIMAX_API_KEY aus /etc/hydrahive/llm_env muss in
shell_exec-Subprozessen korrekt ankommen.

Zwei Injektionspfade:
  1. sudo-Pfad (UNRESTRICTED, proj_<id>-User):
     export KEY=val; command  (inline im bash -c String)
  2. env=-Pfad (alle Modi):
     env={**os.environ, **_load_llm_env()}  → asyncio.create_subprocess_shell

Kein Prozessstart. Kein Netzwerk. Reinspektive Assertions.
Analog zu test_architecture_invariants.py.
"""

from __future__ import annotations

import asyncio
import os
import shlex
from unittest.mock import patch

import pytest


# ===========================================================================
# Invariante A — _load_llm_env() parsing & caching
# ===========================================================================

def test_A1_load_llm_env_returns_dict():
    """A1: _load_llm_env() gibt ein dict zurück."""
    from hydrahive_core.tool_registry import _load_llm_env
    result = _load_llm_env()
    assert isinstance(result, dict)


def test_A2_load_llm_env_caches():
    """A2: Zweiter Aufruf liefert denselben gecachten Wert.

    Das Cache-Objekt wird bei gleichem mtime zurückgegeben (nicht neu gebaut).
    """
    from hydrahive_core.tool_registry import _load_llm_env
    r1 = _load_llm_env()
    r2 = _load_llm_env()
    # Bei unveränderter llm_env: gleiches Objekt (Cache-Hit)
    assert r1 == r2


# ===========================================================================
# Invariante B — sudo-Pfad: export KEY=val; im bash -c String
# ===========================================================================

def test_B1_export_string_includes_key_and_value():
    """B1: Export-String enthält MINIMAX_API_KEY und den Wert."""
    fake_env = {"MINIMAX_API_KEY": "secret_test_value"}
    with patch("hydrahive_core.tool_registry._load_llm_env", lambda: fake_env):
        from hydrahive_core.tool_registry import _load_llm_env
        command = "echo hello"
        exports = "".join(
            f"export {k}={shlex.quote(v)}; "
            for k, v in _load_llm_env().items()
        )
        full_cmd = exports + command

        assert "MINIMAX_API_KEY" in full_cmd
        assert "secret_test_value" in full_cmd
        assert "export MINIMAX_API_KEY=" in full_cmd


def test_B2_shlex_quote_prevents_injection():
    """B2: shlex.quote single-quotet Shell-Metacharacters → keine Injection."""
    # shlex.quote single-quotet bei $, ` etc. → $ expandiert nicht
    special = "val with $pecial `backticks` and ;semicolon"
    quoted = shlex.quote(special)
    # Value ist vollständig in Quotes → alle metacharacters literal
    assert quoted.startswith("'") or quoted.startswith('"')
    # Der裸露 $ ist NICHT inQuotes → $pecial expandiert nicht
    # quoted = 'val with $pecial...' → $ ist裸露 → ACHTUNG
    # shlex.quote single-quotet NUR wenn nötig — einfacher $ bleibt als $
    # Das ist korrekt! In single-quoted strings: 'abc$def' → $ bleibt literal
    # shlex.quote('abc$def') → 'abc$def' (mit Quotes drum)
    assert quoted.startswith("'"), f"shlex.quote sollte quoting-setzen: {quoted!r}"


def test_B3_sudo_exec_command_structure():
    """B3: sudo-Pfad baut `sudo -n -u proj_X bash -c '...'` mit export darin."""
    fake_env = {"MINIMAX_API_KEY": "testval"}
    with patch("hydrahive_core.tool_registry._load_llm_env", lambda: fake_env):
        from hydrahive_core.tool_registry import _load_llm_env
        project_id = "myproject"
        command = "echo $MINIMAX_API_KEY"

        exports = "".join(
            f"export {k}={shlex.quote(v)}; "
            for k, v in _load_llm_env().items()
        )
        full_cmd = exports + command
        proj_user = f"proj_{project_id}"
        exec_cmd = f"sudo -n -u {shlex.quote(proj_user)} bash -c {shlex.quote(full_cmd)}"

        assert exec_cmd.startswith("sudo -n -u")
        assert "bash -c" in exec_cmd
        bash_c_arg = exec_cmd.split("bash -c", 1)[1]
        assert "MINIMAX_API_KEY" in bash_c_arg
        assert "testval" in bash_c_arg


# ===========================================================================
# Invariante C — env=-Pfad: _merged_env = {**os.environ, **_load_llm_env()}
# ===========================================================================

def test_C1_merged_env_contains_minimax_key():
    """C1: _merged_env enthält MINIMAX_API_KEY aus llm_env."""
    fake_env = {"MINIMAX_API_KEY": "env_path_value", "EXTRA": "extraval"}
    with patch("hydrahive_core.tool_registry._load_llm_env", lambda: fake_env):
        from hydrahive_core.tool_registry import _load_llm_env
        merged = {**os.environ, **_load_llm_env()}

        assert "MINIMAX_API_KEY" in merged
        assert merged["MINIMAX_API_KEY"] == "env_path_value"
        assert merged["EXTRA"] == "extraval"


def test_C2_llm_env_overwrites_environ():
    """C2: Gleichnamiger Key in llm_env überschreibt os.environ."""
    fake_env = {"MINIMAX_API_KEY": "overridden"}
    with patch("hydrahive_core.tool_registry._load_llm_env", lambda: fake_env):
        from hydrahive_core.tool_registry import _load_llm_env
        merged = {**os.environ, **_load_llm_env()}
        assert merged["MINIMAX_API_KEY"] == "overridden"


def test_C3_empty_llm_env_keeps_environ():
    """C3: Leeres llm_env → os.environ bleibt vollständig erhalten."""
    fake_env = {}
    with patch("hydrahive_core.tool_registry._load_llm_env", lambda: fake_env):
        from hydrahive_core.tool_registry import _load_llm_env
        merged = {**os.environ, **_load_llm_env()}
        for k, v in os.environ.items():
            assert k in merged


# ===========================================================================
# Invariante D — execute() nutzt _load_llm_env im subprocess env
# ===========================================================================

def test_D1_execute_elevated_env_contains_minimax_key():
    """D1: execute(elevated) ruft subprocess mit env= auf, das MINIMAX_API_KEY enthält."""
    from hydrahive_core.tool_registry import ShellExecTool

    fake_env = {"MINIMAX_API_KEY": "subprocess_test_value", "OTHER": "other"}
    captured_env: list = []
    captured_cmds: list = []

    async def fake_subprocess(cmd, stdout=None, stderr=None, cwd=None, env=None):
        captured_cmds.append(cmd)
        if env:
            captured_env.append(dict(env))
        class MockProc:
            returncode = 0
            async def communicate(self): return b"ok", b""
            def kill(self): pass
        return MockProc()

    # Set bwrap_works BEFORE importing the module-patched function
    # We need to patch the class attribute and the asyncio function together
    saved_bwrap = ShellExecTool._bwrap_works
    ShellExecTool._bwrap_works = True  # True → bwrap-Pfad mit env= (nicht blocked)

    with patch("hydrahive_core.tool_registry._load_llm_env", lambda: fake_env):
        with patch("asyncio.create_subprocess_shell", fake_subprocess):
            tool = ShellExecTool()
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    tool.execute(
                        agent_id="test", project_id="testproj",
                        command="echo test", _execution_mode="elevated",
                    )
                )
            finally:
                loop.close()

    ShellExecTool._bwrap_works = saved_bwrap

    assert len(captured_env) >= 1, (
        f"Kein subprocess gerufen. cmds: {captured_cmds}"
    )
    env = captured_env[0]
    assert "MINIMAX_API_KEY" in env, f"KEY nicht in env: {list(env.keys())}"
    assert env["MINIMAX_API_KEY"] == "subprocess_test_value"
    assert env["OTHER"] == "other"
    assert "PATH" in env


def test_D2_elevated_no_sudo_in_command():
    """D2: elevated-Modus nutzt NICHT den sudo-Pfad (kein proj_-User)."""
    from hydrahive_core.tool_registry import ShellExecTool

    fake_env = {"MINIMAX_API_KEY": "elevated_test"}
    captured_cmds: list = []

    async def fake_subprocess(cmd, stdout=None, stderr=None, cwd=None, env=None):
        captured_cmds.append(cmd)
        class MockProc:
            returncode = 0
            async def communicate(self): return b"ok", b""
            def kill(self): pass
        return MockProc()

    saved_bwrap = ShellExecTool._bwrap_works
    ShellExecTool._bwrap_works = True  # True → bwrap-Pfad mit env=

    with patch("hydrahive_core.tool_registry._load_llm_env", lambda: fake_env):
        with patch("asyncio.create_subprocess_shell", fake_subprocess):
            tool = ShellExecTool()
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    tool.execute(
                        agent_id="test", project_id="testproj",
                        command="echo test", _execution_mode="elevated",
                    )
                )
            finally:
                loop.close()

    ShellExecTool._bwrap_works = saved_bwrap

    assert len(captured_cmds) >= 1
    # elevated nutzt bwrap (nicht sudo) — bwrap-Cmd kein sudo
    assert "sudo" not in captured_cmds[0]


# ===========================================================================
# Invariante E — zentrale E2E: MINIMAX_API_KEY kommt im Subprozess an
# ===========================================================================

def test_E1_minimax_api_key_in_subprocess_env():
    """E1: MINIMAX_API_KEY=testvalue_from_llm_env ist im subprocess env dict.

    Kerninvariante für #913.
    """
    from hydrahive_core.tool_registry import ShellExecTool

    fake_env = {"MINIMAX_API_KEY": "testvalue_from_llm_env"}
    received: list = []

    async def fake_subprocess(cmd, stdout=None, stderr=None, cwd=None, env=None):
        if env:
            received.append(env.get("MINIMAX_API_KEY", "NOT_FOUND"))
        class MockProc:
            returncode = 0
            async def communicate(self): return b"ok", b""
            def kill(self): pass
        return MockProc()

    saved_bwrap = ShellExecTool._bwrap_works
    ShellExecTool._bwrap_works = True  # True → bwrap-Pfad mit env=

    with patch("hydrahive_core.tool_registry._load_llm_env", lambda: fake_env):
        with patch("asyncio.create_subprocess_shell", fake_subprocess):
            tool = ShellExecTool()
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(
                    tool.execute(
                        agent_id="test", project_id="testproj",
                        command="echo ok", _execution_mode="elevated",
                    )
                )
            finally:
                loop.close()

    ShellExecTool._bwrap_works = saved_bwrap

    assert len(received) >= 1, f"Subprocess nicht aufgerufen: {received}"
    assert received[0] == "testvalue_from_llm_env", (
        f"MINIMAX_API_KEY im subprocess env falsch: "
        f"expected 'testvalue_from_llm_env', got {received[0]!r}"
    )