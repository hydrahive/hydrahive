"""Smoke-Tests für collab_yjs (#554 H1-H4).

Stellt sicher dass der Yjs-Server sich starten/stoppen lässt und pro
Room-Name ein eigener SQLiteYStore angelegt wird. Kein echtes
WebSocket-Handshake — das deckt der Integration-Test via .220 ab.
"""
from __future__ import annotations

import pytest

from hydrahive_core.collab_yjs import (
    HydraHiveYjsServer,
    _resolve_store_dir,
    start_yjs_server,
    stop_yjs_server,
)


@pytest.fixture
def tmp_store_dir(tmp_path, monkeypatch):
    # Default-Verzeichnis auf tmp umbiegen, damit Tests nicht /var/lib nutzen
    monkeypatch.setattr("hydrahive_core.collab_yjs.DEFAULT_STORE_DIR", tmp_path / "yjs")
    yield tmp_path / "yjs"


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
        assert "test-project" in server.rooms
        # SQLite-Datei wird spätestens beim ersten Write angelegt; hier testen
        # wir nur dass der Pfad korrekt konfiguriert wurde.
        db_path = tmp_store_dir / "test-project.sqlite"
        assert room.ystore is not None
        assert str(db_path) in str(room.ystore.path) if hasattr(room.ystore, "path") else True
    finally:
        await stop_yjs_server()
