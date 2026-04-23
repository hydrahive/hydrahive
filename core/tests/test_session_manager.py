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

from hydrahive_core.session_manager import SessionManager, MessageRole, cleanup_incomplete_messages


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


# =========================================================================
# #637 Review-Finding: _merge_consecutive_roles darf tool_calls nicht
# stillschweigend verlieren (Edge nach Compaction-Summary etc.)
# =========================================================================

def test_merge_consecutive_roles_preserves_tool_calls():
    """Wenn zwei aufeinanderfolgende assistant-Messages auftreten und eine
    davon `tool_calls` hat, darf der Merge sie NICHT zusammenwerfen —
    sonst gehen die strukturierten Tool-Call-Daten verloren."""
    from hydrahive_core.session_manager import _merge_consecutive_roles

    msgs = [
        {"role": "assistant", "content": "Compaction-Summary."},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "call_42", "type": "function",
             "function": {"name": "foo", "arguments": "{}"}},
        ]},
    ]
    out = _merge_consecutive_roles(msgs)

    # Beide Messages bleiben getrennt, tool_calls erhalten
    assert len(out) == 2, f"Merge hat tool_calls-Message verschluckt: {out}"
    assert out[1].get("tool_calls"), "tool_calls-Feld ging verloren"
    assert out[1]["tool_calls"][0]["id"] == "call_42"
    # Compaction-Summary bleibt unverändert
    assert out[0]["content"] == "Compaction-Summary."


def test_merge_consecutive_roles_still_merges_plain_text():
    """Regression-Schutz: zwei aufeinanderfolgende user/assistant ohne
    tool_calls werden weiterhin gemerged (Anthropic-Wechsel-Constraint)."""
    from hydrahive_core.session_manager import _merge_consecutive_roles

    msgs = [
        {"role": "assistant", "content": "Erste Antwort"},
        {"role": "assistant", "content": "Zweite Antwort"},
    ]
    out = _merge_consecutive_roles(msgs)
    assert len(out) == 1
    assert "Erste Antwort" in out[0]["content"]
    assert "Zweite Antwort" in out[0]["content"]


# =============================================================================
# #846 — DB-Geister-Sessions (mehrere ended_at IS NULL pro project_id)
# =============================================================================

@pytest.mark.asyncio
async def test_start_cleans_up_ghost_sessions(tmp_path):
    """#846: Nach Core-Crash koennen mehrere Sessions pro project_id mit
    ended_at=None in der DB liegen. start() darf nur die neueste als
    aktiv uebernehmen, alle aelteren Geister muessen automatisch beendet
    werden."""
    import sqlite3

    # SessionManager 1: erstelle 3 Geister-Sessions fuer gleichen project_id
    mgr1 = SessionManager(projects_dir=tmp_path)
    mgr1.start()
    s1 = await mgr1.new_session("proj-x")
    # Zweite: simuliere Crash — ended_at bleibt NULL in DB
    s2 = await mgr1.new_session("proj-x")
    s3 = await mgr1.new_session("proj-x")
    # Durch new_session wurden s1,s2 sauber beendet. Simuliere Crash:
    # manuell NULL-ended_at wieder eintragen fuer s1 und s2 — ueber mgr1's
    # eigenes DB-Handle, dann close damit mgr2 einen frischen Reload macht.
    mgr1._db.execute("UPDATE sessions SET ended_at = NULL WHERE id IN (?, ?)", (s1.id, s2.id))
    mgr1._db.commit()
    mgr1._db.close()
    mgr1._db = None

    # SessionManager 2: neuer Start, liest DB neu
    mgr2 = SessionManager(projects_dir=tmp_path)
    mgr2.start()

    # Nur s3 (neueste) sollte als aktive geladen sein
    active = mgr2.get_active("proj-x")
    assert active is not None
    assert active.id == s3.id, (
        f"Erwartet neueste Session {s3.id}, got {active.id if active else None}"
    )

    # s1 und s2 muessen in DB als ended markiert sein
    row1 = mgr2._db.execute("SELECT ended_at FROM sessions WHERE id = ?", (s1.id,)).fetchone()
    row2 = mgr2._db.execute("SELECT ended_at FROM sessions WHERE id = ?", (s2.id,)).fetchone()
    assert row1["ended_at"] is not None, "Geister-Session s1 muss beendet sein"
    assert row2["ended_at"] is not None, "Geister-Session s2 muss beendet sein"


@pytest.mark.asyncio
async def test_end_session_cleans_db_ghosts(sm, tmp_path):
    """#846: end_session raeumt DB-Geister fuer project_id auf, auch wenn
    die in-memory Session bereits weg ist (Szenario: Client DELETE nach
    Core-Restart ohne vorherigen send_message)."""
    import sqlite3

    # Manuell einen Geist in die DB einfuegen (simuliert abgestorbene Session
    # aus vorherigem Core-Run).
    ghost_id = "ghost-deadbeef-0000-0000-0000-000000000000"
    sm._db.execute(
        "INSERT INTO sessions (id, project_id, started_at, ended_at) VALUES (?, ?, ?, NULL)",
        (ghost_id, "proj-y", "2026-04-22T10:00:00+00:00"),
    )
    sm._db.commit()

    # Keine in-memory Session fuer proj-y
    assert sm.get_active("proj-y") is None

    # DELETE-Call auf Projekt ohne aktive Session
    result = await sm.end_session("proj-y")
    assert result is None, "end_session ohne aktive Session gibt None zurueck"

    # DB-Geist muss trotzdem beendet sein
    row = sm._db.execute("SELECT ended_at FROM sessions WHERE id = ?", (ghost_id,)).fetchone()
    assert row["ended_at"] is not None, "Geist muss durch end_session beendet worden sein"


@pytest.mark.asyncio
async def test_new_session_cleans_db_ghosts(sm):
    """#846: new_session raeumt DB-Geister fuer project_id auf (ausser der
    frisch erstellten), damit bei naechstem Reload kein Geist wiederkommt."""
    ghost_id = "ghost-aaaaaaaa-0000-0000-0000-000000000000"
    sm._db.execute(
        "INSERT INTO sessions (id, project_id, started_at, ended_at) VALUES (?, ?, ?, NULL)",
        (ghost_id, "proj-z", "2026-04-22T10:00:00+00:00"),
    )
    sm._db.commit()

    # Neue Session fuer proj-z starten
    fresh = await sm.new_session("proj-z")

    # Geist muss beendet sein, fresh noch aktiv (NULL)
    row_ghost = sm._db.execute("SELECT ended_at FROM sessions WHERE id = ?", (ghost_id,)).fetchone()
    row_fresh = sm._db.execute("SELECT ended_at FROM sessions WHERE id = ?", (fresh.id,)).fetchone()
    assert row_ghost["ended_at"] is not None, "Geist muss beendet sein"
    assert row_fresh["ended_at"] is None, "Frische Session muss noch aktiv sein"


# -------------------------------------------------------------------------- #871

def _make_msg(role, content="test", tool_calls=None, tool_call_id=None):
    """Hilfsfunktion für Message-Objekte im Test."""
    from hydrahive_core.session_manager import Message
    return Message.create(
        role=MessageRole(role),
        content=content,
        tool_calls=tool_calls,
        tool_call_id=tool_call_id,
    )


def test_cleanup_removes_incomplete_assistant_after_cancel():
    """#871: Nach Cancellation — incomplete ASSISTANT (tool_calls aber kein
    tool_result) ist die LETZTE message und wird entfernt."""
    messages = [
        _make_msg("user", "start"),
        # ASSISTANT mit tool_calls gefolgt von nichts (Cancel) — incomplete
        _make_msg("assistant", "ich rufe tool auf", tool_calls=[{"id": "tc1", "type": "function", "function": {"name": "test", "arguments": "{}"}}]),
    ]
    removed = cleanup_incomplete_messages(messages)
    assert removed == 1, f"Erwartet 1 removed, bekommen {removed}"
    # Nur user-Message übrig
    assert len(messages) == 1
    assert messages[0].role == MessageRole.USER


def test_cleanup_removes_orphaned_tool_results():
    """#871: Orphaned TOOL (tool_call_id hat keinen corresponding ASSISTANT)
    wird entfernt. ASSISTANTtc1 bleibt weil tc1 ein gültiges tool_call.id ist
    (corresponding TOOLtc1 existiert)."""
    messages = [
        _make_msg("user", "start"),
        _make_msg("assistant", "", tool_calls=[{"id": "tc1", "type": "function", "function": {"name": "test", "arguments": "{}"}}]),
        _make_msg("tool", "result for tc1", tool_call_id="tc1"),   # valid pair for tc1
        _make_msg("tool", "orphaned result", tool_call_id="tc999"),  # orphaned — tc999 has no corresponding assistant
    ]
    removed = cleanup_incomplete_messages(messages)
    assert removed == 1, f"Erwartet 1 removed (orphaned tool_result), bekommen {removed}"
    assert len(messages) == 3, f"USER + ASSISTANTtc1 + TOOLtc1 = 3 messages, got {len(messages)}"
    # TOOLtc999 removed, last message is now TOOLtc1
    assert messages[-1].role == MessageRole.TOOL, "TOOLtc1 muss erhalten bleiben"
    assert messages[-1].tool_call_id == "tc1"


def test_cleanup_noop_on_clean_history():
    """#871: Saubere History (kein cancel) — keine Aenderung."""
    messages = [
        _make_msg("user", "start"),
        _make_msg("assistant", "antwort"),
        _make_msg("tool", "ok", tool_call_id="tc1"),
        _make_msg("assistant", "fertig", tool_calls=[{"id": "tc2", "type": "function", "function": {"name": "test2", "arguments": "{}"}}]),
        _make_msg("tool", "ok2", tool_call_id="tc2"),
    ]
    original_len = len(messages)
    removed = cleanup_incomplete_messages(messages)
    assert removed == 0, f"Erwartet 0 removed, bekommen {removed}"
    assert len(messages) == original_len
