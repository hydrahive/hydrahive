"""
session_manager.py — Sitzungskonzept (#6, #395 SQLite)

Jede Arbeitssession hält die Conversation-History die der Boss-Agent
ans LLM übergibt. Ein Projekt hat immer genau eine aktive Session.
Vergangene Sessions werden auf Disk persistiert und können geladen werden.

Persistence via SQLite (WAL-Mode) statt JSON-Dateien (#395).
In-Memory-Cache für aktive Sessions bleibt bestehen.
"""

import asyncio
import json
import logging
import sqlite3
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


def redact_thinking_blocks(messages: list[dict]) -> list[dict]:
    """Redact thinking blocks from older assistant messages (#473).

    Extended Thinking (Claude 3.7+/Sonnet 4/Opus 4) returns thinking blocks
    in responses. These are useful for the current turn but waste massive
    tokens when re-sent in history. This function replaces thinking content
    with a minimal placeholder for all assistant messages except the last one.

    Handles both:
    - List-of-blocks content: [{"type": "thinking", "thinking": "..."}, ...]
    - String content with embedded JSON thinking markers (safety net)
    """
    if not messages:
        return messages

    # Find index of last assistant message
    last_asst_idx = -1
    for i in range(len(messages) - 1, -1, -1):
        if messages[i].get("role") == "assistant":
            last_asst_idx = i
            break

    result = []
    for i, msg in enumerate(messages):
        if msg.get("role") != "assistant" or i == last_asst_idx:
            result.append(msg)
            continue

        content = msg.get("content")
        if isinstance(content, list):
            # List-of-blocks format — redact thinking blocks
            new_blocks = []
            for block in content:
                if isinstance(block, dict) and block.get("type") == "thinking":
                    new_blocks.append({"type": "thinking", "thinking": "[redacted]"})
                else:
                    new_blocks.append(block)
            result.append({**msg, "content": new_blocks})
        else:
            # String content — no thinking blocks to redact
            result.append(msg)

    return result


def repair_tool_pairs(messages: list[dict]) -> list[dict]:
    """#507: Verwaiste tool_use/tool_result Paare reparieren.

    Beim Session-Resume können inkonsistente Paare auftreten:
    - tool-Messages ohne vorheriges assistant-Message mit tool_calls
    - assistant-Messages mit tool_calls ohne nachfolgende tool-Results

    Diese Funktion entfernt verwaiste tool-Messages und fügt
    Dummy-Results für verwaiste tool_calls ein.
    """
    if not messages:
        return messages

    result: list[dict] = []
    i = 0
    while i < len(messages):
        msg = messages[i]

        # Tool-Messages ohne Kontext → entfernen (verwaist)
        if msg.get("role") == "tool":
            # Prüfen ob vorherige Message ein assistant mit tool_calls ist
            has_parent = False
            for j in range(len(result) - 1, -1, -1):
                if result[j].get("role") == "assistant":
                    if result[j].get("tool_calls"):
                        has_parent = True
                    break
                if result[j].get("role") == "tool":
                    has_parent = True  # Teil einer Tool-Gruppe
                    break
            if not has_parent:
                # Verwaiste tool-Message → überspringen
                i += 1
                continue

        # Assistant mit tool_calls → prüfen ob tool-Results folgen
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            result.append(msg)
            tc_ids = [tc.get("id") or tc.get("tool_call_id", f"tc_{k}")
                      for k, tc in enumerate(msg["tool_calls"])]
            # Nachfolgende tool-Messages sammeln
            found_ids: set[str] = set()
            j = i + 1
            while j < len(messages) and messages[j].get("role") == "tool":
                tid = messages[j].get("tool_call_id", "")
                if tid:
                    found_ids.add(tid)
                result.append(messages[j])
                j += 1
            # Fehlende tool-Results mit Dummy auffüllen
            for tc_id in tc_ids:
                if tc_id not in found_ids:
                    result.append({
                        "role": "tool",
                        "tool_call_id": tc_id,
                        "content": "[Session unterbrochen — Ergebnis nicht verfügbar]",
                    })
            i = j
            continue

        result.append(msg)
        i += 1

    return result


def _merge_consecutive_roles(messages: list[dict]) -> list[dict]:
    """Consecutive Messages mit gleicher Role mergen.

    Anthropic erfordert strikten user/assistant Wechsel.
    Tool-Call + Tool-Result erzeugen sonst zwei assistant-Messages
    hintereinander → 400 Error.

    #637-Sicherheitsregel: Messages mit `tool_calls` werden NIE gemerged —
    sonst gehen die strukturierten Tool-Call-Daten still verloren oder
    zwei Tool-Roundtrips werden zu einem verschmolzen. Lieber Anthropic-
    Validierung sprechen lassen als stillen Datenverlust.
    """
    if not messages:
        return messages
    merged: list[dict] = [messages[0]]
    for msg in messages[1:]:
        prev = merged[-1]
        same_role = msg.get("role") == prev.get("role")
        mergeable_role = msg.get("role") in ("assistant", "user")
        # tool_calls auf einer der beiden Seiten → Merge unterbinden,
        # sonst wäre der Tool-Roundtrip strukturell beschädigt.
        has_tool_calls = bool(prev.get("tool_calls")) or bool(msg.get("tool_calls"))
        if same_role and mergeable_role and not has_tool_calls:
            prev_content = prev.get("content", "") or ""
            new_content = msg.get("content", "") or ""
            if prev_content and new_content:
                prev["content"] = prev_content + "\n" + new_content
            elif new_content:
                prev["content"] = new_content
        else:
            merged.append(msg)
    return merged


@dataclass
class Message:
    role:         MessageRole
    content:      str
    timestamp:    str                  # ISO-8601
    msg_id:       str | None = None    # Unique ID pro Message (#477)
    agent_id:     str | None = None    # welcher Agent hat das produziert
    metadata:     dict = field(default_factory=dict)
    tool_calls:   list[dict] | None = None   # OpenAI-Format tool_calls (assistant messages)
    tool_call_id: str | None = None          # tool_call_id (tool result messages)

    @classmethod
    def create(
        cls,
        role: MessageRole,
        content: str,
        agent_id: str | None = None,
        tool_calls: list[dict] | None = None,
        tool_call_id: str | None = None,
        **metadata,
    ) -> "Message":
        return cls(
            role=role,
            content=content,
            timestamp=datetime.now(timezone.utc).isoformat(),
            msg_id=uuid.uuid4().hex[:8],
            agent_id=agent_id,
            metadata=metadata,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
        )

    def as_llm_message(self) -> dict:
        """#637: Format für LLM-API — kanonisches OpenAI-Schema.

        Tool-Events bleiben strukturiert, Provider-Adapter (Anthropic-OAuth)
        konvertieren erst beim Senden zu Anthropic-content-Block-Liste.
        litellm akzeptiert OpenAI-Format nativ.
        """
        # Assistant mit Tool-Calls → strukturiert mit tool_calls-Feld
        if self.tool_calls and self.role == MessageRole.ASSISTANT:
            return {
                "role": "assistant",
                "content": self.content or "",
                "tool_calls": self.tool_calls,
            }

        # Tool-Result mit tool_call_id → role:"tool"
        if self.tool_call_id and self.role == MessageRole.TOOL:
            return {
                "role": "tool",
                "tool_call_id": self.tool_call_id,
                "content": self.content or "",
            }

        # Legacy-Fallback: TOOL-Messages OHNE tool_call_id (alte DB-Einträge
        # vor #637). Ohne tool_call_id kein gültiges OpenAI tool-Pair → als
        # assistant-Text. Eng begrenzt, sichtbar markiert. Kein neuer Drift.
        if self.role == MessageRole.TOOL:
            return {"role": "assistant", "content": self.content}

        # Standard-Messages
        prefix = ""
        if self.role == MessageRole.USER:
            try:
                dt = datetime.fromisoformat(self.timestamp)
                prefix = f"[{dt.strftime('%H:%M:%S')}] "
            except (ValueError, TypeError):
                pass
        return {"role": self.role.value, "content": prefix + self.content}

    def as_history_message(self) -> dict:
        """Format für Frontend-History — inkl. Metadata (Token-Usage etc.)."""
        d = {
            "role": self.role.value, "content": self.content,
            "timestamp": self.timestamp, "agent_id": self.agent_id,
            "msg_id": self.msg_id,
        }
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
    # #630: Working-Memory-Snapshot bei Resume (None für frische Sessions)
    working_state: object | None = None

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

        # #516: Tool-Result-Budgeting mit Tool-Typ-Bewusstsein
        # Verschiedene Budgets je nach Tool-Typ (mutation/read/search/meta)
        from .context_lifecycle import budget_tool_result
        now = datetime.now(timezone.utc)

        def _extract_tool_name(msg: Message) -> str:
            """Tool-Name aus metadata oder Content-Prefix extrahieren."""
            tn = msg.metadata.get("tool_name", "")
            if tn:
                return tn
            # Fallback: TOOL-Messages haben Format "tool_name|detail"
            if "|" in msg.content[:80]:
                return msg.content.split("|", 1)[0].strip()
            return ""

        # Position-from-end pro Tool-Typ berechnen (neueste zuerst zählen)
        _tool_type_counters: dict[str, int] = {}
        _tool_positions: list[int] = []
        for i in range(len(window) - 1, -1, -1):
            m = window[i]
            if m.role == MessageRole.TOOL:
                tn = _extract_tool_name(m)
                pos = _tool_type_counters.get(tn, 0)
                _tool_type_counters[tn] = pos + 1
                _tool_positions.insert(0, pos)
            else:
                _tool_positions.insert(0, -1)

        result = [m.as_llm_message() for m in summary_msgs]
        for i, m in enumerate(window):
            if m.role == MessageRole.TOOL:
                # #637: Tool-Result-Budgeting bleibt — wirkt jetzt auf den
                # content der strukturierten role:tool-Message statt durch
                # Rollenwechsel.
                tool_name = _extract_tool_name(m)
                try:
                    msg_time = datetime.fromisoformat(m.timestamp)
                    age_minutes = (now - msg_time).total_seconds() / 60
                except (ValueError, TypeError):
                    age_minutes = 0
                pos_from_end = _tool_positions[i] if i < len(_tool_positions) else 0
                budgeted = budget_tool_result(
                    m.content, tool_name, pos_from_end, age_minutes,
                )
                msg_dict = m.as_llm_message()
                # Nur überschreiben falls budgeting den content gekürzt hat
                # (Legacy-Fallback ohne tool_call_id liefert role:assistant —
                # auch dort budgeting auf content anwenden).
                msg_dict["content"] = budgeted
                result.append(msg_dict)
            else:
                result.append(m.as_llm_message())

        # #473: Redact thinking blocks from older assistant messages
        result = redact_thinking_blocks(result)
        # #507: Verwaiste tool_use/tool_result Paare reparieren
        result = repair_tool_pairs(result)
        # Consecutive same-role Messages mergen (Anthropic erfordert user/assistant Wechsel)
        result = _merge_consecutive_roles(result)
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
                msg_id=m.get("msg_id"),
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


def group_messages_by_api_round(messages: list[Message]) -> list[list[Message]]:
    """Gruppiert Messages nach API-Round Boundaries (#477).

    Angelehnt an Claude Code grouping.ts. Eine Gruppe = ein kompletter
    User-Turn: [user, assistant, tool, tool, assistant, ...] — alles was
    zwischen zwei USER-Messages passiert gehört zusammen.

    SYSTEM-Messages am Anfang (Zusammenfassung) werden in eine eigene
    Gruppe gepackt und bei Compaction separat behandelt.

    Garantiert: Tool-Results werden nie von ihrem auslösenden Assistant
    getrennt, weil der ganze Tool-Loop in einer Gruppe bleibt.
    """
    if not messages:
        return []

    groups: list[list[Message]] = []
    current: list[Message] = []

    for msg in messages:
        # SYSTEM am Anfang (Compaction-Summary) → eigene Gruppe
        if msg.role == MessageRole.SYSTEM and not current and not groups:
            groups.append([msg])
            continue

        # USER startet immer eine neue Gruppe
        if msg.role == MessageRole.USER and current:
            groups.append(current)
            current = [msg]
        else:
            current.append(msg)

    if current:
        groups.append(current)

    return groups


class SessionManager:
    """
    Ein SessionManager pro Core-Instanz.
    Hält eine aktive Session pro Projekt, persistiert in SQLite (#395).
    """

    SESSIONS_SUBDIR = ".sessions"  # Legacy, für JSON-Migration

    def __init__(self, projects_dir: str | Path = "/projects") -> None:
        self._projects_dir = Path(projects_dir)
        self._active: dict[str, Session] = {}       # project_id → Session
        self._locks: dict[str, asyncio.Lock] = {}   # project_id → Lock
        self._db: sqlite3.Connection | None = None

    def _get_lock(self, project_id: str) -> asyncio.Lock:
        """Thread-safe Lock pro Projekt (#354)."""
        return self._locks.setdefault(project_id, asyncio.Lock())

    # ------------------------------------------------------------------ DB init

    def _init_db(self) -> sqlite3.Connection:
        """SQLite-DB öffnen und Schema erstellen."""
        self._projects_dir.mkdir(parents=True, exist_ok=True)
        db_path = self._projects_dir / "sessions.db"
        db = sqlite3.connect(str(db_path), check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA foreign_keys=ON")
        db.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id            TEXT PRIMARY KEY,
                project_id    TEXT NOT NULL,
                started_at    TEXT NOT NULL,
                ended_at      TEXT,
                message_count INTEGER NOT NULL DEFAULT 0,
                preview       TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS idx_sessions_project
                ON sessions(project_id, started_at DESC);

            CREATE TABLE IF NOT EXISTS messages (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                msg_id      TEXT,
                role        TEXT NOT NULL,
                content     TEXT NOT NULL,
                timestamp   TEXT NOT NULL,
                agent_id    TEXT,
                metadata    TEXT NOT NULL DEFAULT '{}',
                seq         INTEGER NOT NULL,
                input_tokens       INTEGER NOT NULL DEFAULT 0,
                output_tokens      INTEGER NOT NULL DEFAULT 0,
                cache_read_tokens  INTEGER NOT NULL DEFAULT 0,
                cache_write_tokens INTEGER NOT NULL DEFAULT 0,
                model              TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_messages_session
                ON messages(session_id, seq);
            CREATE INDEX IF NOT EXISTS idx_messages_usage
                ON messages(input_tokens) WHERE input_tokens > 0;

            -- #630: Working-Memory Snapshots pro Turn (siehe working_state.py)
            CREATE TABLE IF NOT EXISTS session_snapshots (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id  TEXT NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
                created_at  TEXT NOT NULL,
                turn_seq    INTEGER NOT NULL DEFAULT 0,
                state_json  TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_snapshots_session
                ON session_snapshots(session_id, id DESC);
        """)
        db.commit()
        return db

    # ------------------------------------------------------------------ #630 Snapshots

    def save_snapshot(self, session_id: str, state, turn_seq: int = 0) -> None:
        """#630: Speichert einen Working-Memory-Snapshot am Turn-Ende.

        Nimmt ein WorkingState-Objekt (siehe working_state.py) oder ein dict
        und persistiert es als JSON. Fehler werden geschluckt — Snapshots
        sind eine Komfort-Schicht, kein kritischer Pfad.
        """
        if not self._db:
            return
        try:
            from .working_state import WorkingState, now_iso
            if isinstance(state, dict):
                state = WorkingState(**state)
            if not state.created_at:
                state.created_at = now_iso()
            self._db.execute(
                "INSERT INTO session_snapshots (session_id, created_at, turn_seq, state_json) "
                "VALUES (?, ?, ?, ?)",
                (session_id, state.created_at, int(turn_seq), state.to_json()),
            )
            self._db.commit()
        except Exception as e:
            logger.debug("save_snapshot Fehler (%s): %s", session_id[:8], e)

    def load_latest_snapshot(self, session_id: str):
        """#630: Lädt den jüngsten Snapshot zu einer Session oder None."""
        if not self._db:
            return None
        try:
            row = self._db.execute(
                "SELECT state_json FROM session_snapshots "
                "WHERE session_id = ? ORDER BY id DESC LIMIT 1",
                (session_id,),
            ).fetchone()
            if not row:
                return None
            from .working_state import WorkingState
            return WorkingState.from_json(row["state_json"])
        except Exception as e:
            logger.debug("load_latest_snapshot Fehler (%s): %s", session_id[:8], e)
            return None

    # ------------------------------------------------------------------ public

    def start(self) -> None:
        """DB initialisieren und aktive Sessions wiederherstellen."""
        self._db = self._init_db()

        restored = 0
        rows = self._db.execute(
            "SELECT id, project_id, started_at, ended_at FROM sessions WHERE ended_at IS NULL"
        ).fetchall()
        for row in rows:
            session = Session(
                id=row["id"],
                project_id=row["project_id"],
                started_at=row["started_at"],
                ended_at=None,
                messages=self._load_messages(row["id"]),
            )
            self._active[session.project_id] = session
            restored += 1
        logger.info("SessionManager gestartet (SQLite) — %d aktive Sessions wiederhergestellt", restored)

    def get_or_create(self, project_id: str) -> Session:
        """Gibt aktive Session zurück oder startet eine neue (synchron, ohne Lock)."""
        if project_id not in self._active:
            session = Session.new(project_id)
            self._active[project_id] = session
            self._db_insert_session(session)
            logger.info("Neue Session (sync): %s (Projekt: %s)", session.id[:8], project_id)
            return session
        return self._active[project_id]

    def _write_session_memory(self, project_id: str, session: "Session") -> None:
        """Schreibt eine Zusammenfassung der Session in die Projekt-Memory.

        Wird beim Session-Ende aufgerufen wenn die Session >= MIN_MSGS Nachrichten hat.
        Die letzte Zusammenfassung überschreibt die vorherige (nur 1 Datei pro Projekt).
        """
        MIN_MSGS = 4  # Mindestanzahl Nachrichten für einen Memory-Eintrag
        MAX_CONTENT = 800  # Zeichen pro Nachricht (gekürzt)

        msgs = [m for m in session.messages if m.role in ("user", "assistant")]
        if len(msgs) < MIN_MSGS:
            return

        try:
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

            lines = [f"# Letzte Session — {now}\n"]
            # Letzte 10 User+Assistant-Nachrichten als Kontext
            for m in msgs[-10:]:
                role_label = "User" if m.role == "user" else "Assistent"
                content = (m.content or "").strip()
                if len(content) > MAX_CONTENT:
                    content = content[:MAX_CONTENT] + "…"
                lines.append(f"**{role_label}:** {content}\n")

            memory_dir = self._projects_dir / project_id / "memory"
            memory_dir.mkdir(parents=True, exist_ok=True)
            memory_file = memory_dir / "_last_session.md"
            memory_file.write_text("\n".join(lines), encoding="utf-8")
            logger.info("Session-Memory gespeichert: %s (%d Msgs → %s)",
                        session.id[:8], len(msgs), memory_file)
        except Exception as e:
            logger.warning("Session-Memory konnte nicht gespeichert werden: %s", e)

    async def new_session(self, project_id: str) -> Session:
        """Neue Session starten — alte wird automatisch beendet und gespeichert."""
        async with self._get_lock(project_id):
            if project_id in self._active:
                old = self._active[project_id]
                old.end()
                self._db_end_session(old)
                self._write_session_memory(project_id, old)
                logger.info("Session beendet: %s (Projekt: %s, %d Nachrichten)",
                            old.id[:8], project_id, len(old.messages))

            session = Session.new(project_id)
            self._active[project_id] = session
            self._db_insert_session(session)
            logger.info("Neue Session: %s (Projekt: %s)", session.id[:8], project_id)
            return session

    async def end_session(self, project_id: str) -> Session | None:
        """Aktive Session beenden und speichern."""
        async with self._get_lock(project_id):
            session = self._active.pop(project_id, None)
            if session:
                session.end()
                self._db_end_session(session)
                self._write_session_memory(project_id, session)
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
        tool_calls: list[dict] | None = None,
        tool_call_id: str | None = None,
        **metadata,
    ) -> Message:
        """Nachricht an aktive Session anhängen (Session wird ggf. angelegt)."""
        async with self._get_lock(project_id):
            session = self.get_or_create(project_id)
            message = Message.create(
                role, content, agent_id=agent_id,
                tool_calls=tool_calls, tool_call_id=tool_call_id,
                **metadata,
            )
            session.append(message)
            self._db_insert_message(session.id, message, len(session.messages) - 1)
            # Preview aktualisieren: erste User-Message die ankommt wenn preview noch leer
            if role == MessageRole.USER:
                row = self._db.execute(
                    "SELECT preview FROM sessions WHERE id = ?", (session.id,)
                ).fetchone()
                if row and not row["preview"]:
                    self._db.execute(
                        "UPDATE sessions SET preview = ? WHERE id = ?",
                        (content[:120], session.id),
                    )
                    self._db.commit()
            # #630/#632: Working-Memory-Snapshot am Turn-Ende (assistant-Antwort)
            if role == MessageRole.ASSISTANT and not tool_calls:
                try:
                    from .working_state import (
                        WorkingState, now_iso, summarize_tool_message,
                        extract_open_files_from_messages, compute_git_state,
                    )
                    last_user = next(
                        (m.content for m in reversed(session.messages[:-1])
                         if m.role == MessageRole.USER),
                        "",
                    )

                    # last_tools — letzte 10 Tool-Use/Tool-Result-Paare aus der Session
                    last_tools: list[dict] = []
                    for m in session.messages[-30:]:  # window auf die letzten 30 Messages
                        meta = getattr(m, "metadata", None) or {}
                        if m.role == MessageRole.TOOL:
                            tn = meta.get("tool_name", "tool")
                            ok = not meta.get("error")
                            last_tools.append(summarize_tool_message("tool", m.content, tn, ok=ok))
                        elif m.role == MessageRole.ASSISTANT and m.tool_calls:
                            for tc in (m.tool_calls or []):
                                fn = (tc.get("function") or {})
                                args = fn.get("arguments", "")
                                last_tools.append(summarize_tool_message(
                                    "call", args, fn.get("name", "tool"), ok=None,
                                ))
                    last_tools = last_tools[-10:]

                    open_files = extract_open_files_from_messages(session.messages, max_files=8)

                    # git_state für Projekt-Workspace (best effort, nie raise)
                    git_state: list[dict] = []
                    proj_dir = Path(f"/projects/{project_id}")
                    gs = compute_git_state(proj_dir)
                    if gs:
                        git_state.append(gs)

                    snap = WorkingState(
                        current_goal=(last_user or "")[:200],
                        created_at=now_iso(),
                        last_tools=last_tools,
                        open_files=open_files,
                        git_state=git_state,
                    )
                    self.save_snapshot(session.id, snap, turn_seq=len(session.messages))
                    session.working_state = snap
                except Exception as _e:
                    logger.debug("Snapshot bei append fehlgeschlagen: %s", _e)
            return message

    async def replace_messages(self, project_id: str, messages: list[Message]) -> None:
        """Session-Nachrichten komplett ersetzen (für /compact)."""
        async with self._get_lock(project_id):
            session = self.get_or_create(project_id)
            session.messages = messages
            self._db_replace_messages(session)

    async def pop_last(self, project_id: str) -> None:
        """Letzte Nachricht aus der aktiven Session entfernen (Rollback bei Fehler)."""
        async with self._get_lock(project_id):
            session = self._active.get(project_id)
            if session and session.messages:
                session.messages.pop()
                # Letzte Message in DB entfernen
                self._db.execute(
                    "DELETE FROM messages WHERE session_id = ? AND seq = ("
                    "  SELECT MAX(seq) FROM messages WHERE session_id = ?"
                    ")",
                    (session.id, session.id),
                )
                self._db.execute(
                    "UPDATE sessions SET message_count = ? WHERE id = ?",
                    (len(session.messages), session.id),
                )
                self._db.commit()

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
        Aggregiert Token-Usage aus SQLite (#395).
        Gibt {project_id: {total_input, total_output, total_cache_read, total_cache_write,
                           model_breakdown: {model: {...}}, sessions_with_usage}} zurück.
        """
        if not self._db:
            return {}

        rows = self._db.execute("""
            SELECT s.project_id, m.model,
                   SUM(m.input_tokens) as total_input,
                   SUM(m.output_tokens) as total_output,
                   SUM(m.cache_read_tokens) as total_cache_read,
                   SUM(m.cache_write_tokens) as total_cache_write,
                   COUNT(DISTINCT m.session_id) as sessions_with_usage
            FROM messages m
            JOIN sessions s ON m.session_id = s.id
            WHERE m.input_tokens > 0 OR m.output_tokens > 0
            GROUP BY s.project_id, m.model
        """).fetchall()

        result: dict[str, dict] = {}
        for row in rows:
            pid = row["project_id"]
            model = row["model"] or "unknown"
            if pid not in result:
                result[pid] = {
                    "total_input": 0, "total_output": 0,
                    "total_cache_read": 0, "total_cache_write": 0,
                    "sessions_with_usage": 0, "model_breakdown": {},
                }
            stats = result[pid]
            stats["total_input"] += row["total_input"]
            stats["total_output"] += row["total_output"]
            stats["total_cache_read"] += row["total_cache_read"]
            stats["total_cache_write"] += row["total_cache_write"]
            stats["sessions_with_usage"] += row["sessions_with_usage"]
            stats["model_breakdown"][model] = {
                "input": row["total_input"],
                "output": row["total_output"],
                "cache_read": row["total_cache_read"],
                "cache_write": row["total_cache_write"],
            }
        return result

    def estimated_tokens(self, project_id: str) -> int:
        """Token-Count der aktiven Session.

        Nutzt echte Token-Counts aus API-Responses wenn vorhanden (#496),
        fällt auf Zeichenschätzung zurück für Messages ohne Counts.
        """
        session = self._active.get(project_id)
        if not session:
            return 0
        from .token_estimation import estimate_tokens
        total = 0
        for m in session.messages:
            real_in = m.metadata.get("input_tokens", 0) or 0
            real_out = m.metadata.get("output_tokens", 0) or 0
            if real_in or real_out:
                total += real_in + real_out
            else:
                total += estimate_tokens(m.content)
        return total

    async def resume_session(self, project_id: str, session_id: str) -> "Session | None":
        """Lädt eine historische Session und setzt sie als aktive Session."""
        async with self._get_lock(project_id):
            # Aktive Session beenden (außer es ist dieselbe)
            if project_id in self._active:
                old = self._active.pop(project_id)
                if old.id != session_id:
                    old.end()
                    self._db_end_session(old)

            # Session aus DB laden
            row = self._db.execute(
                "SELECT * FROM sessions WHERE id = ?", (session_id,)
            ).fetchone()
            if not row:
                return None

            try:
                session = Session(
                    id=row["id"],
                    project_id=row["project_id"],
                    started_at=row["started_at"],
                    ended_at=None,
                    messages=self._load_messages(session_id),
                )
            except Exception as e:
                logger.warning("resume_session: Fehler beim Laden von %s: %s", session_id, e)
                return None

            # #525: Resume Recovery — unvollständige Tool-Chains erkennen und reparieren
            _repaired = 0
            _last_msg = session.messages[-1] if session.messages else None
            if _last_msg and _last_msg.role == MessageRole.TOOL:
                # Session endet mit Tool-Message — wahrscheinlich unterbrochen
                # Recovery: System-Message anhängen die den Agent informiert
                recovery_msg = Message.create(
                    role=MessageRole.SYSTEM,
                    content=(
                        "[Session Recovery] Diese Session wurde nach einer Unterbrechung fortgesetzt. "
                        "Die letzte Aktion war ein Tool-Aufruf. Das Ergebnis könnte unvollständig sein. "
                        "Fasse zusammen was bisher erledigt wurde und frage den User wie es weitergehen soll."
                    ),
                )
                session.messages.append(recovery_msg)
                _repaired += 1

            # Verwaiste SYSTEM-Messages am Ende entfernen (z.B. 🔧 Tool-Detail ohne Result)
            while session.messages and session.messages[-1].role == MessageRole.SYSTEM:
                if session.messages[-1].content.startswith("🔧"):
                    session.messages.pop()
                    _repaired += 1
                else:
                    break

            # Turn Journal: Resume-Event aufzeichnen
            try:
                from .turn_journal import journal as _tj, EventType as _JE
                _tj.append(session_id, project_id, _JE.SESSION_RESUME, {
                    "messages": len(session.messages), "repaired": _repaired,
                })
            except Exception:
                pass

            # #630: Working-Memory-Snapshot laden falls vorhanden
            _snapshot = self.load_latest_snapshot(session_id)
            if _snapshot:
                logger.info(
                    "Session %s: Working-Memory-Snapshot geladen (created=%s, goal=%r, files=%d)",
                    session_id[:8], _snapshot.created_at,
                    (_snapshot.current_goal or "")[:60], len(_snapshot.open_files),
                )
                # Snapshot am Session-Objekt halten — Builder nutzt ihn via
                # session.working_state für den `working_state`-Channel (#627).
                session.working_state = _snapshot

            # Als aktiv setzen — ended_at zurücksetzen
            self._active[project_id] = session
            self._db.execute("UPDATE sessions SET ended_at = NULL WHERE id = ?", (session_id,))
            self._db.commit()
            if _repaired:
                # Bug: hieß früher _persist (existiert nicht) — korrekt ist _db_replace_messages
                self._db_replace_messages(session)
                logger.info("Session %s resumed mit %d Reparaturen (%d Nachrichten)",
                            session_id[:8], _repaired, len(session.messages))
            else:
                logger.info("Session %s als aktive Session für %s wiederhergestellt (%d Nachrichten)",
                            session_id[:8], project_id, len(session.messages))
            return session

    def list_sessions(self, project_id: str, limit: int = 20) -> list[dict]:
        """Alle gespeicherten Sessions eines Projekts (neueste zuerst) mit Preview."""
        if not self._db:
            return []
        rows = self._db.execute(
            "SELECT id, started_at, ended_at, message_count, preview "
            "FROM sessions WHERE project_id = ? ORDER BY started_at DESC LIMIT ?",
            (project_id, limit),
        ).fetchall()
        return [
            {
                "id": r["id"],
                "started_at": r["started_at"],
                "ended_at": r["ended_at"],
                "message_count": r["message_count"],
                "preview": r["preview"],
            }
            for r in rows
        ]

    def get_session_by_id(self, project_id: str, session_id: str) -> "Session | None":
        """Eine bestimmte Session laden (aktive oder historische)."""
        active = self._active.get(project_id)
        if active and active.id == session_id:
            return active
        if not self._db:
            return None
        row = self._db.execute(
            "SELECT * FROM sessions WHERE id = ? AND project_id = ?",
            (session_id, project_id),
        ).fetchone()
        if not row:
            return None
        return Session(
            id=row["id"],
            project_id=row["project_id"],
            started_at=row["started_at"],
            ended_at=row["ended_at"],
            messages=self._load_messages(session_id),
        )

    async def compact(
        self,
        project_id: str,
        summary: str,
        keep_last: int = 10,
        keep_last_rounds: int | None = None,
    ) -> None:
        """
        Context-Kompaktierung (#74, #477 Round-Grouping).

        Wenn keep_last_rounds gesetzt: schneidet an API-Round-Grenzen
        (keine verwaisten tool_results). Fallback auf flat keep_last
        für Legacy-Calls oder wenn Grouping nicht genug Rounds hat.
        """
        async with self._get_lock(project_id):
            session = self._active.get(project_id)
            if not session or len(session.messages) <= keep_last:
                return

            if keep_last_rounds is not None:
                # #477: Round-basierte Compaction
                # System-Summary am Anfang separat behandeln
                msgs = session.messages
                system_prefix: list[Message] = []
                if msgs and msgs[0].role == MessageRole.SYSTEM and msgs[0].content.startswith("[Zusammenfassung"):
                    system_prefix = [msgs[0]]
                    msgs = msgs[1:]

                rounds = group_messages_by_api_round(msgs)
                if len(rounds) > keep_last_rounds:
                    # Letzte N Rounds behalten, Rest kompaktieren
                    kept_rounds = rounds[-keep_last_rounds:]
                    tail = [m for rnd in kept_rounds for m in rnd]
                else:
                    # Nicht genug Rounds → flat Fallback
                    tail = session.messages[-keep_last:]
            else:
                tail = session.messages[-keep_last:]

            summary_msg = Message.create(
                role=MessageRole.SYSTEM,
                content=f"[Zusammenfassung früherer Konversation]\n{summary}",
            )
            session.messages = [summary_msg] + tail
            self._db_replace_messages(session)
            logger.info(
                "Session %s kompaktiert: Summary + %d Nachrichten (%s Rounds) behalten",
                session.id[:8], len(tail),
                keep_last_rounds if keep_last_rounds else "flat",
            )

    # ----------------------------------------------------------------- private DB helpers

    def _db_insert_session(self, session: Session) -> None:
        """Neue Session in DB einfügen."""
        self._db.execute(
            "INSERT INTO sessions (id, project_id, started_at, ended_at, message_count, preview) "
            "VALUES (?, ?, ?, ?, 0, '')",
            (session.id, session.project_id, session.started_at, session.ended_at),
        )
        self._db.commit()

    def _db_end_session(self, session: Session) -> None:
        """Session als beendet markieren."""
        self._db.execute(
            "UPDATE sessions SET ended_at = ? WHERE id = ?",
            (session.ended_at, session.id),
        )
        self._db.commit()

    def _db_insert_message(self, session_id: str, message: Message, seq: int) -> None:
        """Einzelne Message in DB einfügen."""
        meta = dict(message.metadata or {})
        # Tool-Call Daten in metadata persistieren (kein DB-Schema-Change nötig)
        if message.tool_calls:
            meta["_tool_calls"] = message.tool_calls
        if message.tool_call_id:
            meta["_tool_call_id"] = message.tool_call_id
        self._db.execute(
            "INSERT INTO messages "
            "(session_id, msg_id, role, content, timestamp, agent_id, metadata, seq, "
            " input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, model) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                message.msg_id,
                message.role.value,
                message.content,
                message.timestamp,
                message.agent_id,
                json.dumps(meta, ensure_ascii=False),
                seq,
                meta.get("input_tokens", 0) or 0,
                meta.get("output_tokens", 0) or 0,
                meta.get("cache_read_tokens", 0) or 0,
                meta.get("cache_write_tokens", 0) or 0,
                meta.get("model"),
            ),
        )
        self._db.execute(
            "UPDATE sessions SET message_count = message_count + 1 WHERE id = ?",
            (session_id,),
        )
        self._db.commit()

    def _db_replace_messages(self, session: Session) -> None:
        """Alle Messages einer Session ersetzen (für compact/replace_messages)."""
        self._db.execute("DELETE FROM messages WHERE session_id = ?", (session.id,))
        for i, msg in enumerate(session.messages):
            meta = dict(msg.metadata or {})
            if msg.tool_calls:
                meta["_tool_calls"] = msg.tool_calls
            if msg.tool_call_id:
                meta["_tool_call_id"] = msg.tool_call_id
            self._db.execute(
                "INSERT INTO messages "
                "(session_id, msg_id, role, content, timestamp, agent_id, metadata, seq, "
                " input_tokens, output_tokens, cache_read_tokens, cache_write_tokens, model) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    session.id,
                    msg.msg_id,
                    msg.role.value,
                    msg.content,
                    msg.timestamp,
                    msg.agent_id,
                    json.dumps(meta, ensure_ascii=False),
                    i,
                    meta.get("input_tokens", 0) or 0,
                    meta.get("output_tokens", 0) or 0,
                    meta.get("cache_read_tokens", 0) or 0,
                    meta.get("cache_write_tokens", 0) or 0,
                    meta.get("model"),
                ),
            )
        # Preview aus erster User-Message
        preview = ""
        for msg in session.messages:
            if msg.role == MessageRole.USER:
                preview = msg.content[:120]
                break
        self._db.execute(
            "UPDATE sessions SET message_count = ?, preview = ? WHERE id = ?",
            (len(session.messages), preview, session.id),
        )
        self._db.commit()

    def _load_messages(self, session_id: str) -> list[Message]:
        """Alle Messages einer Session aus DB laden."""
        rows = self._db.execute(
            "SELECT * FROM messages WHERE session_id = ? ORDER BY seq",
            (session_id,),
        ).fetchall()
        result = []
        for r in rows:
            meta = json.loads(r["metadata"]) if r["metadata"] else {}
            tc = meta.pop("_tool_calls", None)
            tcid = meta.pop("_tool_call_id", None)
            result.append(Message(
                role=MessageRole(r["role"]),
                content=r["content"],
                timestamp=r["timestamp"],
                msg_id=r["msg_id"],
                agent_id=r["agent_id"],
                metadata=meta,
                tool_calls=tc,
                tool_call_id=tcid,
            ))
        return result
