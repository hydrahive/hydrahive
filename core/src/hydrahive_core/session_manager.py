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

    def as_history_message(self) -> dict:
        """Format für Frontend-History — inkl. Metadata (Token-Usage etc.)."""
        d = {"role": self.role.value, "content": self.content, "timestamp": self.timestamp, "agent_id": self.agent_id}
        if self.metadata:
            d["metadata"] = self.metadata
        return d


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
        max_messages: int | None = None,
        prune_tool_results: int = 5,
        max_tool_result_chars: int = 4000,
        max_history_tokens: int | None = None,
    ) -> list[dict]:
        """
        Messages für LLM-Call. Token-Budget ist der primäre Begrenzer (#348).

        OpenClaw-Strategie: Kein hartes Message-Limit. Stattdessen:
        1. Alle Messages laden (neueste zuerst)
        2. Token-Budget füllen bis voll
        3. Tool-Results nach Alter prunen (letzte N vollständig, ältere gekürzt)

        - max_messages: Safety-Cap (default None = nur Token-Budget begrenzt)
        - max_history_tokens: Token-Budget (primärer Begrenzer)
        - prune_tool_results: Letzte N Tool-Results behalten (default 5)
        - max_tool_result_chars: Tool-Result-Limit (default 4000, vorher 1500)
        """
        # Kompaktierungs-Summary immer als erste Nachricht erhalten
        summary_msgs: list = []
        rest = self.messages
        if self.messages and self.messages[0].role == MessageRole.SYSTEM:
            summary_msgs = [self.messages[0]]
            rest = self.messages[1:]

        # Safety-Cap: wenn max_messages gesetzt, als obere Grenze nutzen
        if max_messages is not None and max_messages > 0:
            window = list(rest[-max_messages:])
        else:
            window = list(rest)  # Alle Messages — Token-Budget entscheidet

        # Token-Budget: von hinten nach vorne füllen, älteste entfernen wenn Budget voll
        if max_history_tokens is not None and max_history_tokens > 0:
            while len(window) > 2:
                from .token_estimation import estimate_tokens as _et
                estimated = sum(_et(m.content) for m in window)
                if estimated <= max_history_tokens:
                    break
                window.pop(0)  # älteste Nachricht entfernen

        # Tool-Results prunen: letzte N vollständig, ältere micro-compacted (#416)
        cutoff = max(0, len(window) - prune_tool_results)
        result = [m.as_llm_message() for m in summary_msgs]
        for i, m in enumerate(window):
            if m.role == MessageRole.TOOL:
                if i < cutoff and len(m.content) > 100:
                    # Micro-Compaction: ältere Tool-Results → 100 Chars Preview
                    preview = m.content[:100] + f"\n…[{len(m.content)} Zeichen, micro-compacted]"
                    result.append({"role": m.role.value, "content": preview})
                elif len(m.content) > max_tool_result_chars:
                    truncated = m.content[:max_tool_result_chars] + f"\n…[gekürzt, {len(m.content)} Zeichen total]"
                    result.append({"role": m.role.value, "content": truncated})
                else:
                    result.append(m.as_llm_message())
            else:
                result.append(m.as_llm_message())
        return result

    def history_context(self, max_messages: int = 50) -> list[dict]:
        """Frontend-History inkl. Tool-Messages und Metadata."""
        window = list(self.messages[-max_messages:])
        return [m.as_history_message() for m in window]

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
        """Thread-safe Lock pro Projekt (#354)."""
        return self._locks.setdefault(project_id, asyncio.Lock())

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

    def get_context(
        self,
        project_id: str,
        max_messages: int | None = None,
        max_history_tokens: int | None = None,
    ) -> list[dict]:
        """LLM-Context der aktiven Session (#348: Token-basiert statt max_messages)."""
        session = self._active.get(project_id)
        if not session:
            return []
        return session.llm_context(max_messages, max_history_tokens=max_history_tokens)

    def get_history(self, project_id: str, max_messages: int = 50) -> list[dict]:
        """Frontend-History mit Metadata (Token-Usage etc.)."""
        session = self._active.get(project_id)
        if not session:
            return []
        return session.history_context(max_messages)

    def get_active(self, project_id: str) -> Session | None:
        return self._active.get(project_id)

    def active_projects(self) -> list[str]:
        return list(self._active.keys())

    def get_usage_stats(self, limit_sessions_per_project: int = 50) -> dict:
        """
        Aggregiert Token-Usage aus allen Session-Dateien aller Projekte.
        Liest nur Sessions die echte Usage-Metadata haben (input_tokens > 0).
        Gibt {project_id: {total_input, total_output, total_cache_read, total_cache_write,
                           model_breakdown: {model: {...}}, sessions_with_usage}} zurück.
        """
        result: dict[str, dict] = {}

        for project_dir in self._projects_dir.iterdir():
            if not project_dir.is_dir():
                continue
            project_id = project_dir.name
            sessions_dir = project_dir / self.SESSIONS_SUBDIR
            if not sessions_dir.exists():
                continue

            files = sorted(
                sessions_dir.glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:limit_sessions_per_project]

            proj_stats: dict = {
                "total_input":       0,
                "total_output":      0,
                "total_cache_read":  0,
                "total_cache_write": 0,
                "sessions_with_usage": 0,
                "model_breakdown": {},
            }

            for f in files:
                try:
                    data = json.loads(f.read_text(encoding="utf-8"))
                    has_usage = False
                    for m in data.get("messages", []):
                        meta = m.get("metadata", {})
                        inp = meta.get("input_tokens", 0)
                        out = meta.get("output_tokens", 0)
                        if not (inp or out):
                            continue
                        has_usage = True
                        cw  = meta.get("cache_write_tokens", 0)
                        cr  = meta.get("cache_read_tokens",  0)
                        mdl = meta.get("model", "unknown")
                        proj_stats["total_input"]       += inp
                        proj_stats["total_output"]      += out
                        proj_stats["total_cache_read"]  += cr
                        proj_stats["total_cache_write"] += cw
                        mb = proj_stats["model_breakdown"].setdefault(mdl, {
                            "input": 0, "output": 0, "cache_read": 0, "cache_write": 0,
                        })
                        mb["input"]       += inp
                        mb["output"]      += out
                        mb["cache_read"]  += cr
                        mb["cache_write"] += cw
                    if has_usage:
                        proj_stats["sessions_with_usage"] += 1
                except Exception:
                    continue

            if proj_stats["total_input"] or proj_stats["total_output"]:
                result[project_id] = proj_stats

        return result

    def estimated_tokens(self, project_id: str) -> int:
        """Grobe Token-Schaetzung der aktiven Session (1 Token ≈ 4 Zeichen)."""
        session = self._active.get(project_id)
        if not session:
            return 0
        from .token_estimation import estimate_tokens
        return sum(estimate_tokens(m.content) for m in session.messages)

    async def resume_session(self, project_id: str, session_id: str) -> "Session | None":
        """Lädt eine historische Session und setzt sie als aktive Session."""
        async with self._get_lock(project_id):
            # Aktive Session beenden (außer es ist dieselbe)
            if project_id in self._active:
                old = self._active.pop(project_id)
                if old.id != session_id:
                    old.end()
                    self._persist(old)

            # Session von Disk laden
            path = self._session_dir(project_id) / f"{session_id}.json"
            if not path.exists():
                return None
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                session = Session.from_dict(data)
            except Exception as e:
                logger.warning("resume_session: Fehler beim Laden von %s: %s", session_id, e)
                return None

            # Als aktiv setzen — ended_at zurücksetzen
            session.ended_at = None
            self._active[project_id] = session
            self._persist(session)
            logger.info("Session %s als aktive Session für %s wiederhergestellt (%d Nachrichten)",
                        session_id[:8], project_id, len(session.messages))
            return session

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
        """Atomic Write mit WAL: WAL-append → tmp-write → rename (#358, #393)."""
        path = self._session_dir(session.project_id) / f"{session.id}.json"
        wal_path = path.with_suffix(".wal")
        tmp = path.with_suffix(".json.tmp")
        try:
            data = json.dumps(session.to_dict(), ensure_ascii=False, indent=2)
            # #393: WAL — letzten State vor Write sichern (Crash-Recovery)
            try:
                with wal_path.open("a", encoding="utf-8") as wal:
                    wal.write(f"{datetime.now(timezone.utc).isoformat()}|{len(session.messages)}\n")
            except OSError:
                pass  # WAL-Fehler ist nicht fatal
            tmp.write_text(data, encoding="utf-8")
            tmp.chmod(0o600)
            tmp.replace(path)  # atomic auf POSIX
            # WAL nach erfolgreichem Write löschen
            wal_path.unlink(missing_ok=True)
        except OSError as e:
            logger.warning("Session konnte nicht gespeichert werden: %s", e)
            tmp.unlink(missing_ok=True)

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
