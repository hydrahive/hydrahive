"""
proactive_mode.py — Autonomes Arbeiten ohne User-Input (#483)

Agent führt konfigurierte Tasks eigenständig im Hintergrund aus.
Safety: Nur read-only Tools + explizit freigegebene Aktionen.
Ergebnisse werden als Notification an den User gesendet.
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Maximale Laufzeit eines proaktiven Tasks (5 Minuten)
MAX_TASK_DURATION = 300

# Tools die im Proactive Mode erlaubt sind (read-only + Memory)
PROACTIVE_ALLOWED_TOOLS = {
    "file_read", "list_directory", "read_system_file",
    "read_memory", "shared_memory_read", "user_memory_read",
    "git_status", "git_diff", "git_log", "git_grep",
    "web_search", "http_request",
    "write_memory",  # Memory schreiben ist erlaubt (kein Seiteneffekt)
}


@dataclass
class ProactiveTask:
    """Ein konfigurierter proaktiver Task."""
    id: str
    agent_id: str
    project_id: str
    prompt: str
    interval_seconds: int = 3600  # Default: stündlich
    enabled: bool = True
    last_run: float = 0
    last_result: str = ""
    allowed_tools: set = field(default_factory=lambda: set(PROACTIVE_ALLOWED_TOOLS))


class ProactiveService:
    """Verwaltet und führt proaktive Tasks aus."""

    def __init__(self):
        self._tasks: dict[str, ProactiveTask] = {}
        self._running: set[str] = set()
        self._loop_task: asyncio.Task | None = None

    def add_task(self, task: ProactiveTask) -> None:
        self._tasks[task.id] = task
        logger.info("Proactive Task '%s' hinzugefügt (Agent: %s, Intervall: %ds)",
                     task.id, task.agent_id, task.interval_seconds)

    def remove_task(self, task_id: str) -> None:
        self._tasks.pop(task_id, None)

    def list_tasks(self) -> list[dict]:
        return [
            {
                "id": t.id, "agent_id": t.agent_id, "project_id": t.project_id,
                "prompt": t.prompt[:100], "interval": t.interval_seconds,
                "enabled": t.enabled, "last_run": t.last_run,
                "last_result": t.last_result[:200] if t.last_result else "",
                "running": t.id in self._running,
            }
            for t in self._tasks.values()
        ]

    def start(self) -> None:
        """Background-Loop starten."""
        if self._loop_task and not self._loop_task.done():
            return
        self._loop_task = asyncio.create_task(self._loop())
        logger.info("ProactiveService gestartet")

    async def _loop(self) -> None:
        """Endlos-Loop: prüft welche Tasks fällig sind."""
        while True:
            try:
                now = time.time()
                for task in list(self._tasks.values()):
                    if not task.enabled:
                        continue
                    if task.id in self._running:
                        continue
                    if now - task.last_run < task.interval_seconds:
                        continue
                    # Task ist fällig
                    asyncio.create_task(self._run_task(task))
            except Exception as e:
                logger.debug("ProactiveService loop error: %s", e)
            await asyncio.sleep(30)  # Alle 30s prüfen

    async def _run_task(self, task: ProactiveTask) -> None:
        """Einen proaktiven Task ausführen."""
        self._running.add(task.id)
        task.last_run = time.time()
        logger.info("Proactive Task '%s' gestartet", task.id)

        try:
            # Orchestrator importieren (lazy, vermeidet Zirkel-Import)
            from .main import orchestrator
            if not orchestrator:
                return

            # Nachricht senden mit eingeschränktem Execution-Mode
            result, _ = await asyncio.wait_for(
                orchestrator.handle_message(
                    project_id=task.project_id,
                    content=f"[Proaktiver Task] {task.prompt}",
                    sender="proactive",
                    execution_mode="proactive",
                ),
                timeout=MAX_TASK_DURATION,
            )
            task.last_result = result or ""
            logger.info("Proactive Task '%s' abgeschlossen (%d chars)", task.id, len(task.last_result))

            # Notification an User senden
            try:
                from .notification_service import notification_service
                if notification_service:
                    notification_service.send(
                        title=f"Proaktiv: {task.prompt[:50]}",
                        body=task.last_result[:300],
                        level="info",
                        link=f"/projects/{task.project_id}",
                    )
            except Exception:
                pass

        except asyncio.TimeoutError:
            task.last_result = "[Timeout nach 5 Minuten]"
            logger.warning("Proactive Task '%s' Timeout", task.id)
        except Exception as e:
            task.last_result = f"[Fehler: {e}]"
            logger.error("Proactive Task '%s' Fehler: %s", task.id, e)
        finally:
            self._running.discard(task.id)

    def load_from_config(self, config_dir: Path) -> None:
        """Proaktive Tasks aus Konfigurationsdatei laden."""
        config_file = config_dir / "proactive_tasks.json"
        if not config_file.exists():
            return
        try:
            tasks = json.loads(config_file.read_text(encoding="utf-8"))
            for t in tasks:
                self.add_task(ProactiveTask(
                    id=t["id"],
                    agent_id=t["agent_id"],
                    project_id=t["project_id"],
                    prompt=t["prompt"],
                    interval_seconds=t.get("interval", 3600),
                    enabled=t.get("enabled", True),
                ))
            logger.info("Proactive: %d Tasks aus Config geladen", len(tasks))
        except Exception as e:
            logger.warning("Proactive Config laden fehlgeschlagen: %s", e)

    def save_to_config(self, config_dir: Path) -> None:
        """Tasks in Konfigurationsdatei speichern."""
        config_file = config_dir / "proactive_tasks.json"
        try:
            config_dir.mkdir(parents=True, exist_ok=True)
            data = [
                {
                    "id": t.id, "agent_id": t.agent_id, "project_id": t.project_id,
                    "prompt": t.prompt, "interval": t.interval_seconds, "enabled": t.enabled,
                }
                for t in self._tasks.values()
            ]
            config_file.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError as e:
            logger.warning("Proactive Config speichern fehlgeschlagen: %s", e)


# Globale Singleton-Instanz
proactive_service = ProactiveService()
