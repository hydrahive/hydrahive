"""
test_session_manager.py — SessionManager Unit-Tests

Testet:
- Neue Session erstellen
- Nachrichten anhängen (async)
- Parallele Appends (Race Condition Schutz via Lock)
- Context-Kompaktierung
- Persist & Reload
"""
import asyncio
import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from hydrahive_core.session_manager import SessionManager, MessageRole


@pytest.fixture
def sm(tmp_path):
    return SessionManager(projects_dir=tmp_path)


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
    """Session wird auf Disk gespeichert und kann wiederhergestellt werden."""
    await sm.new_session("proj1")
    await sm.append("proj1", MessageRole.USER, "persistierte Nachricht")
    await sm.end_session("proj1")

    # Neuen SessionManager mit gleichem Verzeichnis
    sm2 = SessionManager(projects_dir=tmp_path)
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
    await sm.append("proj1", MessageRole.USER, "a" * 400)  # ≈ 100 Tokens
    tokens = sm.estimated_tokens("proj1")
    assert 90 <= tokens <= 110
