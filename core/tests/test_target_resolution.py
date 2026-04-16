"""
test_target_resolution.py — #584-C Resolver + SSH-Runner.

Deckt ab:
- resolve_server_target: Projekt-Target-Precedence, Legacy-Fallback, Key-Check.
- resolve_wks_target: Projekt-Pflicht, Defaulting, Multi-WKS, Key-Check.
- run_ssh_command: Build der SSH-Args, Key-Redaction, Truncation, Timeout.
- Fehlermeldungen leaken niemals ssh_key_path.
"""
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.target_resolution import (
    resolve_server_target,
    resolve_wks_target,
    run_ssh_command,
    TargetAccessError,
    MAX_SSH_OUTPUT,
)
from hydrahive_core.project_targets import set_project_targets


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Hängt Settings + router_servers auf tmp-Dateisystem um."""
    targets_file  = tmp_path / "project_targets.json"
    users_file    = tmp_path / "users.json"
    agent_srv     = tmp_path / "agent_servers.json"
    srv_dir       = tmp_path / "servers"
    srv_keys      = tmp_path / "server_keys"
    wks_keys      = tmp_path / "wks_keys"
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

    # Fake-Server + Key
    (srv_dir / "prod-web.json").write_text(json.dumps({
        "id": "prod-web", "name": "Production Web",
        "ip": "1.2.3.4", "ssh_user": "root", "ssh_port": 22,
        "description": "",
    }), encoding="utf-8")
    (srv_keys / "prod-web").write_text("FAKE-SRV-KEY", encoding="utf-8")

    # Fake-User + WKS + Key
    users_file.write_text(json.dumps({
        "till": {
            "wks": {"ip": "10.0.0.1", "ssh_user": "till", "ollama_port": 11434},
        },
        "alice": {
            "wks": {"ip": "10.0.0.2", "ssh_user": "alice", "ollama_port": 11434},
        },
        "ghost": {  # kein IP
            "wks": {"ip": "", "ssh_user": "ghost"},
        },
    }), encoding="utf-8")
    (wks_keys / "till").write_text("FAKE-WKS-KEY", encoding="utf-8")
    (wks_keys / "alice").write_text("FAKE-WKS-KEY", encoding="utf-8")

    return _S


# ═════════════════════════════════════════════════════ resolve_server_target

class TestResolveServer:

    def test_allows_project_target(self, env):
        set_project_targets("proj-a", {
            "servers": [{"server_id": "prod-web", "role": "web", "note": ""}],
            "wks": [],
        })
        r = resolve_server_target("proj-a", "prod-web", project_id="proj-a")
        assert r.server_id == "prod-web"
        assert r.ip == "1.2.3.4"
        assert r.ssh_user == "root"
        assert r.ssh_port == 22
        assert r.ssh_key_path == env.server_keys_dir / "prod-web"

    def test_rejects_unassigned(self, env):
        with pytest.raises(TargetAccessError, match="nicht zugewiesen"):
            resolve_server_target("proj-a", "prod-web", project_id="proj-a")

    def test_legacy_agent_servers_fallback(self, env):
        # Legacy: kein project-Target, aber Agent hat direkt Zuweisung
        env.agent_servers_config.write_text(
            json.dumps({"legacy-agent": ["prod-web"]}), encoding="utf-8",
        )
        r = resolve_server_target("legacy-agent", "prod-web", project_id=None)
        assert r.server_id == "prod-web"

    def test_project_targets_take_precedence(self, env):
        """Wenn project_targets gesetzt: Legacy ist irrelevant."""
        set_project_targets("proj-a", {
            "servers": [{"server_id": "prod-web", "role": "web", "note": ""}],
            "wks": [],
        })
        env.agent_servers_config.write_text(
            json.dumps({"proj-a": ["some-other-server"]}), encoding="utf-8",
        )
        # prod-web ist via project-target zugewiesen, nicht via legacy
        r = resolve_server_target("proj-a", "prod-web", project_id="proj-a")
        assert r.server_id == "prod-web"

    def test_rejects_when_key_missing(self, env):
        set_project_targets("proj-a", {
            "servers": [{"server_id": "prod-web", "role": "", "note": ""}],
            "wks": [],
        })
        (env.server_keys_dir / "prod-web").unlink()
        with pytest.raises(TargetAccessError, match="keinen SSH-Key"):
            resolve_server_target("proj-a", "prod-web", project_id="proj-a")

    def test_rejects_invalid_id(self, env):
        with pytest.raises(TargetAccessError, match="unerlaubte Zeichen"):
            resolve_server_target("proj-a", "../etc/passwd", project_id="proj-a")

    def test_rejects_missing_id(self, env):
        with pytest.raises(TargetAccessError, match="server_id fehlt"):
            resolve_server_target("proj-a", "", project_id="proj-a")

    def test_rejects_unknown_server_stammdaten(self, env):
        """server_id in targets, aber Stammdaten-File gelöscht."""
        set_project_targets("proj-a", {
            "servers": [{"server_id": "vanished", "role": "", "note": ""}],
            "wks": [],
        })
        with pytest.raises(TargetAccessError):
            resolve_server_target("proj-a", "vanished", project_id="proj-a")

    def test_project_targets_block_legacy_extra_server(self, env):
        """#584-C Security: wenn Projekt eine server-Zuweisung hat, darf eine
        alte Legacy agent_servers-Entry KEINEN zusätzlichen Server öffnen."""
        # Projekt erlaubt nur prod-web
        set_project_targets("proj-a", {
            "servers": [{"server_id": "prod-web", "role": "", "note": ""}],
            "wks": [],
        })
        # Legacy-Eintrag öffnet prod-db — darf nicht durchschlagen
        # Zweiten Server-Stammdatensatz anlegen, damit Legacy-Fallback nicht schon
        # am Stammdaten-Check scheitert.
        (env.servers_dir / "prod-db.json").write_text(
            '{"id":"prod-db","name":"DB","ip":"1.1.1.1","ssh_user":"root","ssh_port":22}',
            encoding="utf-8",
        )
        (env.server_keys_dir / "prod-db").write_text("K", encoding="utf-8")
        env.agent_servers_config.write_text(
            json.dumps({"proj-a": ["prod-db"]}), encoding="utf-8",
        )
        with pytest.raises(TargetAccessError, match="nicht zugewiesen"):
            resolve_server_target("proj-a", "prod-db", project_id="proj-a")

    def test_wks_only_project_targets_also_disable_legacy_server(self, env):
        """Wenn Projekt nur WKS (aber keine Server) zugewiesen hat, blockt
        das den Legacy-Fallback für server_shell trotzdem — sonst wäre der
        Targets-Block im Prompt irreführend (zeigt keine Server, Legacy würde
        heimlich welche erlauben)."""
        set_project_targets("proj-a", {
            "servers": [],
            "wks": [{"username": "till", "role": "dev", "note": ""}],
        })
        (env.server_keys_dir / "prod-web").write_text("K", encoding="utf-8")
        env.agent_servers_config.write_text(
            json.dumps({"proj-a": ["prod-web"]}), encoding="utf-8",
        )
        with pytest.raises(TargetAccessError, match="nicht zugewiesen"):
            resolve_server_target("proj-a", "prod-web", project_id="proj-a")


# ═════════════════════════════════════════════════════ resolve_wks_target

class TestResolveWks:

    def test_requires_project_id(self, env):
        with pytest.raises(TargetAccessError, match="Projektkontext"):
            resolve_wks_target("till", project_id=None)

    def test_single_defaults_username(self, env):
        set_project_targets("proj-a", {
            "servers": [],
            "wks": [{"username": "till", "role": "dev", "note": ""}],
        })
        r = resolve_wks_target(None, project_id="proj-a")
        assert r.username == "till"
        assert r.ip == "10.0.0.1"
        assert r.ssh_port == 22

    def test_multiple_requires_username(self, env):
        set_project_targets("proj-a", {
            "servers": [],
            "wks": [
                {"username": "till", "role": "", "note": ""},
                {"username": "alice", "role": "", "note": ""},
            ],
        })
        with pytest.raises(TargetAccessError, match="username erforderlich"):
            resolve_wks_target(None, project_id="proj-a")

    def test_multiple_with_explicit_username_ok(self, env):
        set_project_targets("proj-a", {
            "servers": [],
            "wks": [
                {"username": "till", "role": "", "note": ""},
                {"username": "alice", "role": "", "note": ""},
            ],
        })
        r = resolve_wks_target("alice", project_id="proj-a")
        assert r.username == "alice"
        assert r.ip == "10.0.0.2"

    def test_rejects_foreign_username(self, env):
        set_project_targets("proj-a", {
            "servers": [],
            "wks": [{"username": "till", "role": "", "note": ""}],
        })
        with pytest.raises(TargetAccessError, match="nicht zugewiesen"):
            resolve_wks_target("alice", project_id="proj-a")

    def test_rejects_unconfigured_ip(self, env):
        """User mit wks.ip='' — obwohl zugewiesen, Resolver muss 400-like erzeugen."""
        set_project_targets("proj-a", {
            "servers": [],
            "wks": [{"username": "ghost", "role": "", "note": ""}],
        })
        with pytest.raises(TargetAccessError, match="nicht konfiguriert"):
            resolve_wks_target("ghost", project_id="proj-a")

    def test_rejects_when_key_missing(self, env):
        set_project_targets("proj-a", {
            "servers": [],
            "wks": [{"username": "till", "role": "", "note": ""}],
        })
        (env.wks_keys_dir / "till").unlink()
        with pytest.raises(TargetAccessError, match="keinen SSH-Key"):
            resolve_wks_target("till", project_id="proj-a")

    def test_rejects_when_no_wks_assigned(self, env):
        with pytest.raises(TargetAccessError, match="Keine WKS"):
            resolve_wks_target(None, project_id="proj-a")


# ═════════════════════════════════════════════════════ Error-Message-Safety

class TestErrorMessagesNeverLeakKeyPath:

    def test_server_resolver_error_has_no_key_path(self, env):
        """Resolver darf Key-Pfad nicht in Fehlermeldung durchreichen."""
        try:
            resolve_server_target("proj-a", "nope", project_id="proj-a")
            pytest.fail("sollte fehlschlagen")
        except TargetAccessError as e:
            msg = str(e)
            assert "server_keys" not in msg
            assert str(env.server_keys_dir) not in msg

    def test_wks_resolver_error_has_no_key_path(self, env):
        try:
            resolve_wks_target("nobody", project_id="proj-a")
            pytest.fail("sollte fehlschlagen")
        except TargetAccessError as e:
            assert "wks_keys" not in str(e)
            assert str(env.wks_keys_dir) not in str(e)


# ═════════════════════════════════════════════════════ run_ssh_command

class TestRunSshCommand:

    async def test_builds_ssh_args_with_key(self, env):
        captured_args = []

        async def fake_exec(*args, **kwargs):
            captured_args.extend(args)
            proc = MagicMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"hello\n", b""))
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await run_ssh_command(
                "1.2.3.4", "root", 22,
                env.server_keys_dir / "prod-web",
                "echo hello", timeout=5,
            )

        assert result == {"stdout": "hello\n", "stderr": "", "exit_code": 0}
        assert "ssh" in captured_args
        assert "-i" in captured_args
        assert str(env.server_keys_dir / "prod-web") in captured_args
        assert "BatchMode=yes" in captured_args
        assert "StrictHostKeyChecking=no" in captured_args
        assert "ConnectTimeout=10" in captured_args

    async def test_redacts_key_path_in_stderr(self, env):
        key = env.server_keys_dir / "prod-web"

        async def fake_exec(*args, **kwargs):
            proc = MagicMock()
            proc.returncode = 1
            proc.communicate = AsyncMock(return_value=(
                b"", f"ssh: Permission denied (publickey). Identity file {key}".encode("utf-8"),
            ))
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await run_ssh_command(
                "1.2.3.4", "root", 22, key, "true", timeout=5,
            )
        assert str(key) not in result["stderr"]
        assert "<ssh_key>" in result["stderr"]

    async def test_timeout_kills_proc(self, env):
        async def fake_exec(*args, **kwargs):
            proc = MagicMock()
            proc.returncode = None
            proc.kill = MagicMock()
            # Simuliere hängenden Prozess: communicate() wartet ewig
            import asyncio
            async def _never():
                await asyncio.sleep(60)
            proc.communicate = _never
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await run_ssh_command(
                "1.2.3.4", "root", 22,
                env.server_keys_dir / "prod-web",
                "sleep 60", timeout=1,
            )
        assert "Timeout" in result.get("error", "")
        assert result["exit_code"] == -1

    async def test_truncates_long_output(self, env):
        huge = b"x" * (MAX_SSH_OUTPUT + 5000)

        async def fake_exec(*args, **kwargs):
            proc = MagicMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(huge, b""))
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await run_ssh_command(
                "1.2.3.4", "root", 22,
                env.server_keys_dir / "prod-web",
                "cat big", timeout=5,
            )
        assert "gekürzt" in result["stdout"]
        # Total-Länge = MAX_SSH_OUTPUT + Suffix (Zeichen), auf jeden Fall > MAX_SSH_OUTPUT
        assert len(result["stdout"]) > MAX_SSH_OUTPUT
        # Aber nicht die komplette Originallänge
        assert len(result["stdout"]) < MAX_SSH_OUTPUT + 500

    async def test_max_output_none_does_not_truncate_stdout(self, env):
        """#670: max_output=None → Runner kappt stdout nicht, Aufrufer begrenzt
        selbst per Remote-Command."""
        huge = b"y" * (MAX_SSH_OUTPUT + 20_000)

        async def fake_exec(*args, **kwargs):
            proc = MagicMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(huge, b""))
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await run_ssh_command(
                "1.2.3.4", "root", 22,
                env.server_keys_dir / "prod-web",
                "dd if=/big bs=1 count=100000", timeout=5,
                max_output=None,
            )
        # Komplette Länge erhalten — keine "gekürzt"-Marker
        assert "gekürzt" not in result["stdout"]
        assert len(result["stdout"]) == MAX_SSH_OUTPUT + 20_000

    async def test_max_output_none_still_truncates_stderr(self, env):
        """#670: stderr bleibt bei MAX_SSH_OUTPUT gekappt auch wenn
        max_output=None — Diagnose-Kanal soll nie riesig werden."""
        huge_err = b"e" * (MAX_SSH_OUTPUT + 5000)

        async def fake_exec(*args, **kwargs):
            proc = MagicMock()
            proc.returncode = 0
            proc.communicate = AsyncMock(return_value=(b"ok", huge_err))
            return proc

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await run_ssh_command(
                "1.2.3.4", "root", 22,
                env.server_keys_dir / "prod-web",
                "x", timeout=5, max_output=None,
            )
        assert "gekürzt" in result["stderr"]
        assert len(result["stderr"]) < MAX_SSH_OUTPUT + 500

    async def test_exception_redacts_key(self, env):
        key = env.server_keys_dir / "prod-web"

        async def fake_exec(*args, **kwargs):
            raise OSError(f"boom with {key} in message")

        with patch("asyncio.create_subprocess_exec", side_effect=fake_exec):
            result = await run_ssh_command("h", "u", 22, key, "x", timeout=5)
        assert str(key) not in result.get("error", "")
        assert "<ssh_key>" in result.get("error", "")
