"""folder_watcher.py — Asyncio-basierter Ordner-Watcher für Datei-Pipelines (#60)

Polling-basiert (kein watchdog erforderlich): scannt registrierte Ordner
alle POLL_INTERVAL Sekunden auf neue Dateien. Bereits verarbeitete Dateien
werden über (path + mtime) getrackt und in /var/run/hydrahive-watcher-state.json
persistiert damit nach Neustart keine Dateien doppelt verarbeitet werden.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)

POLL_INTERVAL   = 30        # Sekunden zwischen Scans
STATE_FILE      = Path("/var/run/hydrahive-watcher-state.json")
MAX_STATE_ITEMS = 10_000    # State-Größe begrenzen


def _load_state() -> dict[str, float]:
    """Lädt den gesehenen Dateien-State (path → mtime)."""
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _save_state(state: dict[str, float]) -> None:
    # Auf MAX_STATE_ITEMS begrenzen (älteste entfernen)
    if len(state) > MAX_STATE_ITEMS:
        sorted_items = sorted(state.items(), key=lambda x: x[1])
        state = dict(sorted_items[-(MAX_STATE_ITEMS // 2):])
    try:
        STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
    except Exception as e:
        logger.warning("Watcher-State konnte nicht gespeichert werden: %s", e)


async def run_folder_watcher(
    get_watches_fn: Callable[[], list[dict]],
    execute_pipeline_fn: Callable,
    get_pipeline_fn: Callable,
    notify_fn: Callable | None = None,
    poll_interval: int = POLL_INTERVAL,
) -> None:
    """Hauptschleife des Folder-Watchers. Als asyncio-Task ausführen."""
    logger.info("Folder-Watcher gestartet (Interval: %ds)", poll_interval)
    seen: dict[str, float] = _load_state()

    while True:
        try:
            watches = get_watches_fn()
            if not watches:
                await asyncio.sleep(poll_interval)
                continue

            new_files: list[tuple[str, str]] = []  # (file_path, pipeline_id)

            for watch in watches:
                folder = Path(watch["path"])
                if not folder.is_dir():
                    continue
                try:
                    pattern = "**/*" if watch.get("recursive") else "*"
                    for entry in folder.glob(pattern):
                        if not entry.is_file():
                            continue
                        key = str(entry)
                        try:
                            mtime = entry.stat().st_mtime
                        except OSError:
                            continue
                        if seen.get(key) != mtime:
                            seen[key] = mtime
                            new_files.append((key, watch["pipeline_id"]))
                except PermissionError as e:
                    logger.warning("Watcher: kein Zugriff auf %s: %s", watch["path"], e)

            if new_files:
                _save_state(seen)
                for file_path, pipeline_id in new_files:
                    pipeline = get_pipeline_fn(pipeline_id)
                    if pipeline is None or not pipeline.get("enabled"):
                        continue
                    logger.info("Watcher: neue Datei %s → Pipeline %s", file_path, pipeline_id)
                    try:
                        await execute_pipeline_fn(pipeline, file_path, notify_fn=notify_fn)
                    except Exception as e:
                        logger.error("Watcher: Pipeline-Fehler für %s: %s", file_path, e)

        except asyncio.CancelledError:
            logger.info("Folder-Watcher gestoppt")
            return
        except Exception as e:
            logger.error("Folder-Watcher Fehler: %s", e)

        await asyncio.sleep(poll_interval)
