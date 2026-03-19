"""
session_manager.py — Sitzungskonzept (#6)

Jede Arbeitssession hält die Conversation-History die der Boss-Agent
ans LLM übergibt. Ein Projekt hat immer genau eine aktive Session.
Vergangene Sessions werden auf Disk persistiert und können geladen werden.

Analog zu OpenClaw (O2: unverändert übernehmen), integriert in neuen Lifecycle.
"""

import json
import logging
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

logger = logging.getLogger(__name__)


class MessageRole(str, Enum):
    USER      = "user"
    ASSISTANT = "assistant"
    SYSTEM    = "system"
    TOOL      = "tool"


@dataclass
class Message:
    role:      MessageRole
    content:   str
    timestamp: str                  # ISO-8601
    agent_id:  str | None = None    # welcher Agent hat das produziert
    metadata:  dict = field(default_factory=dict)

    @classmethod
    def create(
        cls,
        role: MessageRole,
        content: str,
        agent_id: str | None = None,
        **metadata,
    ) -> "Message":
        return cls(
            role=role,
            content=content,
            timestamp=datetime.now(timezone.utc).isoformat(),
            agent_id=agent_id,
            metadata=metadata,
        )

    def as_llm_message(self) -> dict:
        """Format für LLM-API (OpenAI-kompatibel via litellm)."""
        return {"role": self.role.value, "content": self.content}


@dataclass
class Session:
    id:          str
    project_id:  str
    started_at:  str                    # ISO-8601
    ended_at:    str | None = None
    messages:    list[Message] = field(default_factory=list)

    @classmethod
    def new(cls, project_id: str) -> "Session":
        return cls(
            id=str(uuid.uuid4()),
            project_id=project_id,
            started_at=datetime.now(timezone.utc).isoformat(),
        )

    def append(self, message: Message) -> None:
        self.messages.append(message)

    def end(self) -> None:
        self.ended_at = datetime.now(timezone.utc).isoformat()

    def llm_context(self, max_messages: int = 50) -> list[dict]:
        """Letzten N Nachrichten als LLM-Format."""
        return [m.as_llm_message() for m in self.messages[-max_messages:]]

    def to_dict(self) -> dict:
        d = asdict(self)
        d["messages"] = [
            {**asdict(m), "role": m.role.value}
            for m in self.messages
        ]
        return d

    @classmethod
    def from_dict(cls, data: dict) -> "Session":
        messages = [
            Message(
                role=MessageRole(m["role"]),
                content=m["content"],
                timestamp=m["timestamp"],
                agent_id=m.get("agent_id"),
                metadata=m.get("metadata", {}),
            )
            for m in data.get("messages", [])
        ]
        return cls(
            id=data["id"],
            project_id=data["project_id"],
            started_at=data["started_at"],
            ended_at=data.get("ended_at"),
            messages=messages,
        )


class SessionManager:
    """
    Ein SessionManager pro Core-Instanz.
    Hält eine aktive Session pro Projekt, persistiert auf Disk.
    """

    SESSIONS_SUBDIR = ".sessions"

    def __init__(self, projects_dir: str | Path = "/projects") -> None:
        self._projects_dir = Path(projects_dir)
        self._active: dict[str, Session] = {}    # project_id → Session

    # ------------------------------------------------------------------ public

    def start(self) -> None:
        """Aktive Sessions aus Disk wiederherstellen (nach Core-Neustart)."""
        restored = 0
        for project_dir in self._projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            session = self._load_latest(project_dir)
            if session and session.ended_at is None:
                self._active[session.project_id] = session
                restored += 1
        logger.info("SessionManager gestartet — %d aktive Sessions wiederhergestellt", restored)

    def get_or_create(self, project_id: str) -> Session:
        """Gibt aktive Session zurück oder startet eine neue."""
        if project_id not in self._active:
            return self.new_session(project_id)
        return self._active[project_id]

    def new_session(self, project_id: str) -> Session:
        """Neue Session starten — alte wird automatisch beendet und gespeichert."""
        if project_id in self._active:
            old = self._active[project_id]
            old.end()
            self._persist(old)
            logger.info("Session beendet: %s (Projekt: %s, %d Nachrichten)",
                        old.id[:8], project_id, len(old.messages))

        session = Session.new(project_id)
        self._active[project_id] = session
        self._persist(session)
        logger.info("Neue Session: %s (Projekt: %s)", session.id[:8], project_id)
        return session

    def end_session(self, project_id: str) -> Session | None:
        """Aktive Session beenden und speichern."""
        session = self._active.pop(project_id, None)
        if session:
            session.end()
            self._persist(session)
            logger.info("Session %s beendet", session.id[:8])
        return session

    def append(
        self,
        project_id: str,
        role: MessageRole,
        content: str,
        agent_id: str | None = None,
        **metadata,
    ) -> Message:
        """Nachricht an aktive Session anhängen (Session wird ggf. angelegt)."""
        session = self.get_or_create(project_id)
        message = Message.create(role, content, agent_id=agent_id, **metadata)
        session.append(message)
        self._persist(session)
        return message

    def get_context(self, project_id: str, max_messages: int = 50) -> list[dict]:
        """LLM-Context der aktiven Session (für Boss-Agent in #8)."""
        session = self._active.get(project_id)
        if not session:
            return []
        return session.llm_context(max_messages)

    def get_active(self, project_id: str) -> Session | None:
        return self._active.get(project_id)

    def active_projects(self) -> list[str]:
        return list(self._active.keys())

    # ----------------------------------------------------------------- private

    def _session_dir(self, project_id: str) -> Path:
        d = self._projects_dir / project_id / self.SESSIONS_SUBDIR
        d.mkdir(parents=True, exist_ok=True)
        return d

    def _persist(self, session: Session) -> None:
        path = self._session_dir(session.project_id) / f"{session.id}.json"
        try:
            path.write_text(
                json.dumps(session.to_dict(), ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except OSError as e:
            logger.warning("Session konnte nicht gespeichert werden: %s", e)

    def _load_latest(self, project_dir: Path) -> Session | None:
        sessions_dir = project_dir / self.SESSIONS_SUBDIR
        if not sessions_dir.exists():
            return None
        files = sorted(sessions_dir.glob("*.json"), key=lambda p: p.stat().st_mtime)
        if not files:
            return None
        try:
            data = json.loads(files[-1].read_text(encoding="utf-8"))
            return Session.from_dict(data)
        except Exception as e:
            logger.warning("Session-Datei konnte nicht geladen werden (%s): %s", files[-1], e)
            return None
