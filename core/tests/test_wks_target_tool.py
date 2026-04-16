"""
test_wks_target_tool.py — #584-C WksShellExecTool.

Deckt:
- Default-Username bei genau 1 zugewiesener WKS.
- Explizite username-Pflicht bei mehreren WKS.
- Rejection ohne project_id.
- SSH-Run nutzt Port 22 + WKS-Key.
"""
import json
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.tool_registry import WksShellExecTool
from hydrahive_core.project_targets import set_project_targets


@pytest.fixture
def env(tmp_path, monkeypatch):
    targets_file = tmp_path / "project_targets.json"
    users_file   = tmp_path / "users.json"
    wks_keys     = tmp_path / "wks_keys"
    srv_dir      = tmp_path / "servers"
    srv_keys     = tmp_path / "server_keys"
    for p in (wks_keys, srv_dir, srv_keys):
        p.mkdir(parents=True, exist_ok=True)

    class _S:
        project_targets_config = targets_file
        users_config = users_file
        wks_keys_dir = wks_keys
        servers_dir = srv_dir
        server_keys_dir = srv_keys
        agent_servers_config = tmp_path / "agent_servers.json"
        llm_env = tmp_path / "llm.env"

    monkeypatch.setattr("hydrahive_core.project_targets.settings", _S)
    monkeypatch.setattr("hydrahive_core.target_resolution.settings", _S)

    users_file.write_text(json.dumps({
        "till":  {"wks": {"ip": "10.0.0.1", "ssh_user": "till"}},
        "alice": {"wks": {"ip": "10.0.0.2", "ssh_user": "alice"}},
    }), encoding="utf-8")
    (wks_keys / "till").write_text("K", encoding="utf-8")
    (wks_keys / "alice").write_text("K", encoding="utf-8")
    return _S


class TestWksShellExecTool:

    async def test_uses_default_username_when_single(self, env):
        set_project_targets("p1", {
            "servers": [],
            "wks": [{"username": "till", "role": "dev", "note": ""}],
        })
        captured = {}

        async def fake_run(host, ssh_user, ssh_port, key_path, command, *, timeout):
            captured.update({"host": host, "port": ssh_port, "key": key_path, "cmd": command})
            return {"stdout": "", "stderr": "", "exit_code": 0}

        with patch("hydrahive_core.target_resolution.run_ssh_command", side_effect=fake_run):
            result = await WksShellExecTool().execute(
                agent_id="p1", project_id="p1", command="whoami",
            )

        assert captured["host"] == "10.0.0.1"
        assert captured["port"] == 22
        assert captured["key"] == env.wks_keys_dir / "till"
        assert captured["cmd"] == "whoami"
        assert result["username"] == "till"

    async def test_requires_explicit_username_when_multiple(self, env):
        set_project_targets("p1", {
            "servers": [],
            "wks": [
                {"username": "till",  "role": "", "note": ""},
                {"username": "alice", "role": "", "note": ""},
            ],
        })
        result = await WksShellExecTool().execute(
            agent_id="p1", project_id="p1", command="x",
        )
        assert "username erforderlich" in result.get("error", "")
        assert result["exit_code"] == -1

    async def test_rejects_without_project_context(self, env):
        result = await WksShellExecTool().execute(
            agent_id="x", project_id="", command="x",
        )
        assert "Projektkontext" in result.get("error", "")

    async def test_rejects_foreign_username(self, env):
        set_project_targets("p1", {
            "servers": [],
            "wks": [{"username": "till", "role": "", "note": ""}],
        })
        result = await WksShellExecTool().execute(
            agent_id="p1", project_id="p1", command="x", username="alice",
        )
        assert "nicht zugewiesen" in result.get("error", "")

    async def test_never_leaks_ssh_key_path(self, env):
        result = await WksShellExecTool().execute(
            agent_id="p1", project_id="p1", command="x",
        )
        # Keine WKS zugewiesen → Fehler. Darf keinen Pfad enthalten.
        assert "wks_keys" not in result.get("error", "")
        assert str(env.wks_keys_dir) not in result.get("error", "")
