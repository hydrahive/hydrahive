"""
session_manager.py — Sitzungskonzept (#6)

Jede Arbeitssession hält die Conversation-History die der Boss-Agent
ans LLM übergibt. Ein Projekt hat immer genau eine aktive Session.
Vergangene Sessions werden auf Disk persistiert und können geladen werden.

Analog zu OpenClaw (O2: unverändert übernehmen), integriert in neuen Lifecycle.
"""

import asyncio
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

    def llm_context(
        self,
        max_messages: int = 50,
        prune_tool_results: int = 3,
        max_tool_result_chars: int = 1500,
    ) -> list[dict]:
        """
        Letzten N Nachrichten als LLM-Format. (#78 Session-Pruning)
        - Tool-Results älter als prune_tool_results → Platzhalter
        - Alle Tool-Results werden auf max_tool_result_chars gekürzt (verhindert
          dass große file_read / shell_exec Outputs den Context fluten)
        """
        window = self.messages[-max_messages:]
        cutoff = max(0, len(window) - prune_tool_results)
        result = []
        for i, m in enumerate(window):
            if m.role == MessageRole.TOOL:
                if i < cutoff and len(m.content) > 200:
                    result.append({"role": m.role.value, "content": "[Tool-Result gekürzt — zu weit zurück im Kontext]"})
                elif len(m.content) > max_tool_result_chars:
                    truncated = m.content[:max_tool_result_chars] + f"\n…[gekürzt, {len(m.content)} Zeichen total]"
                    result.append({"role": m.role.value, "content": truncated})
                else:
                    result.append(m.as_llm_message())
            else:
                result.append(m.as_llm_message())
        return result

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
        self._active: dict[str, Session] = {}       # project_id → Session
        self._locks: dict[str, asyncio.Lock] = {}   # project_id → Lock

    def _get_lock(self, project_id: str) -> asyncio.Lock:
        if project_id not in self._locks:
            self._locks[project_id] = asyncio.Lock()
        return self._locks[project_id]

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
        """Gibt aktive Session zurück oder startet eine neue (synchron, ohne Lock)."""
        if project_id not in self._active:
            session = Session.new(project_id)
            self._active[project_id] = session
            self._persist(session)
            logger.info("Neue Session (sync): %s (Projekt: %s)", session.id[:8], project_id)
            return session
        return self._active[project_id]

    async def new_session(self, project_id: str) -> Session:
        """Neue Session starten — alte wird automatisch beendet und gespeichert."""
        async with self._get_lock(project_id):
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

    async def end_session(self, project_id: str) -> Session | None:
        """Aktive Session beenden und speichern."""
        async with self._get_lock(project_id):
            session = self._active.pop(project_id, None)
            if session:
                session.end()
                self._persist(session)
                logger.info("Session %s beendet", session.id[:8])
            if project_id in self._locks:
                del self._locks[project_id]
            return session

    async def append(
        self,
        project_id: str,
        role: MessageRole,
        content: str,
        agent_id: str | None = None,
        **metadata,
    ) -> Message:
        """Nachricht an aktive Session anhängen (Session wird ggf. angelegt)."""
        async with self._get_lock(project_id):
            session = self.get_or_create(project_id)
            message = Message.create(role, content, agent_id=agent_id, **metadata)
            session.append(message)
            self._persist(session)
            return message

    async def replace_messages(self, project_id: str, messages: list[Message]) -> None:
        """Session-Nachrichten komplett ersetzen (für /compact)."""
        async with self._get_lock(project_id):
            session = self.get_or_create(project_id)
            session.messages = messages
            self._persist(session)

    async def pop_last(self, project_id: str) -> None:
        """Letzte Nachricht aus der aktiven Session entfernen (Rollback bei Fehler)."""
        async with self._get_lock(project_id):
            session = self._active.get(project_id)
            if session and session.messages:
                session.messages.pop()
                self._persist(session)

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

    def estimated_tokens(self, project_id: str) -> int:
        """Grobe Token-Schaetzung der aktiven Session (1 Token ≈ 4 Zeichen)."""
        session = self._active.get(project_id)
        if not session:
            return 0
        total_chars = sum(len(m.content) for m in session.messages)
        return total_chars // 4

    def list_sessions(self, project_id: str, limit: int = 20) -> list[dict]:
        """Alle gespeicherten Sessions eines Projekts (neueste zuerst) mit Preview."""
        sessions_dir = self._projects_dir / project_id / self.SESSIONS_SUBDIR
        if not sessions_dir.exists():
            return []
        files = sorted(sessions_dir.glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
        result = []
        for f in files[:limit]:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                msgs = data.get("messages", [])
                first_user = next((m for m in msgs if m["role"] == "user"), None)
                result.append({
                    "id": data["id"],
                    "started_at": data["started_at"],
                    "ended_at": data.get("ended_at"),
                    "message_count": len(msgs),
                    "preview": first_user["content"][:120] if first_user else "",
                })
            except Exception:
                continue
        return result

    def get_session_by_id(self, project_id: str, session_id: str) -> "Session | None":
        """Eine bestimmte Session laden (aktive oder historische)."""
        active = self._active.get(project_id)
        if active and active.id == session_id:
            return active
        path = self._session_dir(project_id) / f"{session_id}.json"
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            return Session.from_dict(data)
        except Exception:
            return None

    async def compact(self, project_id: str, summary: str, keep_last: int = 10) -> None:
        """
        Context-Kompaktierung (#74): ersetzt alte Nachrichten durch eine Summary-Message.
        Die letzten keep_last Nachrichten bleiben erhalten.
        """
        async with self._get_lock(project_id):
            session = self._active.get(project_id)
            if not session or len(session.messages) <= keep_last:
                return
            tail = session.messages[-keep_last:]
            summary_msg = Message.create(
                role=MessageRole.SYSTEM,
                content=f"[Zusammenfassung früherer Konversation]\n{summary}",
            )
            session.messages = [summary_msg] + tail
            self._persist(session)
            logger.info(
                "Session %s kompaktiert: Summary + %d Nachrichten behalten",
                session.id[:8], keep_last,
            )

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
            path.chmod(0o600)
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
