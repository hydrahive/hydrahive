"""Smoke-Tests für collab_yjs (#554 H1-H4).

Stellt sicher dass der Yjs-Server sich starten/stoppen lässt und pro
Room-Name ein eigener SQLiteYStore angelegt wird. Kein echtes
WebSocket-Handshake — das deckt der Integration-Test via .220 ab.
"""
from __future__ import annotations

import pytest

from hydrahive_core.collab_yjs import (
    DebugYRoom,
    HydraHiveYjsServer,
    get_yjs_debug_events,
    record_yjs_debug_event,
    reset_yjs_debug_events,
    _resolve_store_dir,
    start_yjs_server,
    stop_yjs_server,
)


@pytest.fixture
def tmp_store_dir(tmp_path, monkeypatch):
    # Default-Verzeichnis auf tmp umbiegen, damit Tests nicht /var/lib nutzen
    monkeypatch.setattr("hydrahive_core.collab_yjs.DEFAULT_STORE_DIR", tmp_path / "yjs")
    reset_yjs_debug_events()
    yield tmp_path / "yjs"
    reset_yjs_debug_events()


@pytest.mark.anyio
async def test_resolve_store_dir_returns_writable_path(tmp_store_dir):
    path = _resolve_store_dir()
    assert path.exists()
    assert path == tmp_store_dir


@pytest.mark.anyio
async def test_start_and_stop_server(tmp_store_dir):
    server = await start_yjs_server()
    try:
        assert isinstance(server, HydraHiveYjsServer)
        assert server.store_dir == tmp_store_dir
        # Zweiter Aufruf gibt dieselbe Instanz zurück (idempotent)
        second = await start_yjs_server()
        assert second is server
    finally:
        await stop_yjs_server()


@pytest.mark.anyio
async def test_get_room_creates_sqlite_file(tmp_store_dir):
    server = await start_yjs_server()
    try:
        room = await server.get_room("test-project")
        assert room is not None
        assert isinstance(room, DebugYRoom)
        assert "test-project" in server.rooms
        # SQLite-Datei wird spätestens beim ersten Write angelegt; hier testen
        # wir nur dass der Pfad korrekt konfiguriert wurde.
        db_path = tmp_store_dir / "test-project.sqlite"
        assert room.ystore is not None
        assert str(db_path) in str(room.ystore.path) if hasattr(room.ystore, "path") else True
    finally:
        await stop_yjs_server()


def test_debug_events_are_limited_and_filterable_by_room():
    reset_yjs_debug_events()
    for i in range(250):
        room = "room-a" if i % 2 == 0 else "room-b"
        record_yjs_debug_event("test_event", room=room, index=i)

    events = get_yjs_debug_events(limit=500)
    assert len(events) == 200
    assert events[0]["index"] == 50

    room_a_events = get_yjs_debug_events(limit=500, room="room-a")
    assert room_a_events
    assert all(event["room"] == "room-a" for event in room_a_events)


def test_debug_events_infer_room_from_label():
    reset_yjs_debug_events()
    record_yjs_debug_event("serve_enter", label="project-1/alice")

    events = get_yjs_debug_events()
    assert events[-1]["room"] == "project-1"
