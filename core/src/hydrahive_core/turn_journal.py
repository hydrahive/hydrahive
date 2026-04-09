"""
turn_journal.py — Append-only Event-Log für Turns und Tool-Calls (#523)

Zeichnet Key Events pro Session auf für Audit und Resume-Recovery.
SQLite-basiert, append-only, keine Updates/Deletes.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import time
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path("/var/log/hydrahive/turn_journal.db")


class EventType(str, Enum):
    USER_MESSAGE = "user_message"
    ASSISTANT_DELTA = "assistant_delta"
    TOOL_USE = "tool_use"
    TOOL_RESULT = "tool_result"
    COMPACTION = "compaction"
    RETRY = "retry"
    FAILOVER = "failover"
    OVERFLOW = "overflow"
    SESSION_RESET = "session_reset"
    SESSION_RESUME = "session_resume"
    VERIFICATION = "verification"


class TurnJournal:
    """Append-only Event-Log pro Session."""

    def __init__(self, db_path: Path = DB_PATH):
        self._db_path = db_path
        self._db: sqlite3.Connection | None = None
        self._init_db()

    def _init_db(self) -> None:
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("""
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    project_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    data TEXT DEFAULT '{}',
                    tool_name TEXT,
                    tool_call_id TEXT
                )
            """)
            self._db.execute("""
                CREATE INDEX IF NOT EXISTS idx_events_session
                ON events (session_id, id)
            """)
            self._db.commit()
            logger.info("TurnJournal initialisiert (%s)", self._db_path)
        except Exception as e:
            logger.warning("TurnJournal init fehlgeschlagen: %s", e)
            self._db = None

    def append(
        self,
        session_id: str,
        project_id: str,
        event_type: EventType,
        data: dict | None = None,
        tool_name: str | None = None,
        tool_call_id: str | None = None,
    ) -> None:
        """Event an das Journal anhängen (fire-and-forget, nie Fehler werfen)."""
        if not self._db:
            return
        try:
            self._db.execute(
                "INSERT INTO events (session_id, project_id, event_type, timestamp, data, tool_name, tool_call_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    session_id, project_id, event_type.value,
                    datetime.now(timezone.utc).isoformat(),
                    json.dumps(data or {}, default=str),
                    tool_name, tool_call_id,
                ),
            )
            self._db.commit()
        except Exception as e:
            logger.debug("TurnJournal append error: %s", e)

    def get_session_events(
        self, session_id: str, limit: int = 200, event_type: str | None = None,
    ) -> list[dict]:
        """Events einer Session abrufen (neueste zuerst)."""
        if not self._db:
            return []
        try:
            if event_type:
                rows = self._db.execute(
                    "SELECT * FROM events WHERE session_id = ? AND event_type = ? ORDER BY id DESC LIMIT ?",
                    (session_id, event_type, limit),
                ).fetchall()
            else:
                rows = self._db.execute(
                    "SELECT * FROM events WHERE session_id = ? ORDER BY id DESC LIMIT ?",
                    (session_id, limit),
                ).fetchall()
            return [
                {
                    "id": r[0], "session_id": r[1], "project_id": r[2],
                    "event_type": r[3], "timestamp": r[4],
                    "data": json.loads(r[5]) if r[5] else {},
                    "tool_name": r[6], "tool_call_id": r[7],
                }
                for r in rows
            ]
        except Exception as e:
            logger.debug("TurnJournal query error: %s", e)
            return []

    def get_project_stats(self, project_id: str) -> dict:
        """Aggregierte Stats für ein Projekt."""
        if not self._db:
            return {}
        try:
            total = self._db.execute(
                "SELECT COUNT(*) FROM events WHERE project_id = ?", (project_id,)
            ).fetchone()[0]
            by_type = self._db.execute(
                "SELECT event_type, COUNT(*) FROM events WHERE project_id = ? GROUP BY event_type",
                (project_id,),
            ).fetchall()
            return {
                "total_events": total,
                "by_type": {r[0]: r[1] for r in by_type},
            }
        except Exception:
            return {}

    def cleanup(self, max_age_days: int = 30) -> int:
        """Alte Events entfernen. Returns: Anzahl gelöschter Events."""
        if not self._db:
            return 0
        try:
            cutoff = datetime.now(timezone.utc).isoformat()[:10]  # Heute
            # Einfach: alles älter als N Tage
            from datetime import timedelta
            cutoff_dt = datetime.now(timezone.utc) - timedelta(days=max_age_days)
            cutoff = cutoff_dt.isoformat()
            r = self._db.execute(
                "DELETE FROM events WHERE timestamp < ?", (cutoff,)
            )
            self._db.commit()
            deleted = r.rowcount
            if deleted:
                logger.info("TurnJournal cleanup: %d Events älter als %d Tage entfernt", deleted, max_age_days)
            return deleted
        except Exception as e:
            logger.debug("TurnJournal cleanup error: %s", e)
            return 0


# Globale Singleton-Instanz
journal = TurnJournal()
