"""
cleanup_service.py — Disk-Cleanup & Disk-Usage-Warnungen (#81)

Täglich (03:30) werden folgende Aufräumarbeiten durchgeführt:
  - Alte Transcripts (> transcript_days Tage)
  - Alte Backups (> backup_keep letzte behalten)
  - Verwaiste persönliche Projekte (user nicht mehr in users.yaml)
  - FAISS- und SQLite-Indizes gelöschter Agenten

Disk-Warnungen bei 80%/90% Belegung (einmalig je Level, Reset wenn wieder < 75%).

Konfiguration (optional): /etc/hydrahive/cleanup.json
{
  "transcript_days": 30,
  "backup_keep": 10,
  "warn_pct_yellow": 80,
  "warn_pct_red": 90
}
"""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_CONFIG_FILE = Path("/etc/hydrahive/cleanup.json")
_DEFAULT_CFG: dict[str, Any] = {
    "transcript_days": 30,
    "backup_keep": 10,
    "warn_pct_yellow": 80,
    "warn_pct_red": 90,
}


def _load_config() -> dict[str, Any]:
    if _CONFIG_FILE.exists():
        try:
            return {**_DEFAULT_CFG, **json.loads(_CONFIG_FILE.read_text())}
        except Exception:
            pass
    return dict(_DEFAULT_CFG)


def save_config(cfg: dict[str, Any]) -> None:
    _CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    _CONFIG_FILE.write_text(json.dumps(cfg, indent=2))


# ---------------------------------------------------------------------------
# Cleanup-Funktionen
# ---------------------------------------------------------------------------

def cleanup_old_transcripts(agents_dir: str, max_age_days: int = 30) -> int:
    """Löscht Transcript-Dateien älter als max_age_days Tage. Gibt Anzahl zurück."""
    cutoff = time.time() - max_age_days * 86_400
    deleted = 0
    for agent_dir in Path(agents_dir).iterdir():
        if not agent_dir.is_dir():
            continue
        transcripts = agent_dir / "transcripts"
        if not transcripts.is_dir():
            continue
        for f in transcripts.glob("*.json"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    deleted += 1
            except Exception as e:
                logger.warning("Transcript löschen fehlgeschlagen %s: %s", f, e)
    return deleted


def cleanup_old_backups(backups_dir: str, keep: int = 10) -> int:
    """Behält nur die neuesten `keep` Backups, löscht den Rest. Gibt Anzahl zurück."""
    bd = Path(backups_dir)
    if not bd.is_dir():
        return 0
    backups = sorted(bd.glob("*.tar.gz"), key=lambda p: p.stat().st_mtime, reverse=True)
    to_delete = backups[keep:]
    deleted = 0
    for f in to_delete:
        try:
            f.unlink()
            deleted += 1
            logger.info("Altes Backup gelöscht: %s", f.name)
        except Exception as e:
            logger.warning("Backup löschen fehlgeschlagen %s: %s", f, e)
    return deleted


def cleanup_orphaned_personal_projects(projects_dir: str, users: dict) -> int:
    """Löscht personal_* Projektverzeichnisse für nicht mehr existierende User."""
    deleted = 0
    for project_dir in Path(projects_dir).iterdir():
        if not project_dir.is_dir():
            continue
        name = project_dir.name
        if not name.startswith("personal_"):
            continue
        username = name[len("personal_"):]
        if username not in users:
            try:
                shutil.rmtree(project_dir)
                deleted += 1
                logger.info("Verwaistes Projekt gelöscht: %s", name)
            except Exception as e:
                logger.warning("Projekt löschen fehlgeschlagen %s: %s", name, e)
    return deleted


def prune_weak_memory_chunks(agents_dir: str) -> int:
    """
    Ebbinghaus Decay Prune: läuft über alle Agenten-Verzeichnisse,
    recomputed Stärken und löscht Chunks unter PRUNE_THRESHOLD.
    Gibt Gesamtanzahl gelöschter Chunks zurück.
    """
    import sqlite3 as _sqlite3
    from .memory_decay import recompute_all_strengths, prune_weak_chunks

    total_pruned = 0
    for agent_dir in Path(agents_dir).iterdir():
        if not agent_dir.is_dir():
            continue
        db_path = agent_dir / "memory_index.db"
        if not db_path.exists():
            continue
        try:
            conn = _sqlite3.connect(str(db_path))
            conn.execute("PRAGMA foreign_keys=ON")
            try:
                recompute_all_strengths(conn)
                pruned = prune_weak_chunks(conn)
                if pruned:
                    conn.commit()
                    total_pruned += len(pruned)
                    logger.info(
                        "memory_decay prune: %d Chunks für Agent %s entfernt",
                        len(pruned), agent_dir.name,
                    )
            finally:
                conn.close()
        except Exception as e:
            logger.warning("memory_decay prune fehlgeschlagen für %s: %s", agent_dir.name, e)
    return total_pruned


def cleanup_stale_indices(agents_dir: str, known_agent_ids: set[str]) -> int:
    """Löscht FAISS/SQLite-Indizes von Agenten die nicht mehr existieren."""
    deleted = 0
    for agent_dir in Path(agents_dir).iterdir():
        if not agent_dir.is_dir():
            continue
        if agent_dir.name in known_agent_ids:
            continue
        # Agent-Verzeichnis selbst existiert noch → skip
        # (cleanup_stale_indices wird nur für wirklich fehlende Agents aufgerufen)
    # Prüfe index-Dateien direkt auf bekannte Agenten
    for agent_dir in Path(agents_dir).iterdir():
        if not agent_dir.is_dir():
            continue
        if agent_dir.name not in known_agent_ids:
            # Agent existiert nicht mehr → Indizes löschen
            for suffix in ("*.faiss", "*.index", "memory_search.db"):
                for f in agent_dir.glob(suffix):
                    try:
                        f.unlink()
                        deleted += 1
                    except Exception as e:
                        logger.warning("Index löschen fehlgeschlagen %s: %s", f, e)
    return deleted


# ---------------------------------------------------------------------------
# Disk-Usage
# ---------------------------------------------------------------------------

def get_disk_usage(path: str = "/") -> dict:
    """Gibt disk usage als dict zurück: total_gb, used_gb, free_gb, percent."""
    try:
        usage = shutil.disk_usage(path)
        return {
            "total_gb": round(usage.total / 1_073_741_824, 1),
            "used_gb":  round(usage.used  / 1_073_741_824, 1),
            "free_gb":  round(usage.free  / 1_073_741_824, 1),
            "percent":  round(usage.used / usage.total * 100, 1),
        }
    except Exception as e:
        logger.warning("disk_usage Fehler: %s", e)
        return {"total_gb": 0, "used_gb": 0, "free_gb": 0, "percent": 0}


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class CleanupService:
    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._warned_yellow = False
        self._warned_red    = False
        self._agents_dir    = "/agents"
        self._projects_dir  = "/projects"
        self._backups_dir   = "/opt/hydrahive/backups"
        self._load_users_fn = None
        self._notify_fn     = None
        self._last_result: dict | None = None

    def start(
        self,
        agents_dir: str,
        projects_dir: str,
        backups_dir: str,
        load_users_fn,
        notify_fn=None,
    ) -> None:
        self._agents_dir   = agents_dir
        self._projects_dir = projects_dir
        self._backups_dir  = backups_dir
        self._load_users_fn = load_users_fn
        self._notify_fn    = notify_fn
        self._task = asyncio.create_task(self._loop(), name="disk-cleanup")
        logger.info("CleanupService gestartet")

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    def last_result(self) -> dict | None:
        return self._last_result

    async def run_now(self) -> dict:
        """Cleanup sofort ausführen (für manuellen Trigger per API)."""
        return await asyncio.get_event_loop().run_in_executor(None, self._run_sync)

    def _run_sync(self) -> dict:
        cfg = _load_config()
        users: dict = {}
        if self._load_users_fn:
            try:
                users = self._load_users_fn()
            except Exception:
                pass

        known_agents = {d.name for d in Path(self._agents_dir).iterdir() if d.is_dir()} if Path(self._agents_dir).is_dir() else set()

        t0 = time.time()
        transcripts   = cleanup_old_transcripts(self._agents_dir, cfg["transcript_days"])
        backups       = cleanup_old_backups(self._backups_dir, cfg["backup_keep"])
        orphans       = cleanup_orphaned_personal_projects(self._projects_dir, users)
        stale_idx     = cleanup_stale_indices(self._agents_dir, known_agents)
        memory_pruned = prune_weak_memory_chunks(self._agents_dir)
        disk          = get_disk_usage("/")
        elapsed_ms    = round((time.time() - t0) * 1000)

        result = {
            "ran_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "elapsed_ms": elapsed_ms,
            "deleted_transcripts": transcripts,
            "deleted_backups": backups,
            "deleted_orphan_projects": orphans,
            "deleted_stale_indices": stale_idx,
            "pruned_memory_chunks": memory_pruned,
            "disk": disk,
        }
        self._last_result = result
        logger.info(
            "Disk-Cleanup: transcripts=%d backups=%d orphans=%d idx=%d disk=%.1f%%",
            transcripts, backups, orphans, stale_idx, disk["percent"],
        )
        return result

    async def _check_disk_warnings(self, disk: dict) -> None:
        if not self._notify_fn:
            return
        cfg = _load_config()
        pct = disk["percent"]

        if pct >= cfg["warn_pct_red"] and not self._warned_red:
            self._warned_red = True
            await self._notify_fn(
                title=f"Disk KRITISCH: {pct:.0f}% belegt",
                body=f"Nur noch {disk['free_gb']} GB frei von {disk['total_gb']} GB.",
                level="red",
            )
        elif pct >= cfg["warn_pct_yellow"] and not self._warned_yellow:
            self._warned_yellow = True
            await self._notify_fn(
                title=f"Disk-Warnung: {pct:.0f}% belegt",
                body=f"Noch {disk['free_gb']} GB frei von {disk['total_gb']} GB.",
                level="yellow",
            )
        elif pct < 75:
            # Reset Warnungen wenn Platz wieder vorhanden
            self._warned_yellow = False
            self._warned_red    = False

    async def _loop(self) -> None:
        """Täglich um 03:30 Uhr ausführen."""
        while True:
            try:
                now = datetime.now()
                # Sekunden bis 03:30 Uhr (nächste Instanz)
                target = now.replace(hour=3, minute=30, second=0, microsecond=0)
                if now >= target:
                    # Heute schon vorbei → morgen
                    from datetime import timedelta
                    target = target + timedelta(days=1)
                sleep_sec = (target - now).total_seconds()
                logger.debug("Disk-Cleanup nächster Lauf in %.0f Sekunden", sleep_sec)
                await asyncio.sleep(sleep_sec)

                result = await self.run_now()
                await self._check_disk_warnings(result["disk"])

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("CleanupService Fehler: %s", e)
                await asyncio.sleep(3600)  # bei Fehler 1h warten


cleanup_service = CleanupService()
