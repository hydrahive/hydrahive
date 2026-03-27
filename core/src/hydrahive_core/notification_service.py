"""
notification_service.py — In-Process Notification-Center (#46)

Notifications werden In-Memory gehalten (asyncio.Queue pro User) und
in SQLite persistiert so dass sie einen Core-Neustart überleben.

Verwendung von aussen:
    from .notification_service import notification_service
    await notification_service.push(
        user="admin",
        type="task_done",
        title="Rapport fertig",
        body="Der tägliche Rapport wurde erstellt.",
        link="/projects/support",
    )
"""
from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

logger = logging.getLogger(__name__)

DB_PATH = Path("/var/log/hydrahive/notifications.db")


@dataclass
class Notification:
    id:         str
    user:       str
    type:       str
    title:      str
    body:       str
    link:       str | None
    read:       bool
    created_at: str


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class NotificationService:
    def __init__(self) -> None:
        self._queues: dict[str, asyncio.Queue[Notification]] = {}
        self._db: sqlite3.Connection | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    def start(self) -> None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS notifications (
                id         TEXT PRIMARY KEY,
                user       TEXT NOT NULL,
                type       TEXT NOT NULL,
                title      TEXT NOT NULL,
                body       TEXT NOT NULL,
                link       TEXT,
                read       INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_user ON notifications(user)")
        self._db.commit()
        logger.info("NotificationService gestartet (DB: %s)", DB_PATH)

    def stop(self) -> None:
        if self._db:
            self._db.close()
            self._db = None

    # ------------------------------------------------------------------ #
    # Push                                                                 #
    # ------------------------------------------------------------------ #

    async def push(
        self,
        *,
        user: str,
        type: str,
        title: str,
        body: str,
        link: str | None = None,
    ) -> Notification:
        n = Notification(
            id=str(uuid.uuid4()),
            user=user,
            type=type,
            title=title,
            body=body,
            link=link,
            read=False,
            created_at=_now_iso(),
        )
        # Persistieren
        if self._db:
            try:
                self._db.execute(
                    "INSERT INTO notifications VALUES (?,?,?,?,?,?,?,?)",
                    (n.id, n.user, n.type, n.title, n.body, n.link, 0, n.created_at),
                )
                self._db.commit()
            except Exception as e:
                logger.warning("Notification DB-Fehler: %s", e)

        # In Queue für SSE-Subscriber eintragen
        q = self._queues.get(user)
        if q:
            try:
                q.put_nowait(n)
            except asyncio.QueueFull:
                pass

        logger.debug("Notification → %s [%s] %s", user, type, title)
        return n

    # ------------------------------------------------------------------ #
    # Query                                                                #
    # ------------------------------------------------------------------ #

    def get_unread(self, user: str, limit: int = 20) -> list[Notification]:
        if not self._db:
            return []
        rows = self._db.execute(
            "SELECT * FROM notifications WHERE user=? AND read=0 "
            "ORDER BY created_at DESC LIMIT ?",
            (user, limit),
        ).fetchall()
        return [_row_to_notif(r) for r in rows]

    def get_all(self, user: str, limit: int = 50) -> list[Notification]:
        if not self._db:
            return []
        rows = self._db.execute(
            "SELECT * FROM notifications WHERE user=? "
            "ORDER BY created_at DESC LIMIT ?",
            (user, limit),
        ).fetchall()
        return [_row_to_notif(r) for r in rows]

    def unread_count(self, user: str) -> int:
        if not self._db:
            return 0
        row = self._db.execute(
            "SELECT COUNT(*) FROM notifications WHERE user=? AND read=0", (user,)
        ).fetchone()
        return int(row[0]) if row else 0

    # ------------------------------------------------------------------ #
    # Mutations                                                            #
    # ------------------------------------------------------------------ #

    def mark_read(self, notification_id: str, user: str) -> bool:
        if not self._db:
            return False
        cur = self._db.execute(
            "UPDATE notifications SET read=1 WHERE id=? AND user=?",
            (notification_id, user),
        )
        self._db.commit()
        return cur.rowcount > 0

    def mark_all_read(self, user: str) -> int:
        if not self._db:
            return 0
        cur = self._db.execute(
            "UPDATE notifications SET read=1 WHERE user=? AND read=0", (user,)
        )
        self._db.commit()
        return cur.rowcount

    def delete(self, notification_id: str, user: str) -> bool:
        if not self._db:
            return False
        cur = self._db.execute(
            "DELETE FROM notifications WHERE id=? AND user=?",
            (notification_id, user),
        )
        self._db.commit()
        return cur.rowcount > 0

    # ------------------------------------------------------------------ #
    # SSE-Stream                                                           #
    # ------------------------------------------------------------------ #

    async def subscribe(self, user: str) -> AsyncIterator[Notification]:
        """Yield Notifications für `user` sobald sie per push() ankommen."""
        q: asyncio.Queue[Notification] = asyncio.Queue(maxsize=50)
        self._queues[user] = q
        try:
            while True:
                try:
                    notif = await asyncio.wait_for(q.get(), timeout=30)
                    yield notif
                except asyncio.TimeoutError:
                    yield _heartbeat(user)   # keep-alive
        finally:
            self._queues.pop(user, None)


# ------------------------------------------------------------------ #
# Helpers                                                              #
# ------------------------------------------------------------------ #

def _row_to_notif(row: sqlite3.Row) -> Notification:
    return Notification(
        id=row["id"],
        user=row["user"],
        type=row["type"],
        title=row["title"],
        body=row["body"],
        link=row["link"],
        read=bool(row["read"]),
        created_at=row["created_at"],
    )


def _heartbeat(user: str) -> Notification:
    return Notification(
        id="__heartbeat__",
        user=user,
        type="heartbeat",
        title="",
        body="",
        link=None,
        read=True,
        created_at=_now_iso(),
    )


# Singleton
notification_service = NotificationService()
