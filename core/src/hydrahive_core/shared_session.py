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
import time
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class ProjectPresence:
    """Tracking welche User gerade in einem Projekt online sind."""
    users: dict[str, float] = field(default_factory=dict)  # username → last_seen timestamp

    def add(self, username: str) -> None:
        self.users[username] = time.time()

    def refresh(self, username: str) -> None:
        """#587: Timestamp erneuern waehrend Client verbunden ist."""
        if username in self.users:
            self.users[username] = time.time()

    def remove(self, username: str) -> None:
        self.users.pop(username, None)

    def online_users(self) -> list[str]:
        """Alle User die in den letzten 60s aktiv waren."""
        cutoff = time.time() - 60
        return [u for u, ts in self.users.items() if ts > cutoff]


class SharedSessionManager:
    """Verwaltet Shared Sessions für alle Projekte."""

    def __init__(self) -> None:
        # project_id → Set von asyncio.Queues (eine pro Client-Verbindung)
        self._subscribers: dict[str, set[asyncio.Queue]] = {}
        # project_id → username der gerade den Turn hat (None = frei)
        self._turn_owner: dict[str, str | None] = {}
        # project_id → Presence-Tracking
        self._presence: dict[str, ProjectPresence] = {}

    def subscribe(self, project_id: str, username: str) -> asyncio.Queue:
        """Client meldet sich an — bekommt eine Queue für SSE-Events.

        Returns: asyncio.Queue die der SSE-Endpoint lesen kann.
        """
        self._subscribers.setdefault(project_id, set())
        self._presence.setdefault(project_id, ProjectPresence())

        queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers[project_id].add(queue)
        self._presence[project_id].add(username)

        # Presence-Update an alle Clients broadcasten
        self._broadcast_presence(project_id)

        logger.info("SharedSession: %s subscribed to %s (%d clients)",
                     username, project_id, len(self._subscribers[project_id]))
        return queue

    def unsubscribe(self, project_id: str, queue: asyncio.Queue, username: str) -> None:
        """Client meldet sich ab."""
        subs = self._subscribers.get(project_id)
        if subs:
            subs.discard(queue)
            if not subs:
                del self._subscribers[project_id]
                self._turn_owner.pop(project_id, None)

        presence = self._presence.get(project_id)
        if presence:
            presence.remove(username)
            if not presence.online_users():
                del self._presence[project_id]
            else:
                self._broadcast_presence(project_id)

        logger.info("SharedSession: %s unsubscribed from %s", username, project_id)

    def touch_presence(self, project_id: str, username: str) -> None:
        """#587: Heartbeat — Timestamp aktualisieren waehrend Client verbunden ist.
        Wird vom subscribe-Endpoint bei jedem Keepalive aufgerufen."""
        presence = self._presence.get(project_id)
        if presence:
            presence.refresh(username)

    def broadcast(self, project_id: str, event_data: str) -> None:
        """Sendet ein SSE-Event an alle verbundenen Clients eines Projekts.

        event_data: JSON-String der als SSE-Chunk gesendet wird.
        """
        subs = self._subscribers.get(project_id)
        if not subs:
            return

        dead_queues = set()
        for queue in subs:
            try:
                queue.put_nowait(event_data)
            except asyncio.QueueFull:
                # Queue voll → Client ist zu langsam, disconnecten
                dead_queues.add(queue)
                logger.warning("SharedSession: Queue full for %s, dropping client", project_id)

        # Tote Queues aufräumen
        for dq in dead_queues:
            subs.discard(dq)

    def subscriber_count(self, project_id: str) -> int:
        """Anzahl verbundener Clients für ein Projekt."""
        return len(self._subscribers.get(project_id, set()))

    # ── Turn-Lock ──────────────────────────────────────────────────────

    def acquire_turn(self, project_id: str, username: str) -> bool:
        """Versucht den Turn zu übernehmen.

        Returns: True wenn erfolgreich, False wenn jemand anderes dran ist.
        """
        current = self._turn_owner.get(project_id)
        if current is not None and current != username:
            return False
        self._turn_owner[project_id] = username
        # Turn-Info broadcasten
        self.broadcast(project_id, json.dumps({
            "_turn": {"owner": username, "status": "active"}
        }))
        return True

    def release_turn(self, project_id: str, username: str) -> None:
        """Gibt den Turn frei."""
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
