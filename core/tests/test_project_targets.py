"""
test_project_targets.py — #584-A Storage + Validierung + API-Shape.

Deckt die Backend-Helper aus project_targets.py sowie die
Validierungs-/Response-Contracts ab. FastAPI-Routes selbst werden separat
getestet (wenn testclient verfügbar ist); hier liegt der Fokus auf den
testbaren Helpern und der API-Shape.
"""
import json
import sys
from pathlib import Path
from unittest.mock import patch
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.project_targets import (
    get_project_targets,
    set_project_targets,
    _validate_and_normalize,
    render_project_targets_for_prompt,
    TargetValidationError,
    MAX_ROLE_LEN,
    MAX_NOTE_LEN,
    MAX_SERVERS,
    MAX_WKS,
)


@pytest.fixture
def targets_file(tmp_path, monkeypatch):
    """Hängt settings.project_targets_config auf eine tmp-Datei um."""
    f = tmp_path / "project_targets.json"

    class _FakeSettings:
        project_targets_config = f
        users_config = tmp_path / "users.json"
        wks_keys_dir = tmp_path / "wks_keys"

    # Existiert-Flag simulieren
    (tmp_path / "wks_keys").mkdir(exist_ok=True)

    monkeypatch.setattr("hydrahive_core.project_targets.settings", _FakeSettings)
    return f


# ================================================================= Storage

class TestStorage:

    def test_empty_project_targets_returns_empty_lists(self, targets_file):
        result = get_project_targets("does-not-exist")
        assert result == {"servers": [], "wks": []}

    def test_put_and_get_multiple_servers_with_roles_and_notes(self, targets_file):
        set_project_targets("proj-a", {
            "servers": [
                {"server_id": "prod-web", "role": "web", "note": "Frontend + API"},
                {"server_id": "prod-db",  "role": "database", "note": "Postgres"},
            ],
            "wks": [{"username": "till", "role": "local-dev", "note": "Testmaschine"}],
        })
        result = get_project_targets("proj-a")
        assert len(result["servers"]) == 2
        assert result["servers"][0] == {"server_id": "prod-web", "role": "web", "note": "Frontend + API"}
        assert result["servers"][1]["role"] == "database"
        assert result["wks"] == [{"username": "till", "role": "local-dev", "note": "Testmaschine"}]

    def test_write_chmod_0600(self, targets_file):
        set_project_targets("proj-a", {"servers": [{"server_id": "s1"}], "wks": []})
        assert oct(targets_file.stat().st_mode)[-3:] == "600"

    def test_targets_for_different_projects_isolated(self, targets_file):
        set_project_targets("a", {"servers": [{"server_id": "s1"}], "wks": []})
        set_project_targets("b", {"servers": [{"server_id": "s2"}], "wks": []})
        assert get_project_targets("a")["servers"][0]["server_id"] == "s1"
        assert get_project_targets("b")["servers"][0]["server_id"] == "s2"


# ================================================================= Validierung

class TestValidation:

    def test_rejects_non_dict_body(self):
        with pytest.raises(TargetValidationError):
            _validate_and_normalize([])

    def test_rejects_non_list_servers(self):
        with pytest.raises(TargetValidationError):
            _validate_and_normalize({"servers": "oops", "wks": []})

    def test_rejects_duplicate_server_id(self):
        with pytest.raises(TargetValidationError, match="Duplikat"):
            _validate_and_normalize({
                "servers": [{"server_id": "s1"}, {"server_id": "s1"}],
                "wks": [],
            })

    def test_rejects_duplicate_username(self):
        with pytest.raises(TargetValidationError, match="Duplikat"):
            _validate_and_normalize({
                "servers": [],
                "wks": [{"username": "till"}, {"username": "till"}],
            })

    def test_rejects_missing_server_id(self):
        with pytest.raises(TargetValidationError, match="server_id fehlt"):
            _validate_and_normalize({"servers": [{"role": "web"}], "wks": []})

    def test_rejects_missing_username(self):
        with pytest.raises(TargetValidationError, match="username fehlt"):
            _validate_and_normalize({"servers": [], "wks": [{"role": "dev"}]})

    def test_role_length_validation(self):
        long_role = "a" * (MAX_ROLE_LEN + 1)
        with pytest.raises(TargetValidationError, match="zu lang"):
            _validate_and_normalize({
                "servers": [{"server_id": "s1", "role": long_role}],
                "wks": [],
            })

    def test_role_invalid_chars_rejected(self):
        with pytest.raises(TargetValidationError, match="ungültige Zeichen"):
            _validate_and_normalize({
                "servers": [{"server_id": "s1", "role": "Web Server"}],  # Leerzeichen + Caps
                "wks": [],
            })

    def test_empty_role_is_ok(self):
        result = _validate_and_normalize({
            "servers": [{"server_id": "s1", "role": ""}],
            "wks": [],
        })
        assert result["servers"][0]["role"] == ""

    def test_note_length_validation(self):
        long_note = "x" * (MAX_NOTE_LEN + 1)
        with pytest.raises(TargetValidationError, match="zu lang"):
            _validate_and_normalize({
                "servers": [{"server_id": "s1", "note": long_note}],
                "wks": [],
            })

    def test_too_many_servers_rejected(self):
        with pytest.raises(TargetValidationError, match=f"{MAX_SERVERS}"):
            _validate_and_normalize({
                "servers": [{"server_id": f"s{i}"} for i in range(MAX_SERVERS + 1)],
                "wks": [],
            })

    def test_too_many_wks_rejected(self):
        with pytest.raises(TargetValidationError, match=f"{MAX_WKS}"):
            _validate_and_normalize({
                "servers": [],
                "wks": [{"username": f"u{i}"} for i in range(MAX_WKS + 1)],
            })

    def test_normalized_output_strips_extra_fields(self):
        """Eingabe-Felder außerhalb des Schemas dürfen NICHT persistiert werden."""
        result = _validate_and_normalize({
            "servers": [{
                "server_id": "s1", "role": "web", "note": "",
                "ssh_key": "leak", "ip": "1.2.3.4",  # böse Extra-Felder
            }],
            "wks": [],
        })
        assert set(result["servers"][0].keys()) == {"server_id", "role", "note"}
        assert "ssh_key" not in result["servers"][0]
        assert "ip" not in result["servers"][0]


# ================================================================= Prompt-Rendering

class TestPromptRendering:

    def test_no_targets_returns_none(self, targets_file):
        assert render_project_targets_for_prompt("empty-proj") is None

    def test_includes_role_and_note(self, targets_file):
        set_project_targets("p1", {
            "servers": [{"server_id": "prod-web", "role": "web", "note": "Frontend"}],
            "wks": [],
        })
        lookup = {"prod-web": {"id": "prod-web", "name": "Production Web",
                                "ip": "1.2.3.4", "ssh_port": 22, "ssh_user": "root"}}
        out = render_project_targets_for_prompt("p1", server_lookup=lookup, users={})
        assert "Zugewiesene Zielsysteme" in out
        assert "Production Web" in out
        assert "role: `web`" in out
        assert "Frontend" in out
        assert "root@1.2.3.4:22" in out

    def test_never_injects_ssh_key_path(self, targets_file):
        set_project_targets("p1", {
            "servers": [{"server_id": "s1", "role": "web"}],
            "wks":     [{"username": "till", "role": "dev"}],
        })
        lookup = {"s1": {"id": "s1", "name": "X", "ip": "1.1.1.1",
                         "ssh_port": 22, "ssh_user": "root",
                         "ssh_key_path": "/etc/hydrahive/server_keys/s1"}}
        users = {"till": {"wks": {
            "ip": "10.0.0.1", "ssh_user": "till",
            "ssh_key_path": "/etc/hydrahive/wks_keys/till",
        }}}
        out = render_project_targets_for_prompt("p1", server_lookup=lookup, users=users)
        assert "ssh_key_path" not in out
        assert "server_keys" not in out
        assert "wks_keys" not in out
        assert "-----BEGIN" not in out

    def test_only_server_section_when_no_wks(self, targets_file):
        set_project_targets("p1", {"servers": [{"server_id": "s1"}], "wks": []})
        lookup = {"s1": {"id": "s1", "name": "X", "ip": "1.1.1.1", "ssh_port": 22, "ssh_user": "root"}}
        out = render_project_targets_for_prompt("p1", server_lookup=lookup, users={})
        assert "### Root-/Remote-Server" in out
        assert "### WKS" not in out

    def test_only_wks_section_when_no_servers(self, targets_file):
        set_project_targets("p1", {
            "servers": [],
            "wks": [{"username": "till", "role": "dev"}],
        })
        users = {"till": {"wks": {"ip": "10.0.0.1", "ssh_user": "till"}}}
        out = render_project_targets_for_prompt("p1", server_lookup={}, users=users)
        assert "### WKS" in out
        assert "### Root-/Remote-Server" not in out
        assert "10.0.0.1:22" in out  # #584-A nutzt Default-WKS-Port 22

    def test_stale_server_reference_is_skipped(self, targets_file):
        """Server in targets, aber nicht mehr in server_lookup → Zeile wird
        übersprungen (nicht als Fehler gerendert)."""
        set_project_targets("p1", {
            "servers": [{"server_id": "vanished", "role": "x"}],
            "wks": [],
        })
        out = render_project_targets_for_prompt("p1", server_lookup={}, users={})
        assert out is None  # keine renderbaren Einträge → None

    def test_wks_without_ip_is_skipped(self, targets_file):
        set_project_targets("p1", {
            "servers": [],
            "wks": [{"username": "ghost", "role": "dev"}],
        })
        users = {"ghost": {"wks": {"ip": "", "ssh_user": "ghost"}}}
        out = render_project_targets_for_prompt("p1", server_lookup={}, users=users)
        assert out is None
