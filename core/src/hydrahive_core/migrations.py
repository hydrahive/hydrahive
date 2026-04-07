"""
migrations.py — Idempotentes Version-Migrations-System (#443)

Migrations-Funktionen werden beim Core-Start ausgeführt.
Jede Migration hat eine ID und wird nur einmal ausgeführt (Tracking in migrations.json).
"""
import json
import logging
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


# ── Registry ──────────────────────────────────────────────────────────────────

MIGRATIONS = [
    ("001_ensure_pbkdf2b", _m001_ensure_pbkdf2b_scheme),
    ("002_ensure_session_dirs", _m002_ensure_session_dirs),
    ("003_cleanup_stale_locks", _m003_cleanup_stale_locks),
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
