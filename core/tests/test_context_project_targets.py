"""
test_context_project_targets.py — #584-A Prompt-Injektion mit Fallback.

Prüft die Channel-Belegung in orchestrator_context.build_system_prompt:
- v2-Projekt-Agent (boss_cfg.project_dir gesetzt) + targets vorhanden → Targets-Block.
- v2-Projekt-Agent + keine Targets → Legacy agent_servers-Block.
- Legacy-Agent (project_dir=None) → Legacy agent_servers-Block, egal ob Targets existieren.

Der eigentliche build_system_prompt ist umfangreich; statt ihn komplett
durchzuspielen, testen wir die Injection-Logik direkt an der Funktion
_load_project_targets / render_project_targets_for_prompt und simulieren den
Channel-Aufbau punktuell über eine Mini-Harness.
"""
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.project_targets import set_project_targets


@pytest.fixture
def targets_env(tmp_path, monkeypatch):
    targets_file = tmp_path / "project_targets.json"
    agent_servers_file = tmp_path / "agent_servers.json"
    srv_dir = tmp_path / "servers"
    srv_dir.mkdir()
    (srv_dir / "prod-web.json").write_text(
        '{"id":"prod-web","name":"Production Web","ip":"1.2.3.4",'
        '"ssh_user":"root","ssh_port":22,"description":"Legacy desc"}',
        encoding="utf-8",
    )
    (tmp_path / "wks_keys").mkdir()
    (tmp_path / "server_keys").mkdir()

    _users_config = tmp_path / "users.json"
    _wks_dir      = tmp_path / "wks_keys"
    _srv_keys_dir = tmp_path / "server_keys"

    class _FakeSettings:
        project_targets_config = targets_file
        users_config = _users_config
        wks_keys_dir = _wks_dir
        servers_dir = srv_dir
        agent_servers_config = agent_servers_file
        server_keys_dir = _srv_keys_dir

    monkeypatch.setattr("hydrahive_core.project_targets.settings", _FakeSettings)
    monkeypatch.setattr("hydrahive_core.router_servers.settings", _FakeSettings)
    monkeypatch.setattr("hydrahive_core.router_servers.SERVERS_DIR", srv_dir)
    monkeypatch.setattr("hydrahive_core.router_servers.AGENT_SERVERS_FILE", agent_servers_file)
    _FakeSettings.agent_servers_config = agent_servers_file  # für Zugriff aus Tests
    return _FakeSettings


def _simulate_context_injection(boss_cfg) -> tuple[str | None, bool]:
    """Repliziert den #584-A-Injection-Block aus orchestrator_context.py.
    Liefert (channel_text, targets_injected).
    """
    from hydrahive_core.project_targets import render_project_targets_for_prompt
    from hydrahive_core.router_servers import _load_agent_servers, _load_servers

    targets_injected = False
    channel_text: str | None = None

    if getattr(boss_cfg, "project_dir", None) is not None:
        block = render_project_targets_for_prompt(boss_cfg.id)
        if block:
            channel_text = block
            targets_injected = True

    if not targets_injected:
        agent_servers_map = _load_agent_servers()
        assigned_ids = agent_servers_map.get(boss_cfg.id, [])
        if assigned_ids:
            all_servers = {s["id"]: s for s in _load_servers()}
            lines = []
            for sid in assigned_ids:
                srv = all_servers.get(sid)
                if srv:
                    lines.append(
                        f"- **{srv.get('name', sid)}** (ID: `{sid}`): "
                        f"`{srv.get('ssh_user', '?')}@{srv.get('ip', '?')}:{srv.get('ssh_port', 22)}`"
                    )
            if lines:
                channel_text = "## Zugewiesene Remote-Server\n\n" + "\n".join(lines)

    return channel_text, targets_injected


class TestContextInjection:

    def test_injects_project_targets_when_set(self, targets_env):
        set_project_targets("proj-a", {
            "servers": [{"server_id": "prod-web", "role": "web", "note": "Frontend"}],
            "wks": [],
        })
        boss_cfg = SimpleNamespace(id="proj-a", project_dir=Path("/tmp/proj-a"))
        text, injected = _simulate_context_injection(boss_cfg)
        assert injected is True
        assert "Zugewiesene Zielsysteme" in text
        assert "role: `web`" in text
        assert "Frontend" in text

    def test_falls_back_to_agent_servers_when_project_targets_empty(self, targets_env):
        # Legacy-Zuweisung schreiben
        targets_env.agent_servers_config.write_text(
            '{"proj-a": ["prod-web"]}', encoding="utf-8",
        )
        boss_cfg = SimpleNamespace(id="proj-a", project_dir=Path("/tmp/proj-a"))
        text, injected = _simulate_context_injection(boss_cfg)
        assert injected is False
        assert "Zugewiesene Remote-Server" in text  # Legacy-Header
        assert "Production Web" in text

    def test_non_project_agent_uses_legacy(self, targets_env):
        """Legacy-Agent ohne project_dir darf NICHT Projekt-Targets lesen,
        selbst wenn eine project_targets.json denselben ID-Wert enthielte."""
        set_project_targets("legacy-agent", {
            "servers": [{"server_id": "prod-web", "role": "web"}], "wks": [],
        })
        targets_env.agent_servers_config.write_text(
            '{"legacy-agent": ["prod-web"]}', encoding="utf-8",
        )
        boss_cfg = SimpleNamespace(id="legacy-agent", project_dir=None)
        text, injected = _simulate_context_injection(boss_cfg)
        assert injected is False
        assert "Zugewiesene Remote-Server" in text  # Legacy-Header
        assert "Zugewiesene Zielsysteme" not in text

    def test_no_targets_no_legacy_returns_none(self, targets_env):
        boss_cfg = SimpleNamespace(id="proj-a", project_dir=Path("/tmp/proj-a"))
        text, injected = _simulate_context_injection(boss_cfg)
        assert injected is False
        assert text is None

    def test_targets_win_over_legacy_no_double_listing(self, targets_env):
        """Wenn Targets gesetzt sind, darf der Legacy-Block NICHT zusätzlich
        erscheinen — sonst hätte der Agent zwei widersprüchliche Listen."""
        set_project_targets("proj-a", {
            "servers": [{"server_id": "prod-web", "role": "web"}], "wks": [],
        })
        targets_env.agent_servers_config.write_text(
            '{"proj-a": ["prod-web"]}', encoding="utf-8",
        )
        boss_cfg = SimpleNamespace(id="proj-a", project_dir=Path("/tmp/proj-a"))
        text, injected = _simulate_context_injection(boss_cfg)
        assert injected is True
        # Nur der neue Header, nicht der Legacy-Header
        assert "Zugewiesene Zielsysteme" in text
        assert "Zugewiesene Remote-Server" not in text

    def test_prompt_never_injects_ssh_key_path(self, targets_env):
        set_project_targets("proj-a", {
            "servers": [{"server_id": "prod-web", "role": "web"}], "wks": [],
        })
        boss_cfg = SimpleNamespace(id="proj-a", project_dir=Path("/tmp/proj-a"))
        text, _ = _simulate_context_injection(boss_cfg)
        assert "ssh_key_path" not in (text or "")
        assert "-----BEGIN" not in (text or "")
