"""
collab_yjs.py — Collaborative Composer via Yjs/CRDT (#554)

Startet den WebSocket-Server für gemeinsames Bearbeiten des Projekt-
Composers. Pro Projekt gibt es einen YRoom mit eigener SQLite-Persistenz
unter /var/lib/hydrahive/yjs/<project_id>.sqlite.

Auth läuft im Endpoint (siehe router_projects.collab_ws) via JWT-Token
als Query-Param. Browser können keine Custom-Headers auf WebSockets
setzen — deshalb Query statt Authorization-Header. Auth MUSS vor
websocket.accept() passieren, sonst verschwinden Yjs-Sync-Messages.

Lifecycle:
    in lifespan-Startup:  await start_yjs_server()
    in lifespan-Shutdown: await stop_yjs_server()
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from fastapi import WebSocket, WebSocketDisconnect

from pycrdt.store import SQLiteYStore
from pycrdt.websocket.websocket_server import WebsocketServer
from pycrdt.websocket.yroom import YRoom

logger = logging.getLogger(__name__)


# Default-Pfad; ist auf .220/.5 unter /var/lib/hydrahive/yjs/ angelegt. Falls
# nicht schreibbar (z.B. lokale Dev-Runs ohne root), fällt start_yjs_server
# auf ~/.hydrahive/yjs/ zurück.
DEFAULT_STORE_DIR = Path("/var/lib/hydrahive/yjs")


class HydraHiveYjsServer(WebsocketServer):
    """pycrdt-Server mit SQLite-Persistenz pro Room.

    Überschreibt get_room(), um beim ersten Zugriff auf einen Raum eine
    SQLite-Datei zu öffnen und dem YRoom als ystore zu übergeben. Der
    YRoom startet die YStore anschließend selbst (siehe pycrdt.yroom).
    """

    def __init__(self, store_dir: Path, **kwargs) -> None:
        super().__init__(**kwargs)
        self.store_dir = store_dir
        self.store_dir.mkdir(parents=True, exist_ok=True)

    async def get_room(self, name: str) -> YRoom:
        if name not in self.rooms:
            db_path = self.store_dir / f"{name}.sqlite"
            ystore = SQLiteYStore(str(db_path), log=self.log)
            room = YRoom(ready=self.rooms_ready, log=self.log, ystore=ystore)
            self.rooms[name] = room
            logger.info("Yjs-Room erstellt: %s (store=%s)", name, db_path)
        room = self.rooms[name]
        await self.start_room(room)
        return room


# Module-global Singleton. Pro Core-Prozess genau ein Server, alle Projekte
# teilen sich die interne Room-Map — jeder Raum hat eine eigene SQLite.
_yjs_server: HydraHiveYjsServer | None = None
_yjs_server_task: asyncio.Task | None = None


def get_yjs_server() -> HydraHiveYjsServer | None:
    return _yjs_server


def _resolve_store_dir() -> Path:
    """Nimmt /var/lib/hydrahive/yjs wenn schreibbar, sonst ~/.hydrahive/yjs.

    Der Fallback schützt Test-/Dev-Umgebungen, in denen /var/lib nicht vom
    Core-Prozess beschrieben werden kann. Produktiv legt der Installer das
    Default-Verzeichnis an und chown'd es auf den Service-User.
    """
    candidate = DEFAULT_STORE_DIR
    try:
        candidate.mkdir(parents=True, exist_ok=True)
        probe = candidate / ".writable"
        probe.touch()
        probe.unlink(missing_ok=True)
        return candidate
    except (OSError, PermissionError):
        fallback = Path.home() / ".hydrahive" / "yjs"
        fallback.mkdir(parents=True, exist_ok=True)
        logger.warning(
            "Yjs-Store fällt zurück auf %s (keine Schreibrechte in %s)",
            fallback, candidate,
        )
        return fallback


async def start_yjs_server() -> HydraHiveYjsServer:
    """Startet den globalen Yjs-Server. Idempotent — zweiter Aufruf gibt
    die laufende Instanz zurück."""
    global _yjs_server, _yjs_server_task
    if _yjs_server is not None:
        return _yjs_server
    store_dir = _resolve_store_dir()
    _yjs_server = HydraHiveYjsServer(
        store_dir=store_dir,
        auto_clean_rooms=True,
        log=logger,
    )
    _yjs_server_task = asyncio.create_task(_yjs_server.start())
    await _yjs_server.started.wait()
    logger.info("Yjs-WebsocketServer gestartet (store=%s)", store_dir)
    return _yjs_server


async def stop_yjs_server() -> None:
    """Stoppt den globalen Yjs-Server sauber (cleanup der Rooms + Stores)."""
    global _yjs_server, _yjs_server_task
    if _yjs_server is None:
        return
    try:
        await _yjs_server.stop()
    except Exception:
        logger.exception("Fehler beim Stoppen des Yjs-Servers")
    if _yjs_server_task is not None:
        _yjs_server_task.cancel()
        try:
            await _yjs_server_task
        except (asyncio.CancelledError, Exception):
            pass
    _yjs_server = None
    _yjs_server_task = None


class FastApiWsChannel:
    """Adapter: FastAPI WebSocket → minimales pycrdt-Channel-Interface.

    pycrdt.Channel ist ein Protocol — wir müssen `send(bytes)`, `recv()`,
    `path` und async iteration liefern. Im Gegensatz zu pycrdts ASGIServer
    (der eigenen Receive/Send-Callbacks nutzt) binden wir das an FastAPIs
    WebSocket-Objekt, damit der Endpoint sich nahtlos in den bestehenden
    APIRouter einreiht.
    """

    def __init__(self, websocket: WebSocket, path: str, label: str = "") -> None:
        self._ws = websocket
        self.path = path
        self._label = label
        self._sent = 0
        self._recv = 0

    async def send(self, message: bytes) -> None:
        await self._ws.send_bytes(message)
        self._sent += 1
        if self._sent <= 5 or self._sent % 100 == 0:
            logger.debug("collab-ws[%s] → send #%d bytes=%d", self._label, self._sent, len(message))

    async def recv(self) -> bytes:
        data = await self._ws.receive_bytes()
        self._recv += 1
        if self._recv <= 5 or self._recv % 100 == 0:
            logger.debug("collab-ws[%s] ← recv #%d bytes=%d", self._label, self._recv, len(data))
        return data

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        try:
            return await self.recv()
        except WebSocketDisconnect:
            raise StopAsyncIteration()
