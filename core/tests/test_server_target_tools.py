"""
test_server_target_tools.py — #584-C Tool-Handler (server_shell, server_file_*).

Mockt den SSH-Runner und prüft:
- Auth-Resolve wird aufgerufen und Fehler als Tool-Result gerendert.
- Korrekte kwargs an run_ssh_command (Host, Port, Key, Timeout).
- server_file_read nutzt dd + shlex.quote, max_bytes Clamping.
- server_file_write nutzt base64 statt raw-Interpolation, mode-Validation.
- Output/Error enthält nie den ssh_key_path.
"""
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.tool_registry import (
    ServerShellTool, ServerFileReadTool, ServerFileWriteTool,
)
from hydrahive_core.project_targets import set_project_targets


@pytest.fixture
def env(tmp_path, monkeypatch):
    targets_file = tmp_path / "project_targets.json"
    users_file   = tmp_path / "users.json"
    agent_srv    = tmp_path / "agent_servers.json"
    srv_dir      = tmp_path / "servers"
    srv_keys     = tmp_path / "server_keys"
    wks_keys     = tmp_path / "wks_keys"
    for p in (srv_dir, srv_keys, wks_keys):
        p.mkdir(parents=True, exist_ok=True)

    class _S:
        project_targets_config = targets_file
        users_config = users_file
        servers_dir = srv_dir
        server_keys_dir = srv_keys
        wks_keys_dir = wks_keys
        agent_servers_config = agent_srv
        llm_env = tmp_path / "llm.env"

    monkeypatch.setattr("hydrahive_core.project_targets.settings", _S)
    monkeypatch.setattr("hydrahive_core.target_resolution.settings", _S)
    monkeypatch.setattr("hydrahive_core.router_servers.settings", _S)
    monkeypatch.setattr("hydrahive_core.router_servers.SERVERS_DIR", srv_dir)
    monkeypatch.setattr("hydrahive_core.router_servers.SERVERS_KEYS_DIR", srv_keys)
    monkeypatch.setattr("hydrahive_core.router_servers.AGENT_SERVERS_FILE", agent_srv)

    (srv_dir / "prod-web.json").write_text(json.dumps({
        "id": "prod-web", "name": "Production Web",
        "ip": "1.2.3.4", "ssh_user": "root", "ssh_port": 2222,
    }), encoding="utf-8")
    (srv_keys / "prod-web").write_text("KEY", encoding="utf-8")
    users_file.write_text("{}", encoding="utf-8")
    set_project_targets("proj-a", {
        "servers": [{"server_id": "prod-web", "role": "web", "note": ""}],
        "wks": [],
    })
    return _S


# ═════════════════════════════════════════════════════ ServerShellTool

class TestServerShellTool:

    async def test_builds_ssh_command_correctly(self, env):
        captured = {}

        async def fake_run(host, ssh_user, ssh_port, key_path, command, *, timeout):
            captured.update({"host": host, "user": ssh_user, "port": ssh_port,
                             "key": key_path, "cmd": command, "timeout": timeout})
            return {"stdout": "ok\n", "stderr": "", "exit_code": 0}

        with patch("hydrahive_core.target_resolution.run_ssh_command", side_effect=fake_run):
            result = await ServerShellTool().execute(
                agent_id="proj-a", project_id="proj-a",
                server_id="prod-web", command="uname -a",
            )

        assert captured["host"] == "1.2.3.4"
        assert captured["user"] == "root"
        assert captured["port"] == 2222
        assert captured["cmd"] == "uname -a"
        assert captured["timeout"] == 60
        assert result["stdout"] == "ok\n"
        assert result["server_id"] == "prod-web"

    async def test_rejects_unassigned_server(self, env):
        result = await ServerShellTool().execute(
            agent_id="proj-a", project_id="proj-a",
            server_id="other-srv", command="true",
        )
        assert "error" in result
        assert "nicht zugewiesen" in result["error"]
        assert result["exit_code"] == -1

    async def test_honors_timeout_clamp(self, env):
        captured = {}

        async def fake_run(*args, timeout, **kwargs):
            captured["timeout"] = timeout
            return {"stdout": "", "stderr": "", "exit_code": 0}

        with patch("hydrahive_core.target_resolution.run_ssh_command", side_effect=fake_run):
            await ServerShellTool().execute(
                agent_id="proj-a", project_id="proj-a",
                server_id="prod-web", command="x", timeout=9999,
            )
        assert captured["timeout"] == 120  # clamped

    async def test_never_leaks_key_path_in_error(self, env):
        result = await ServerShellTool().execute(
            agent_id="proj-a", project_id="proj-a",
            server_id="nonexistent", command="x",
        )
        assert "server_keys" not in result.get("error", "")
        assert str(env.server_keys_dir) not in result.get("error", "")


# ═════════════════════════════════════════════════════ ServerFileReadTool

class TestServerFileReadTool:

    async def test_uses_dd_and_quotes_path(self, env):
        captured = {}

        async def fake_run(host, ssh_user, ssh_port, key_path, command, *, timeout):
            captured["cmd"] = command
            # dd liefert Inhalt — simuliere 100 Bytes
            return {"stdout": "x" * 100, "stderr": "", "exit_code": 0}

        with patch("hydrahive_core.target_resolution.run_ssh_command", side_effect=fake_run):
            result = await ServerFileReadTool().execute(
                agent_id="proj-a", project_id="proj-a",
                server_id="prod-web",
                path="/etc/nginx/nginx.conf",
                max_bytes=200,
            )

        assert "dd if=" in captured["cmd"]
        # shlex.quote quotet nur wenn nötig; simpler Pfad bleibt ohne Anführungszeichen
        assert "/etc/nginx/nginx.conf" in captured["cmd"]
        assert "head -c 201" in captured["cmd"]  # max_bytes + 1
        assert result["content"] == "x" * 100
        assert result["truncated"] is False
        assert result["bytes"] == 100

    async def test_truncates_when_content_exceeds_max_bytes(self, env):
        async def fake_run(*args, **kwargs):
            # Simuliere: dd liefert max_bytes+1 Bytes zurück → Toolcode erkennt trunc
            return {"stdout": "y" * 201, "stderr": "", "exit_code": 0}

        with patch("hydrahive_core.target_resolution.run_ssh_command", side_effect=fake_run):
            result = await ServerFileReadTool().execute(
                agent_id="proj-a", project_id="proj-a",
                server_id="prod-web", path="/tmp/big", max_bytes=200,
            )
        assert result["truncated"] is True
        assert len(result["content"]) == 200

    async def test_quotes_path_with_special_chars(self, env):
        captured = {}

        async def fake_run(host, ssh_user, ssh_port, key_path, command, *, timeout):
            captured["cmd"] = command
            return {"stdout": "", "stderr": "", "exit_code": 0}

        with patch("hydrahive_core.target_resolution.run_ssh_command", side_effect=fake_run):
            await ServerFileReadTool().execute(
                agent_id="proj-a", project_id="proj-a",
                server_id="prod-web",
                path="/tmp/file with spaces & $VAR",
            )
        # shlex.quote muss den Pfad komplett einschliessen
        assert "'/tmp/file with spaces & $VAR'" in captured["cmd"]

    async def test_rejects_missing_path(self, env):
        result = await ServerFileReadTool().execute(
            agent_id="proj-a", project_id="proj-a",
            server_id="prod-web", path="",
        )
        assert "path fehlt" in result.get("error", "")

    async def test_max_bytes_clamped(self, env):
        captured = {}

        async def fake_run(host, ssh_user, ssh_port, key_path, command, *, timeout):
            captured["cmd"] = command
            return {"stdout": "", "stderr": "", "exit_code": 0}

        with patch("hydrahive_core.target_resolution.run_ssh_command", side_effect=fake_run):
            await ServerFileReadTool().execute(
                agent_id="proj-a", project_id="proj-a",
                server_id="prod-web", path="/tmp/x", max_bytes=99_999_999,
            )
        # Clamped auf 1_000_000 → head -c 1000001
        assert "head -c 1000001" in captured["cmd"]


# ═════════════════════════════════════════════════════ ServerFileWriteTool

class TestServerFileWriteTool:

    async def test_uses_base64_not_raw_shell(self, env):
        captured = {}

        async def fake_run(host, ssh_user, ssh_port, key_path, command, *, timeout):
            captured["cmd"] = command
            return {"stdout": "", "stderr": "", "exit_code": 0}

        # Evil content das direktes Shell-Quoten sprengen würde
        evil = "line1\n$VAR\"\\`;rm -rf /"

        with patch("hydrahive_core.target_resolution.run_ssh_command", side_effect=fake_run):
            result = await ServerFileWriteTool().execute(
                agent_id="proj-a", project_id="proj-a",
                server_id="prod-web", path="/tmp/f.txt", content=evil,
            )

        # Raw-Inhalt darf NICHT im Command erscheinen
        assert evil not in captured["cmd"]
        assert "$VAR" not in captured["cmd"].replace("printf ", "").replace("base64", "")  # nicht als Shell-Var
        # base64 + mv muss drin sein
        assert "base64 -d" in captured["cmd"]
        assert "mktemp" in captured["cmd"]
        assert "mv " in captured["cmd"]
        assert result["bytes"] == len(evil.encode("utf-8"))

    async def test_rejects_oversized_content(self, env):
        huge = "x" * (1_048_576 + 1)
        result = await ServerFileWriteTool().execute(
            agent_id="proj-a", project_id="proj-a",
            server_id="prod-web", path="/tmp/f", content=huge,
        )
        assert "nicht erlaubt" in result.get("error", "")
        assert result["exit_code"] == -1

    async def test_mode_validation_rejects_garbage(self, env):
        result = await ServerFileWriteTool().execute(
            agent_id="proj-a", project_id="proj-a",
            server_id="prod-web", path="/tmp/f", content="a", mode="foobar",
        )
        assert "Ungültiger mode" in result.get("error", "")

    async def test_mode_added_to_command(self, env):
        captured = {}

        async def fake_run(host, ssh_user, ssh_port, key_path, command, *, timeout):
            captured["cmd"] = command
            return {"stdout": "", "stderr": "", "exit_code": 0}

        with patch("hydrahive_core.target_resolution.run_ssh_command", side_effect=fake_run):
            await ServerFileWriteTool().execute(
                agent_id="proj-a", project_id="proj-a",
                server_id="prod-web", path="/tmp/f", content="a", mode="0644",
            )
        assert "chmod 0644" in captured["cmd"]

    async def test_write_propagates_remote_failure(self, env):
        async def fake_run(*args, **kwargs):
            return {"stdout": "", "stderr": "permission denied", "exit_code": 1}

        with patch("hydrahive_core.target_resolution.run_ssh_command", side_effect=fake_run):
            result = await ServerFileWriteTool().execute(
                agent_id="proj-a", project_id="proj-a",
                server_id="prod-web", path="/etc/passwd", content="nope",
            )
        assert result["exit_code"] == 1
        assert "permission denied" in result.get("error", "")
