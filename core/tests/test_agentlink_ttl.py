"""
test_agentlink_ttl.py — Tests für AgentLink TTL / expire / consume Logik
"""
import json
import time
import pytest
from pathlib import Path
from hydrahive_core.agentlink import write_handoff, read_handoff, cleanup_expired


@pytest.fixture
def project_dir(tmp_path):
    return tmp_path


class TestWriteHandoff:
    def test_schreibt_json_datei(self, project_dir):
        write_handoff(project_dir, "agent_a", "agent_b", data={"key": "val"})
        files = list((project_dir / "agentlink").glob("*.json"))
        assert len(files) == 1

    def test_gibt_dict_mit_id_zurueck(self, project_dir):
        entry = write_handoff(project_dir, "a", "b")
        assert isinstance(entry, dict)
        assert "id" in entry

    def test_inhalt_korrekt(self, project_dir):
        write_handoff(project_dir, "from_a", "to_b", data={"task": "test"}, ttl_seconds=60)
        f = list((project_dir / "agentlink").glob("*.json"))[0]
        saved = json.loads(f.read_text())
        assert saved["from_agent"] == "from_a"
        assert saved["to_agent"] == "to_b"
        assert saved["data"] == {"task": "test"}
        assert "expires_at" in saved

    def test_ohne_ziel_agent(self, project_dir):
        entry = write_handoff(project_dir, "sender", None, data={"info": 1})
        assert entry["to_agent"] is None

    def test_context_string(self, project_dir):
        write_handoff(project_dir, "a", "b", context="Aufgabe X")
        f = list((project_dir / "agentlink").glob("*.json"))[0]
        saved = json.loads(f.read_text())
        assert saved["context"] == "Aufgabe X"


class TestReadHandoff:
    def test_liest_handoff(self, project_dir):
        write_handoff(project_dir, "a", "b", data={"x": 1}, ttl_seconds=60)
        result = read_handoff(project_dir, to_agent="b")
        assert result is not None
        assert result["data"] == {"x": 1}

    def test_consume_true_loescht_datei(self, project_dir):
        write_handoff(project_dir, "a", "b", ttl_seconds=60)
        read_handoff(project_dir, to_agent="b", consume=True)
        files = list((project_dir / "agentlink").glob("*.json"))
        assert len(files) == 0

    def test_consume_false_behaelt_datei(self, project_dir):
        write_handoff(project_dir, "a", "b", ttl_seconds=60)
        read_handoff(project_dir, to_agent="b", consume=False)
        files = list((project_dir / "agentlink").glob("*.json"))
        assert len(files) == 1

    def test_abgelaufener_handoff_wird_ignoriert(self, project_dir):
        write_handoff(project_dir, "a", "b", data={"old": True}, ttl_seconds=0)
        time.sleep(0.01)
        result = read_handoff(project_dir, to_agent="b")
        assert result is None

    def test_kein_handoff_gibt_none(self, project_dir):
        result = read_handoff(project_dir, to_agent="nobody")
        assert result is None

    def test_falscher_empfaenger_wird_ignoriert(self, project_dir):
        write_handoff(project_dir, "a", "b", ttl_seconds=60)
        result = read_handoff(project_dir, to_agent="c")
        assert result is None

    def test_ohne_empfaenger_filter_liest_alle(self, project_dir):
        write_handoff(project_dir, "a", "b", data={"n": 1}, ttl_seconds=60)
        result = read_handoff(project_dir, to_agent=None)
        assert result is not None


class TestCleanupExpired:
    def test_loescht_abgelaufene(self, project_dir):
        write_handoff(project_dir, "a", "b", ttl_seconds=0)
        time.sleep(0.01)
        count = cleanup_expired(project_dir)
        assert count == 1
        assert len(list((project_dir / "agentlink").glob("*.json"))) == 0

    def test_behaelt_aktive(self, project_dir):
        write_handoff(project_dir, "a", "b", ttl_seconds=3600)
        count = cleanup_expired(project_dir)
        assert count == 0
        assert len(list((project_dir / "agentlink").glob("*.json"))) == 1

    def test_leeres_verzeichnis(self, project_dir):
        count = cleanup_expired(project_dir)
        assert count == 0

    def test_mehrere_gemischt(self, project_dir):
        write_handoff(project_dir, "a", "b", data={"n": 1}, ttl_seconds=0)
        write_handoff(project_dir, "a", "b", data={"n": 2}, ttl_seconds=3600)
        time.sleep(0.01)
        count = cleanup_expired(project_dir)
        assert count == 1
        remaining = list((project_dir / "agentlink").glob("*.json"))
        assert len(remaining) == 1
        data = json.loads(remaining[0].read_text())
        assert data["data"]["n"] == 2
