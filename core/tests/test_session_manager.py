"""
test_session_manager.py — SessionManager Unit-Tests (#395 SQLite)

Testet:
- Neue Session erstellen
- Nachrichten anhängen (async)
- Parallele Appends (Race Condition Schutz via Lock)
- Context-Kompaktierung
- SQLite Persist & Reload
- Usage-Stats aus DB
"""
import asyncio
import sqlite3
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.session_manager import SessionManager, MessageRole


@pytest.fixture
def sm(tmp_path):
    mgr = SessionManager(projects_dir=tmp_path)
    mgr.start()  # DB initialisieren
    return mgr


@pytest.mark.asyncio
async def test_new_session(sm):
    """Neue Session wird erstellt und ist aktiv."""
    session = await sm.new_session("proj1")
    assert session.project_id == "proj1"
    assert session.ended_at is None
    assert sm.get_active("proj1") is session


@pytest.mark.asyncio
async def test_append_message(sm):
    """Nachrichten können angehängt werden."""
    await sm.new_session("proj1")
    msg = await sm.append("proj1", MessageRole.USER, "Hallo Welt")
    assert msg.role == MessageRole.USER
    assert msg.content == "Hallo Welt"
    session = sm.get_active("proj1")
    assert len(session.messages) == 1


@pytest.mark.asyncio
async def test_parallel_appends_kein_datenverlust(sm):
    """Parallele Appends verlieren keine Nachrichten (Lock-Schutz)."""
    await sm.new_session("proj1")

    async def append_n(n: int):
        for i in range(n):
            await sm.append("proj1", MessageRole.USER, f"Nachricht {i}")

    # 5 parallele Coroutinen je 10 Nachrichten = 50 insgesamt
    await asyncio.gather(*[append_n(10) for _ in range(5)])

    session = sm.get_active("proj1")
    assert len(session.messages) == 50, \
        f"Erwartet 50 Nachrichten, bekommen {len(session.messages)}"


@pytest.mark.asyncio
async def test_end_session(sm):
    """Session wird beendet und gespeichert."""
    await sm.new_session("proj1")
    await sm.append("proj1", MessageRole.USER, "Test")
    ended = await sm.end_session("proj1")
    assert ended is not None
    assert ended.ended_at is not None
    assert sm.get_active("proj1") is None


@pytest.mark.asyncio
async def test_new_session_beendet_alte(sm):
    """Neue Session beendet automatisch die alte."""
    s1 = await sm.new_session("proj1")
    s2 = await sm.new_session("proj1")
    assert s1.id != s2.id
    assert sm.get_active("proj1") is s2


@pytest.mark.asyncio
async def test_persist_und_reload(sm, tmp_path):
    """Session wird in SQLite gespeichert und kann wiederhergestellt werden."""
    await sm.new_session("proj1")
    await sm.append("proj1", MessageRole.USER, "persistierte Nachricht")
    await sm.end_session("proj1")

    # Neuen SessionManager mit gleichem Verzeichnis
    sm2 = SessionManager(projects_dir=tmp_path)
    sm2.start()
    sessions = sm2.list_sessions("proj1")
    assert len(sessions) == 1
    assert "persistierte Nachricht" in sessions[0]["preview"]


@pytest.mark.asyncio
async def test_compact(sm):
    """Kompaktierung ersetzt alte Nachrichten durch Summary."""
    await sm.new_session("proj1")
    for i in range(20):
        await sm.append("proj1", MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT, f"msg {i}")

    await sm.compact("proj1", "Zusammenfassung der Konversation", keep_last=5)

    session = sm.get_active("proj1")
    # 1 Summary-Message + 5 kept = 6
    assert len(session.messages) == 6
    assert "Zusammenfassung" in session.messages[0].content


@pytest.mark.asyncio
async def test_pop_last(sm):
    """Letzte Nachricht kann entfernt werden."""
    await sm.new_session("proj1")
    await sm.append("proj1", MessageRole.USER, "erste")
    await sm.append("proj1", MessageRole.ASSISTANT, "zweite")
    await sm.pop_last("proj1")
    session = sm.get_active("proj1")
    assert len(session.messages) == 1
    assert session.messages[0].content == "erste"


@pytest.mark.asyncio
async def test_estimated_tokens(sm):
    """Token-Schätzung ist korrekt (1 Token ≈ 4 Zeichen)."""
    await sm.new_session("proj1")
    await sm.append("proj1", MessageRole.USER, "a" * 400)  # ≈ 125 Tokens (chars/3.2)
    tokens = sm.estimated_tokens("proj1")
    assert 110 <= tokens <= 140


# ── SQLite-spezifische Tests (#395) ─────────────────────────────────────────

@pytest.mark.asyncio
async def test_db_file_created(sm, tmp_path):
    """sessions.db wird beim Start erstellt."""
    assert (tmp_path / "sessions.db").exists()


@pytest.mark.asyncio
async def test_db_message_count(sm, tmp_path):
    """Message-Count in DB stimmt mit In-Memory überein."""
    await sm.new_session("proj1")
    for i in range(5):
        await sm.append("proj1", MessageRole.USER, f"msg {i}")

    db = sqlite3.connect(str(tmp_path / "sessions.db"))
    db.row_factory = sqlite3.Row
    row = db.execute("SELECT message_count FROM sessions WHERE project_id = 'proj1'").fetchone()
    assert row["message_count"] == 5
    db.close()


@pytest.mark.asyncio
async def test_usage_stats_from_db(sm):
    """get_usage_stats aggregiert Token-Usage korrekt aus DB."""
    await sm.new_session("proj1")
    await sm.append("proj1", MessageRole.ASSISTANT, "response",
                     input_tokens=100, output_tokens=50,
                     cache_read_tokens=20, model="claude-sonnet-4-20250514")

    stats = sm.get_usage_stats()
    assert "proj1" in stats
    assert stats["proj1"]["total_input"] == 100
    assert stats["proj1"]["total_output"] == 50
    assert stats["proj1"]["total_cache_read"] == 20
    assert "claude-sonnet-4-20250514" in stats["proj1"]["model_breakdown"]


@pytest.mark.asyncio
async def test_list_sessions_from_db(sm):
    """list_sessions liest aus DB statt JSON-Dateien."""
    s1 = await sm.new_session("proj1")
    await sm.append("proj1", MessageRole.USER, "erste session")
    await sm.end_session("proj1")

    s2 = await sm.new_session("proj1")
    await sm.append("proj1", MessageRole.USER, "zweite session")
    await sm.end_session("proj1")

    sessions = sm.list_sessions("proj1")
    assert len(sessions) == 2
    # Neueste zuerst
    assert sessions[0]["id"] == s2.id
    assert sessions[1]["id"] == s1.id


@pytest.mark.asyncio
async def test_resume_session_from_db(sm):
    """Historische Session kann aus DB wiederhergestellt werden."""
    s1 = await sm.new_session("proj1")
    await sm.append("proj1", MessageRole.USER, "alte nachricht")
    s1_id = s1.id
    await sm.end_session("proj1")

    # Neue Session starten
    await sm.new_session("proj1")

    # Alte Session wieder aktivieren
    resumed = await sm.resume_session("proj1", s1_id)
    assert resumed is not None
    assert resumed.id == s1_id
    assert len(resumed.messages) == 1
    assert resumed.messages[0].content == "alte nachricht"


@pytest.mark.asyncio
async def test_get_session_by_id(sm):
    """Session kann per ID geladen werden (aktiv oder historisch)."""
    s1 = await sm.new_session("proj1")
    await sm.append("proj1", MessageRole.USER, "test content")
    s1_id = s1.id
    await sm.end_session("proj1")

    loaded = sm.get_session_by_id("proj1", s1_id)
    assert loaded is not None
    assert loaded.id == s1_id
    assert len(loaded.messages) == 1


@pytest.mark.asyncio
async def test_compact_persists_to_db(sm, tmp_path):
    """Nach Compaction stimmt DB mit In-Memory überein."""
    await sm.new_session("proj1")
    for i in range(20):
        await sm.append("proj1", MessageRole.USER if i % 2 == 0 else MessageRole.ASSISTANT, f"msg {i}")

    await sm.compact("proj1", "Summary", keep_last=5)

    # DB prüfen
    db = sqlite3.connect(str(tmp_path / "sessions.db"))
    msg_count = db.execute(
        "SELECT COUNT(*) FROM messages WHERE session_id = ?",
        (sm.get_active("proj1").id,),
    ).fetchone()[0]
    assert msg_count == 6  # 1 summary + 5 kept
    db.close()
