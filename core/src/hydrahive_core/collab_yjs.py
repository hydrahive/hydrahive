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
import time
from collections import deque
from inspect import isawaitable
from pathlib import Path

from anyio import create_task_group
from fastapi import WebSocket, WebSocketDisconnect

from pycrdt import (
    YMessageType,
    YSyncMessageType,
    create_sync_message,
    handle_sync_message,
    is_awareness_disconnect_message,
    read_message,
)
from pycrdt.store import SQLiteYStore
from pycrdt.websocket.websocket_server import WebsocketServer
from pycrdt.websocket.yroom import YRoom

logger = logging.getLogger(__name__)


# Default-Pfad; ist auf .220/.5 unter /var/lib/hydrahive/yjs/ angelegt. Falls
# nicht schreibbar (z.B. lokale Dev-Runs ohne root), fällt start_yjs_server
# auf ~/.hydrahive/yjs/ zurück.
DEFAULT_STORE_DIR = Path("/var/lib/hydrahive/yjs")
_DEBUG_EVENTS_MAX = 200
_debug_events: deque[dict] = deque(maxlen=_DEBUG_EVENTS_MAX)


def _record_debug_event(event: str, **data) -> None:
    if "room" not in data and isinstance(data.get("label"), str):
        data["room"] = data["label"].split("/", 1)[0]
    payload = {
        "ts": round(time.time(), 3),
        "event": event,
        **data,
    }
    _debug_events.append(payload)
    logger.info("collab-yjs %s %s", event, data)


def record_yjs_debug_event(event: str, **data) -> None:
    _record_debug_event(event, **data)


def get_yjs_debug_events(limit: int = 50, room: str | None = None) -> list[dict]:
    if limit <= 0:
        return []
    events = list(_debug_events)
    if room:
        events = [event for event in events if event.get("room") in {room, None}]
    return events[-limit:]


def reset_yjs_debug_events() -> None:
    _debug_events.clear()


def _classify_yjs_message(message: bytes) -> dict:
    if not message:
        return {"message_kind": "empty", "bytes": 0}

    result = {"bytes": len(message)}
    try:
        message_type = YMessageType(message[0])
    except Exception:
        result["message_kind"] = f"unknown:{message[0]}"
        return result

    result["message_kind"] = message_type.name.lower()
    if message_type == YMessageType.SYNC and len(message) > 1:
        try:
            result["sync_type"] = YSyncMessageType(message[1]).name.lower()
        except Exception:
            result["sync_type"] = f"unknown:{message[1]}"
    return result


class DebugYRoom(YRoom):
    """YRoom mit Debug-Events für #769.

    Die Logik bleibt nah an pycrdts YRoom.serve(), ergänzt aber Events an den
    Stellen, an denen wir für Cross-Client-Sync unterscheiden müssen:
    Client-Lifecycle, Sync-Messages, Awareness-Messages und Doc-Updates.
    """

    def __init__(self, room_name: str, **kwargs) -> None:
        super().__init__(**kwargs)
        self.room_name = room_name
        self._debug_doc_subscription = self.ydoc.observe(self._record_doc_update)

    def _record_doc_update(self, event) -> None:
        update = getattr(event, "update", None)
        update_len = len(update) if isinstance(update, (bytes, bytearray, memoryview)) else None
        _record_debug_event(
            "doc_update",
            room=self.room_name,
            bytes=update_len,
            event_type=type(event).__name__,
        )

    async def serve(self, channel):
        try:
            async with create_task_group() as tg:
                self.clients.add(channel)
                _record_debug_event(
                    "client_added",
                    room=self.room_name,
                    label=getattr(channel, "_label", getattr(channel, "path", "?")),
                    client_count=len(self.clients),
                )
                sync_message = create_sync_message(self.ydoc)
                await channel.send(sync_message)
                _record_debug_event(
                    "initial_sync_sent",
                    room=self.room_name,
                    label=getattr(channel, "_label", getattr(channel, "path", "?")),
                    bytes=len(sync_message),
                    client_count=len(self.clients),
                )
                async for message in channel:
                    message_info = _classify_yjs_message(message)
                    label = getattr(channel, "_label", getattr(channel, "path", "?"))

                    skip = False
                    if self.on_message:
                        _skip = self.on_message(message)
                        skip = await _skip if isawaitable(_skip) else _skip
                    if skip:
                        _record_debug_event(
                            "message_skipped",
                            room=self.room_name,
                            label=label,
                            **message_info,
                        )
                        continue

                    message_type = message[0] if message else None
                    if message_type == YMessageType.SYNC:
                        _record_debug_event(
                            "sync_message",
                            room=self.room_name,
                            label=label,
                            client_count=len(self.clients),
                            **message_info,
                        )
                        reply = handle_sync_message(message[1:], self.ydoc)
                        if reply is not None:
                            _record_debug_event(
                                "sync_reply",
                                room=self.room_name,
                                label=label,
                                bytes=len(reply),
                                client_count=len(self.clients),
                            )
                            tg.start_soon(channel.send, reply)
                    elif message_type == YMessageType.AWARENESS:
                        disconnection = is_awareness_disconnect_message(message[1:])
                        _record_debug_event(
                            "awareness_message",
                            room=self.room_name,
                            label=label,
                            client_count=len(self.clients),
                            disconnect=disconnection,
                            **message_info,
                        )
                        for client in self.clients:
                            if disconnection and client == channel:
                                continue
                            tg.start_soon(client.send, message)
                        self.awareness.apply_awareness_update(read_message(message[1:]), self)
                    else:
                        _record_debug_event(
                            "unknown_message",
                            room=self.room_name,
                            label=label,
                            **message_info,
                        )
        except Exception as exception:
            self._handle_exception(exception)
        finally:
            self.clients.discard(channel)
            _record_debug_event(
                "client_removed",
                room=self.room_name,
                label=getattr(channel, "_label", getattr(channel, "path", "?")),
                client_count=len(self.clients),
            )


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
            room = DebugYRoom(name, ready=self.rooms_ready, log=self.log, ystore=ystore)
            self.rooms[name] = room
            _record_debug_event("room_created", room=name, store=str(db_path))
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
    _record_debug_event("server_started", store=str(store_dir))
    logger.info("Yjs-WebsocketServer gestartet (store=%s)", store_dir)
    return _yjs_server


async def stop_yjs_server() -> None:
    """Stoppt den globalen Yjs-Server sauber (cleanup der Rooms + Stores)."""
    global _yjs_server, _yjs_server_task
    if _yjs_server is None:
        return
    _record_debug_event("server_stopping")
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
    _record_debug_event("server_stopped")


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
        try:
            await self._ws.send_bytes(message)
        except Exception as e:
            _record_debug_event("send_failed", label=self._label, error=repr(e))
            logger.warning("collab-ws[%s] send failed: %s", self._label, e)
            raise
        self._sent += 1
        if self._sent <= 5 or self._sent % 100 == 0:
            _record_debug_event("send", label=self._label, count=self._sent, bytes=len(message))
            logger.info("collab-ws[%s] → send #%d bytes=%d", self._label, self._sent, len(message))

    async def recv(self) -> bytes:
        # Starlette receive_bytes() assertet hart wenn Message text statt bytes
        # ist. y-websocket-Clients senden binary, aber manche Proxies oder
        # Polyfills könnten texten. Robust: raw receive() und selber konvertieren.
        msg = await self._ws.receive()
        mtype = msg.get("type")
        if mtype == "websocket.disconnect":
            _record_debug_event("disconnect_frame", label=self._label, code=msg.get("code", 1000))
            raise WebSocketDisconnect(code=msg.get("code", 1000))
        if mtype != "websocket.receive":
            _record_debug_event("unexpected_frame", label=self._label, frame_type=mtype)
            logger.warning("collab-ws[%s] unexpected frame type=%s, skip", self._label, mtype)
            return b""
        data = msg.get("bytes")
        if data is None:
            text = msg.get("text") or ""
            _record_debug_event("text_frame", label=self._label, chars=len(text))
            logger.warning("collab-ws[%s] text frame (len=%d), encoding utf-8", self._label, len(text))
            data = text.encode("utf-8")
        self._recv += 1
        if self._recv <= 5 or self._recv % 100 == 0:
            _record_debug_event("recv", label=self._label, count=self._recv, bytes=len(data))
            logger.info("collab-ws[%s] ← recv #%d bytes=%d", self._label, self._recv, len(data))
        return data

    def __aiter__(self):
        return self

    async def __anext__(self) -> bytes:
        try:
            return await self.recv()
        except WebSocketDisconnect:
            _record_debug_event("stop_iteration", label=self._label)
            raise StopAsyncIteration()
