"""
test_security.py — Sicherheits-kritische Unit-Tests

Deckt ab:
- Path-Traversal-Schutz (tool_registry.assert_path_within_project)
- Password-Hashing (PBKDF2, Salt-Entropie, Legacy-Kompatibilität)
- Discord Circuit Breaker Loop-Detektion
- Matrix Circuit Breaker Loop-Detektion
"""
import sys
import time
from pathlib import Path
import pytest

# Sicherstellen dass src/ im Pfad ist
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


# ======================================================= Path Safety

from hydrahive_core.tool_registry import assert_path_within_project, PathSafetyError


def test_path_within_project_erlaubt(tmp_path):
    """Dateien innerhalb des Projekt-Verzeichnisses sind erlaubt."""
    project_id = "testprojekt"
    proj_dir = tmp_path / project_id
    proj_dir.mkdir()

    # Normaler relativer Pfad
    result = assert_path_within_project("datei.txt", project_id)
    assert "datei.txt" in str(result)


def test_path_traversal_wird_geblockt(tmp_path):
    """../.. Traversal wird als PathSafetyError geworfen."""
    from hydrahive_core import tool_registry as tr
    # PROJECTS_ROOT auf tmp_path umlenken für Test
    original = tr.PROJECTS_ROOT
    tr.PROJECTS_ROOT = tmp_path
    project_id = "testprojekt"
    (tmp_path / project_id).mkdir()
    try:
        with pytest.raises(PathSafetyError):
            assert_path_within_project("../../etc/passwd", project_id)
    finally:
        tr.PROJECTS_ROOT = original


def test_absoluter_pfad_ausserhalb_geblockt(tmp_path):
    """Absoluter Pfad außerhalb des Projektverzeichnisses wird geblockt."""
    from hydrahive_core import tool_registry as tr
    original = tr.PROJECTS_ROOT
    tr.PROJECTS_ROOT = tmp_path
    project_id = "testprojekt"
    (tmp_path / project_id).mkdir()
    try:
        with pytest.raises(PathSafetyError):
            assert_path_within_project("/etc/passwd", project_id)
    finally:
        tr.PROJECTS_ROOT = original


def test_verschachtelter_pfad_erlaubt(tmp_path):
    """Tiefe Verschachtelung innerhalb des Projekts ist OK."""
    from hydrahive_core import tool_registry as tr
    original = tr.PROJECTS_ROOT
    tr.PROJECTS_ROOT = tmp_path
    project_id = "testprojekt"
    (tmp_path / project_id / "subdir" / "tief").mkdir(parents=True)
    try:
        result = assert_path_within_project("subdir/tief/datei.txt", project_id)
        assert project_id in str(result)
    finally:
        tr.PROJECTS_ROOT = original


# ======================================================= Password Hashing

from hydrahive_core.auth_utils import hash_password as _hash_pw, verify_password as _verify_pw


def test_password_hash_pbkdf2b_format():
    """Neues pbkdf2b-Format wird verwendet (Salt als echte Bytes)."""
    stored = _hash_pw("testpasswort")
    assert stored.startswith("pbkdf2b:"), f"Falsches Format: {stored[:20]}"
    parts = stored.split(":")
    assert len(parts) == 3
    salt_hex = parts[1]
    assert len(salt_hex) == 32
    bytes.fromhex(salt_hex)  # Wirft ValueError wenn kein gültiges Hex


def test_password_verify_korrekt():
    """Korrektes Passwort wird verifiziert."""
    stored = _hash_pw("meingeheimespasswort")
    assert _verify_pw("meingeheimespasswort", stored) is True


def test_password_verify_falsch():
    """Falsches Passwort wird abgelehnt."""
    stored = _hash_pw("richtig")
    assert _verify_pw("falsch", stored) is False


def test_password_salts_sind_unterschiedlich():
    """Zwei Hashes des gleichen Passworts haben unterschiedliche Salts."""
    h1 = _hash_pw("gleichespasswort")
    h2 = _hash_pw("gleichespasswort")
    assert h1.split(":")[1] != h2.split(":")[1], "Salts müssen zufällig sein!"


def test_legacy_pbkdf2_noch_verifizierbar():
    """Alte pbkdf2-Hashes (ASCII-Salt) werden noch korrekt verifiziert."""
    import hashlib, secrets
    salt = secrets.token_hex(16)
    h = hashlib.pbkdf2_hmac("sha256", "altespasswort".encode(), salt.encode(), 260_000)
    stored = f"pbkdf2:{salt}:{h.hex()}"
    assert _verify_pw("altespasswort", stored) is True
    assert _verify_pw("falsch", stored) is False


# ======================================================= Discord Circuit Breaker

def _make_discord_client():
    """Erstellt einen minimalen DiscordAgentClient für Tests (ohne echten Discord-Connect)."""
    from hydrahive_core.discord_agent import DiscordAgentClient

    class TestDiscordClient(DiscordAgentClient):
        async def on_user_message(self, message):
            pass

    client = TestDiscordClient.__new__(TestDiscordClient)
    import collections
    client.loop_detection = True
    client.loop_bot_threshold = 3
    client.loop_pingpong_seconds = 30
    client.loop_cooldown_seconds = 300
    client._loop_history = {}
    client._circuit_open = {}
    client.agent_id = "test_agent"
    client.LOOP_HISTORY_SIZE = 20
    client.LOOP_PINGPONG_THRESHOLD = 4
    return client


def test_discord_mensch_nie_geblockt():
    """Menschen werden nie geblockt, auch wenn Circuit offen ist."""
    client = _make_discord_client()
    # Circuit manuell öffnen
    client._circuit_open["ch1"] = time.monotonic()
    assert client._check_loop("ch1", is_bot=False) is False


def test_discord_bot_threshold():
    """Nach loop_bot_threshold Bot-Nachrichten wird Circuit geöffnet."""
    client = _make_discord_client()
    # Erste N-1 Nachrichten: kein Block
    for _ in range(client.loop_bot_threshold - 1):
        result = client._check_loop("ch1", is_bot=True)
        assert result is False
    # Threshold-te Nachricht: Block!
    assert client._check_loop("ch1", is_bot=True) is True


def test_discord_circuit_bleibt_offen_fuer_bots():
    """Circuit bleibt für Bots offen, auch wenn Mensch schreibt."""
    client = _make_discord_client()
    client._circuit_open["ch1"] = time.monotonic()  # Circuit offen

    client._check_loop("ch1", is_bot=False)  # Mensch schreibt
    # Circuit muss noch offen sein
    assert "ch1" in client._circuit_open


def test_discord_circuit_schliesst_nach_cooldown():
    """Circuit schließt automatisch nach Ablauf des Cooldowns."""
    client = _make_discord_client()
    client.loop_cooldown_seconds = 0.01  # sehr kurzer Cooldown für Test
    client._circuit_open["ch1"] = time.monotonic() - 1  # schon abgelaufen

    result = client._check_loop("ch1", is_bot=True)
    # Circuit war abgelaufen → geschlossen, neue Nachricht zählt als erste
    assert result is False
    assert "ch1" not in client._circuit_open


# ======================================================= Matrix Circuit Breaker

def _make_matrix_agent():
    """Erstellt einen minimalen MatrixAgent für Tests."""
    import collections
    from hydrahive_core.matrix_agent import MatrixAgent

    class TestAgent(MatrixAgent):
        async def on_user_message(self, room, text, sender):
            pass

    from unittest.mock import MagicMock
    cfg = MagicMock()
    cfg.id = "test_boss"
    agent = TestAgent.__new__(TestAgent)
    agent.config = cfg
    agent._mxid = "@test_boss:testserver"
    agent.loop_detection = True
    agent.loop_bot_threshold = 3
    agent.loop_pingpong_seconds = 30
    agent.loop_cooldown_seconds = 300
    agent._loop_history = {}
    agent._circuit_open = {}
    agent.LOOP_HISTORY_SIZE = 20
    agent.LOOP_PINGPONG_THRESHOLD = 4
    return agent


def test_matrix_mensch_nie_geblockt():
    agent = _make_matrix_agent()
    agent._circuit_open["!room1:server"] = time.monotonic()
    assert agent._check_loop("!room1:server", is_bot=False) is False


def test_matrix_bot_threshold():
    agent = _make_matrix_agent()
    for _ in range(agent.loop_bot_threshold - 1):
        assert agent._check_loop("!room1:server", is_bot=True) is False
    assert agent._check_loop("!room1:server", is_bot=True) is True


def test_matrix_circuit_bleibt_bei_mensch_offen():
    agent = _make_matrix_agent()
    agent._circuit_open["!room1:server"] = time.monotonic()
    agent._check_loop("!room1:server", is_bot=False)
    assert "!room1:server" in agent._circuit_open
