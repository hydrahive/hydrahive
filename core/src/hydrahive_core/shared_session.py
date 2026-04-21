"""
shared_session.py — Shared Sessions für Multi-User-Projekte (v2, #549)

Ermöglicht mehreren Usern gleichzeitig denselben Projekt-Chat zu sehen.
Einer schreibt, alle sehen die Antwort in Echtzeit via SSE.

Architektur:
    - Pro Projekt: Set von asyncio.Queues (eine pro verbundenem Client)
    - Turn-Lock: nur ein User kann gleichzeitig senden
    - Broadcast: SSE-Chunks werden an alle Subscribers gepusht
    - Presence: wer ist gerade online?

Nutzung:
    from .shared_session import shared_sessions

    # Client verbindet sich (z.B. im SSE-Endpoint)
    queue = shared_sessions.subscribe("project-id", "username")
    try:
        while True:
            event = await queue.get()
            yield f"data: {event}\\n\\n"
    finally:
        shared_sessions.unsubscribe("project-id", queue, "username")

    # Broadcast an alle Clients eines Projekts
    shared_sessions.broadcast("project-id", '{"text": "Hallo"}')
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ProjectPresence:
    """Tracking welche User gerade in einem Projekt online sind.

    #608: User ist online solange mindestens eine Client-Queue aktiv ist.
    Multi-Tab sicher — unsubscribe eines Tabs entfernt User nicht wenn
    andere Tabs noch offen sind.
    """
    users: dict[str, float] = field(default_factory=dict)        # username → last_seen
    refcount: dict[str, int] = field(default_factory=dict)       # username → aktive Queues

    def add(self, username: str) -> None:
        """User meldet sich an (Tab geoeffnet). Refcount hochziehen."""
        self.users[username] = time.time()
        self.refcount[username] = self.refcount.get(username, 0) + 1

    def refresh(self, username: str) -> None:
        """#587: Timestamp erneuern waehrend Client verbunden ist."""
        if username in self.users:
            self.users[username] = time.time()

    def remove(self, username: str) -> None:
        """User meldet sich ab (Tab geschlossen). Refcount runter, erst bei 0 entfernen."""
        if username not in self.refcount:
            return
        self.refcount[username] -= 1
        if self.refcount[username] <= 0:
            self.refcount.pop(username, None)
            self.users.pop(username, None)

    def online_users(self) -> list[str]:
        """Alle User die aktiv sind (Refcount > 0 UND last_seen < 60s)."""
        cutoff = time.time() - 60
        return [u for u, ts in self.users.items()
                if ts > cutoff and self.refcount.get(u, 0) > 0]


@dataclass
class ProjectStreamBuffer:
    """#726 K2a: Replay-Buffer pro Projekt für den aktuell laufenden Stream.

    Neue Subscriber bekommen beim Connect die gesammelten Events zugespielt,
    sehen also auch bereits gelaufene Chunks. Wird bei end() geleert — d.h.
    fertig abgeschlossene Streams liegen nicht weiter im Speicher.

    Achtung: pro Projekt gibt es zur Zeit genau einen laufenden Stream
    (Turn-Lock), daher reicht ein einzelner Buffer pro Projekt.
    """
    stream_id: str | None = None
    started_at: float | None = None
    events: list[str] = field(default_factory=list)
    # Obergrenze als Safety-Net — riesige Streams fressen sonst RAM bei late-joinern.
    # 500 Chunks entsprechen ~200 KiB bei typischen Text-Chunks, für Replays völlig ausreichend.
    MAX_EVENTS: int = 500

    def start(self, stream_id: str) -> None:
        self.stream_id = stream_id
        self.started_at = time.time()
        self.events = []

    def append(self, event_str: str) -> None:
        if self.stream_id is None:
            return
        self.events.append(event_str)
        if len(self.events) > self.MAX_EVENTS:
            # Ältesten Chunk kappen — besser Tail-Replay als OOM.
            self.events.pop(0)

    def end(self) -> None:
        self.stream_id = None
        self.started_at = None
        self.events = []

    def is_active(self) -> bool:
        return self.stream_id is not None

    def snapshot(self) -> list[str]:
        """Schnappschuss der Events — Kopie, damit Iteration sicher ist."""
        return list(self.events)


class SharedSessionManager:
    """Verwaltet Shared Sessions für alle Projekte."""

    def __init__(self) -> None:
        # project_id → Set von asyncio.Queues (eine pro Client-Verbindung)
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        # project_id → username der gerade den Turn hat (None = frei)
        self._turn_owner: dict[str, str | None] = {}
        # project_id → Presence-Tracking
        self._presence: dict[str, ProjectPresence] = {}
        # #608: Queue → username-Mapping fuer Cleanup bei Queue-Drops
        self._queue_user: dict[asyncio.Queue, tuple[str, str]] = {}  # queue → (project_id, username)
        # #726 K2a: Replay-Buffer für aktuell laufende Streams pro Projekt
        self._stream_buffers: dict[str, ProjectStreamBuffer] = {}
        # #807: RLock um zusammenhängende Multi-Dict-Updates atomar zu machen.
        # RLock, weil _broadcast_presence() aus gelockten Sektionen heraus
        # aufgerufen wird und selbst den Lock zum online_users-Snapshot
        # braucht. Kritische Sections: subscribe, unsubscribe, broadcast
        # (dead-queue Cleanup), acquire_turn/release_turn, Stream-Lifecycle.
        # Methoden bleiben synchron — ein threading.Lock in sonst sync-Code
        # ändert keine Caller-API und schützt sowohl gegen Thread-Pool-
        # Ausführung (FastAPI sync-Routes) als auch gegen await-Points in
        # Aufrufern, die zwischen zwei Mutationen einen Context-Switch
        # triggern könnten.
        self._state_lock = threading.RLock()

    def subscribe(self, project_id: str, username: str) -> asyncio.Queue:
        """Client meldet sich an — bekommt eine Queue für SSE-Events.

        #726 K2b: wenn beim Projekt ein Stream aktiv ist, wird der bisherige
        Verlauf (Buffer-Snapshot) sofort in die frische Queue geschrieben,
        bevor sie in _subscribers landet. Damit sieht ein später-joiner die
        vorherigen Chunks ohne Duplikate (atomic weil keine await-Punkte).

        Returns: asyncio.Queue die der SSE-Endpoint lesen kann.
        """
        with self._state_lock:  # #807: atomar subscribers+presence+queue_user
            self._subscribers.setdefault(project_id, set())
            self._presence.setdefault(project_id, ProjectPresence())

            # Priming aus dem Replay-Buffer
            buf = self._stream_buffers.get(project_id)
            primed_events: list[str] = buf.snapshot() if buf and buf.is_active() else []
            capacity = max(100, len(primed_events) + 50)
            queue: asyncio.Queue = asyncio.Queue(maxsize=capacity)
            for ev in primed_events:
                queue.put_nowait(ev)

            # Erst NACH dem Priming in _subscribers — vermeidet dass das nächste
            # broadcast() dieselben Events nochmal in die Queue kippt.
            self._subscribers[project_id].add(queue)
            self._presence[project_id].add(username)
            self._queue_user[queue] = (project_id, username)  # #608

            # Presence-Update an alle Clients broadcasten (RLock — nested OK)
            self._broadcast_presence(project_id)

            logger.info(
                "SharedSession: %s subscribed to %s (%d clients, primed=%d events)",
                username, project_id, len(self._subscribers[project_id]), len(primed_events),
            )
        return queue

    def unsubscribe(self, project_id: str, queue: asyncio.Queue, username: str) -> None:
        """Client meldet sich ab."""
        with self._state_lock:  # #807: atomar subs+presence-Cleanup
            subs = self._subscribers.get(project_id)
            if subs:
                subs.discard(queue)
                if not subs:
                    del self._subscribers[project_id]
                    self._turn_owner.pop(project_id, None)
            # #608: Queue aus Mapping entfernen
            self._queue_user.pop(queue, None)

            presence = self._presence.get(project_id)
            if presence:
                presence.remove(username)  # Refcount runter — entfernt nur wenn 0
                if not presence.online_users():
                    del self._presence[project_id]
                else:
                    self._broadcast_presence(project_id)

        logger.info("SharedSession: %s unsubscribed from %s", username, project_id)

    # #726 K2a: Stream-Buffer Management
    def start_stream(self, project_id: str, stream_id: str) -> None:
        """Markiert: für Projekt läuft ab jetzt ein Stream. Wird beim
        orchestrator.stream_chat-Einstieg aufgerufen. Gleichzeitige Streams
        sind per Turn-Lock ausgeschlossen — wenn trotzdem einer aktiv war
        wird er überschrieben (neuer Stream-ID)."""
        with self._state_lock:  # #807
            buf = self._stream_buffers.setdefault(project_id, ProjectStreamBuffer())
            buf.start(stream_id)

    def end_stream(self, project_id: str) -> None:
        """Stream beendet — Buffer leeren."""
        with self._state_lock:  # #807
            buf = self._stream_buffers.get(project_id)
            if buf is not None:
                buf.end()
                # leeren Eintrag abräumen damit dict nicht unkontrolliert wächst
                if not buf.is_active():
                    self._stream_buffers.pop(project_id, None)

    def get_stream_snapshot(self, project_id: str) -> list[str]:
        """Schnappschuss der bisher gesendeten Events im aktuell laufenden
        Stream. Leere Liste wenn kein Stream läuft."""
        with self._state_lock:  # #807: Snapshot unter Lock → Copy via snapshot()
            buf = self._stream_buffers.get(project_id)
            return buf.snapshot() if buf and buf.is_active() else []

    def touch_presence(self, project_id: str, username: str) -> None:
        """#587: Heartbeat — Timestamp aktualisieren waehrend Client verbunden ist.
        Wird vom subscribe-Endpoint bei jedem Keepalive aufgerufen."""
        with self._state_lock:  # #807
            presence = self._presence.get(project_id)
            if presence:
                presence.refresh(username)

    def broadcast(self, project_id: str, event_data: str) -> None:
        """Sendet ein SSE-Event an alle verbundenen Clients eines Projekts.

        event_data: JSON-String der als SSE-Chunk gesendet wird.

        #726 K2a: wenn ein Stream für das Projekt aktiv ist, wird das Event
        zusätzlich in den Replay-Buffer geschrieben — damit spät verbundene
        Subscriber den bisherigen Verlauf zugespielt bekommen können.
        """
        with self._state_lock:  # #807: Snapshot von subs + Cleanup atomar
            buf = self._stream_buffers.get(project_id)
            if buf is not None and buf.is_active():
                buf.append(event_data)

            subs = self._subscribers.get(project_id)
            if not subs:
                return

            # Schnappschuss der Subscriber (subs-Set kann während put_nowait
            # mutiert werden, wenn dead_queue aus derselben Schleife raus
            # fällt — Iteration über Kopie ist sicherer).
            queues_snapshot = tuple(subs)
            dead_queues: set[asyncio.Queue] = set()
            for queue in queues_snapshot:
                try:
                    queue.put_nowait(event_data)
                except asyncio.QueueFull:
                    dead_queues.add(queue)
                    logger.warning("SharedSession: Queue full for %s, dropping client", project_id)

            # Tote Queues aufräumen — #608: inkl. Presence-Cleanup
            presence_changed = False
            for dq in dead_queues:
                subs.discard(dq)
                _queue_info = self._queue_user.pop(dq, None)
                if _queue_info:
                    _, _dead_user = _queue_info
                    presence = self._presence.get(project_id)
                    if presence:
                        presence.remove(_dead_user)
                        presence_changed = True
            if presence_changed:
                if self._presence.get(project_id) and not self._presence[project_id].online_users():
                    del self._presence[project_id]
                else:
                    self._broadcast_presence(project_id)

    def subscriber_count(self, project_id: str) -> int:
        """Anzahl verbundener Clients für ein Projekt."""
        return len(self._subscribers.get(project_id, set()))

    # ── Turn-Lock ──────────────────────────────────────────────────────

    def acquire_turn(self, project_id: str, username: str) -> bool:
        """Versucht den Turn zu übernehmen.

        Returns: True wenn erfolgreich, False wenn jemand anderes dran ist.
        """
        with self._state_lock:  # #807: atomic check-and-set
            current = self._turn_owner.get(project_id)
            if current is not None and current != username:
                return False
            self._turn_owner[project_id] = username
            # Turn-Info broadcasten (RLock — nested OK)
            self.broadcast(project_id, json.dumps({
                "_turn": {"owner": username, "status": "active"}
            }))
            return True

    def release_turn(self, project_id: str, username: str) -> None:
        """Gibt den Turn frei."""
        with self._state_lock:  # #807: atomic check-and-clear
            if self._turn_owner.get(project_id) == username:
                self._turn_owner[project_id] = None
                self.broadcast(project_id, json.dumps({
                    "_turn": {"owner": None, "status": "free"}
                }))

    def turn_owner(self, project_id: str) -> str | None:
        """Wer hat gerade den Turn?"""
        return self._turn_owner.get(project_id)

    # ── Presence ───────────────────────────────────────────────────────

    def online_users(self, project_id: str) -> list[str]:
        """Welche User sind gerade in diesem Projekt online?"""
        presence = self._presence.get(project_id)
        return presence.online_users() if presence else []

    def _broadcast_presence(self, project_id: str) -> None:
        """Sendet aktuelle Presence-Info an alle Clients."""
        users = self.online_users(project_id)
        self.broadcast(project_id, json.dumps({
            "_presence": {"users": users, "count": len(users)}
        }))


# Globale Singleton-Instanz
shared_sessions = SharedSessionManager()
