"""
migrations.py — Idempotentes Version-Migrations-System (#443)

Migrations-Funktionen werden beim Core-Start ausgeführt.
Jede Migration hat eine ID und wird nur einmal ausgeführt (Tracking in migrations.json).
"""
import json
import logging
import shutil
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .settings import settings

logger = logging.getLogger(__name__)

MIGRATIONS_FILE = settings.etc_dir / "migrations.json"


def _load_applied() -> set[str]:
    try:
        data = json.loads(MIGRATIONS_FILE.read_text())
        return set(data.get("applied", []))
    except (OSError, ValueError):
        return set()


def _save_applied(applied: set[str]) -> None:
    MIGRATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    MIGRATIONS_FILE.write_text(json.dumps({
        "applied": sorted(applied),
        "last_run": datetime.now(timezone.utc).isoformat(),
    }, indent=2))


# ── Migration-Funktionen ──────────────────────────────────────────────────────

def _m001_ensure_pbkdf2b_scheme():
    """Markiert alle User mit pbkdf2b-Hashes (für Tracking, kein Daten-Change)."""
    pass  # Auto-Rehash passiert jetzt beim Login (#454)


def _m002_ensure_session_dirs():
    """Stellt sicher dass .sessions/ Verzeichnisse für alle Projekte existieren."""
    projects_dir = settings.projects_dir
    if projects_dir.exists():
        for p in projects_dir.iterdir():
            if p.is_dir():
                (p / ".sessions").mkdir(exist_ok=True)


def _m003_cleanup_stale_locks():
    """Entfernt verwaiste .lock Dateien die nach Crashes übrig geblieben sind."""
    for lock_file in settings.projects_dir.glob("**/*.lock"):
        try:
            lock_file.unlink()
            logger.info("Stale lock entfernt: %s", lock_file)
        except OSError:
            pass


def _m004_json_to_sqlite():
    """Migriert alle JSON-Session-Dateien nach SQLite (#395).

    Liest .sessions/*.json aus /projects/ und /agents/,
    importiert in sessions.db, benennt JSON-Dateien zu .json.migrated um.
    Idempotent: INSERT OR IGNORE überspringt bereits importierte Sessions.
    """
    from .session_manager import SessionManager

    for base_dir in (settings.projects_dir, settings.agents_dir):
        if not base_dir.exists():
            continue

        # DB öffnen/erstellen (SessionManager._init_db Schema)
        db_path = base_dir / "sessions.db"
        db = sqlite3.connect(str(db_path), check_same_thread=False)
        db.execute("PRAGMA journal_mode=WAL")
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
        """)

        migrated_count = 0
        for project_dir in base_dir.iterdir():
            if not project_dir.is_dir():
                continue
            sessions_dir = project_dir / ".sessions"
            if not sessions_dir.is_dir():
                continue

            for json_file in sessions_dir.glob("*.json"):
                try:
                    data = json.loads(json_file.read_text(encoding="utf-8"))
                    session_id = data["id"]
                    project_id = data.get("project_id", project_dir.name)
                    messages = data.get("messages", [])

                    # Preview: erste User-Message
                    preview = ""
                    for m in messages:
                        if m.get("role") == "user":
                            preview = m.get("content", "")[:120]
                            break

                    # Session einfügen (skip wenn schon vorhanden)
                    db.execute(
                        "INSERT OR IGNORE INTO sessions "
                        "(id, project_id, started_at, ended_at, message_count, preview) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            session_id,
                            project_id,
                            data.get("started_at", ""),
                            data.get("ended_at"),
                            len(messages),
                            preview,
                        ),
                    )

                    # Prüfen ob Messages schon importiert
                    existing = db.execute(
                        "SELECT COUNT(*) FROM messages WHERE session_id = ?",
                        (session_id,),
                    ).fetchone()[0]
                    if existing > 0:
                        # Session war schon importiert — nur Datei umbenennen
                        json_file.rename(json_file.with_suffix(".json.migrated"))
                        continue

                    # Messages einfügen
                    for seq, m in enumerate(messages):
                        meta = m.get("metadata", {})
                        db.execute(
                            "INSERT INTO messages "
                            "(session_id, msg_id, role, content, timestamp, agent_id, "
                            " metadata, seq, input_tokens, output_tokens, "
                            " cache_read_tokens, cache_write_tokens, model) "
                            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                            (
                                session_id,
                                m.get("msg_id"),
                                m.get("role", "user"),
                                m.get("content", ""),
                                m.get("timestamp", ""),
                                m.get("agent_id"),
                                json.dumps(meta, ensure_ascii=False),
                                seq,
                                meta.get("input_tokens", 0) or 0,
                                meta.get("output_tokens", 0) or 0,
                                meta.get("cache_read_tokens", 0) or 0,
                                meta.get("cache_write_tokens", 0) or 0,
                                meta.get("model"),
                            ),
                        )

                    # JSON-Datei als migriert markieren
                    json_file.rename(json_file.with_suffix(".json.migrated"))
                    migrated_count += 1

                except Exception as e:
                    logger.warning("Migration 004: Fehler bei %s: %s", json_file, e)
                    continue

        db.commit()
        db.close()
        if migrated_count:
            logger.info("Migration 004: %d Sessions aus %s nach SQLite migriert",
                        migrated_count, base_dir)


def _m005_consolidate_agent_memory():
    """Merge legacy /agents/<id>/memory into /projects/<id>/memory (#706)."""
    from .memory_paths import (
        LEGACY_MEMORY_INDEX_FILENAME,
        MEMORY_INDEX_FILENAME,
        agent_memory_dir,
        legacy_agent_memory_dir,
    )

    def _unique_dest(dst_dir: Path, filename: str) -> Path:
        candidate = dst_dir / filename
        if not candidate.exists():
            return candidate
        suffix = candidate.suffix
        stem = candidate.name[: -len(suffix)] if suffix else candidate.name
        for i in range(1, 1000):
            legacy_name = f"{stem}.agent-legacy{'' if i == 1 else f'-{i}'}{suffix}"
            candidate = dst_dir / legacy_name
            if not candidate.exists():
                return candidate
        raise RuntimeError(f"Keine kollisionsfreie Legacy-Memory-Datei fuer {filename}")

    def _merge_index_text(memory_dir: Path, source_path: Path, *, heading: str) -> None:
        text = source_path.read_text(encoding="utf-8", errors="replace").strip()
        if not text:
            try:
                source_path.unlink()
            except OSError:
                pass
            return

        target = memory_dir / MEMORY_INDEX_FILENAME
        if not target.exists():
            if source_path == target:
                return
            shutil.move(str(source_path), str(target))
            return

        existing = target.read_text(encoding="utf-8", errors="replace")
        if text not in existing:
            target.write_text(
                existing.rstrip() + f"\n\n## {heading}\n\n" + text + "\n",
                encoding="utf-8",
            )
        if source_path != target:
            try:
                source_path.unlink()
            except OSError:
                pass

    if not settings.agents_dir.exists():
        return

    migrated_files = 0
    for agent_dir in sorted(settings.agents_dir.iterdir()):
        if not agent_dir.is_dir():
            continue
        legacy_memory = legacy_agent_memory_dir(agent_dir.name)
        if not legacy_memory.is_dir():
            continue

        memory_dir = agent_memory_dir(agent_dir.name)
        memory_dir.mkdir(parents=True, exist_ok=True)

        # First normalize any v2 bootstrap INDEX.md already present in /projects.
        project_index = memory_dir / LEGACY_MEMORY_INDEX_FILENAME
        if project_index.exists():
            _merge_index_text(memory_dir, project_index, heading="Legacy INDEX.md")

        for item in sorted(legacy_memory.iterdir()):
            if not item.is_file():
                continue
            if item.name in (MEMORY_INDEX_FILENAME, LEGACY_MEMORY_INDEX_FILENAME):
                _merge_index_text(
                    memory_dir,
                    item,
                    heading=f"Legacy /agents/{agent_dir.name}/memory/{item.name}",
                )
            else:
                dest = _unique_dest(memory_dir, item.name)
                shutil.move(str(item), str(dest))
            migrated_files += 1

        try:
            legacy_memory.rmdir()
            logger.info("Migration 005: Legacy-Memory geleert: %s", legacy_memory)
        except OSError:
            logger.warning(
                "Migration 005: Legacy-Memory bleibt bestehen (nicht leer): %s",
                legacy_memory,
            )

    if migrated_files:
        logger.info(
            "Migration 005: %d Legacy-Memory-Dateien nach %s konsolidiert",
            migrated_files,
            settings.projects_dir,
        )


# ── Registry ──────────────────────────────────────────────────────────────────

MIGRATIONS = [
    ("001_ensure_pbkdf2b", _m001_ensure_pbkdf2b_scheme),
    ("002_ensure_session_dirs", _m002_ensure_session_dirs),
    ("003_cleanup_stale_locks", _m003_cleanup_stale_locks),
    ("004_json_to_sqlite", _m004_json_to_sqlite),
    ("005_consolidate_agent_memory", _m005_consolidate_agent_memory),
]


def run_migrations() -> int:
    """Führt alle ausstehenden Migrations aus. Gibt Anzahl der ausgeführten zurück."""
    applied = _load_applied()
    count = 0
    for mid, fn in MIGRATIONS:
        if mid in applied:
            continue
        try:
            fn()
            applied.add(mid)
            logger.info("Migration '%s' ausgeführt", mid)
            count += 1
        except Exception as e:
            logger.error("Migration '%s' fehlgeschlagen: %s", mid, e)
    _save_applied(applied)
    if count:
        logger.info("Migrations: %d ausgeführt, %d total", count, len(applied))
    return count
