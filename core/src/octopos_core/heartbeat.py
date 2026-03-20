"""
heartbeat.py — Periodische Agent-Aktivierung (#77)

Liest heartbeat_tasks aus agent.yaml und fuert sie zur konfigurierten Zeit aus.
State (last_run) wird in /agents/{id}/.heartbeat_state.json gespeichert.

Unterstuetzte Formate:
  schedule: "0 8 * * *"   # Cron-Syntax (braucht croniter)
  interval: 1800           # Sekunden-Intervall

active_hours: "08:00-22:00"  # optional, lokale Zeit
project: "buchhaltung"       # optional, sonst erstes Projekt des Agenten
"""

import asyncio
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent_config import AgentConfig, HeartbeatTaskConfig
    from .agent_discovery import AgentDiscovery
    from .project_loader import ProjectLoader
    from .orchestrator import Orchestrator

logger = logging.getLogger(__name__)

HEARTBEAT_CHECK_INTERVAL = 60   # Scheduler-Loop alle 60 Sekunden
STATE_FILENAME            = ".heartbeat_state.json"


# ------------------------------------------------------------------ Helpers

def _load_state(agent_dir: Path) -> dict:
    path = agent_dir / STATE_FILENAME
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _save_state(agent_dir: Path, state: dict) -> None:
    path = agent_dir / STATE_FILENAME
    try:
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")
    except OSError as e:
        logger.warning("Heartbeat State konnte nicht gespeichert werden: %s", e)


def _now_ts() -> float:
    return datetime.now(timezone.utc).timestamp()


def _is_active_hours(active_hours: str | None) -> bool:
    """Prueft ob aktuelle Uhrzeit im aktiven Fenster liegt."""
    if not active_hours or "-" not in active_hours:
        return True
    try:
        start_str, end_str = active_hours.split("-", 1)
        now = datetime.now()
        start = now.replace(
            hour=int(start_str.split(":")[0]),
            minute=int(start_str.split(":")[1]),
            second=0, microsecond=0
        )
        end = now.replace(
            hour=int(end_str.split(":")[0]),
            minute=int(end_str.split(":")[1]),
            second=0, microsecond=0
        )
        return start <= now <= end
    except (ValueError, IndexError):
        return True


def _is_due_cron(schedule: str, last_run: float | None) -> bool:
    """Prueft ob ein Cron-Task faellig ist."""
    try:
        from croniter import croniter
        base = datetime.fromtimestamp(last_run) if last_run else datetime(2000, 1, 1)
        it   = croniter(schedule, base)
        next_run = it.get_next(datetime)
        return datetime.now() >= next_run
    except Exception as e:
        logger.warning("Cron-Auswertung fehlgeschlagen (%s): %s", schedule, e)
        return False


def _is_due_interval(interval: int, last_run: float | None) -> bool:
    """Prueft ob ein Intervall-Task faellig ist."""
    if last_run is None:
        return True
    return (_now_ts() - last_run) >= interval


def _find_project(agent_id: str, preferred: str | None, projects) -> str | None:
    """Erstes Projekt in dem der Agent als Boss konfiguriert ist."""
    if preferred and projects.get(preferred):
        cfg = projects.get(preferred)
        if cfg and cfg.agents.boss == agent_id:
            return preferred

    for project_id, cfg in projects.items():
        if cfg.agents.boss == agent_id:
            return project_id
    return None


# ------------------------------------------------------------------ Scheduler

class HeartbeatScheduler:
    """
    Background-Scheduler fuer periodische Agent-Tasks.
    Wird einmal im Lifespan gestartet, laeuft fuer immer.
    """

    def __init__(
        self,
        discovery: "AgentDiscovery",
        projects,   # dict[str, ProjectConfig]
        orchestrator: "Orchestrator",
        agents_dir: Path,
    ) -> None:
        self._discovery   = discovery
        self._projects    = projects
        self._orchestrator = orchestrator
        self._agents_dir  = agents_dir
        self._task: asyncio.Task | None = None

    def start(self) -> asyncio.Task:
        self._task = asyncio.create_task(self._loop(), name="heartbeat-scheduler")
        logger.info("HeartbeatScheduler gestartet (Check alle %ds)", HEARTBEAT_CHECK_INTERVAL)
        return self._task

    async def _loop(self) -> None:
        while True:
            try:
                await self._check_all()
            except Exception as e:
                logger.error("Heartbeat Scheduler Fehler: %s", e)
            await asyncio.sleep(HEARTBEAT_CHECK_INTERVAL)

    async def _check_all(self) -> None:
        """Alle Agenten pruefen — faellige Tasks ausfuehren."""
        for agent_id, agent_cfg in self._discovery.agents.items():
            tasks = getattr(agent_cfg, "heartbeat_tasks", [])
            if not tasks:
                continue

            agent_dir = getattr(agent_cfg, "agent_dir", None)
            if not agent_dir:
                continue

            state = _load_state(agent_dir)
            changed = False

            for task in tasks:
                try:
                    fired = await self._check_task(agent_id, agent_cfg, task, state)
                    if fired:
                        changed = True
                except Exception as e:
                    logger.error("Heartbeat Task %s/%s Fehler: %s", agent_id, task.id, e)

            if changed:
                _save_state(agent_dir, state)

    async def _check_task(
        self,
        agent_id: str,
        agent_cfg: "AgentConfig",
        task: "HeartbeatTaskConfig",
        state: dict,
    ) -> bool:
        """Einen Task pruefen und ggf. ausfuehren. Gibt True zurueck wenn gefired."""
        last_run = state.get(task.id)

        # Faellig?
        if task.schedule:
            due = _is_due_cron(task.schedule, last_run)
        elif task.interval:
            due = _is_due_interval(task.interval, last_run)
        else:
            logger.warning("Heartbeat Task %s/%s hat weder schedule noch interval", agent_id, task.id)
            return False

        if not due:
            return False

        # Active-Hours-Check
        if not _is_active_hours(task.active_hours):
            logger.debug("Heartbeat Task %s/%s ausserhalb active_hours", agent_id, task.id)
            return False

        # Projekt finden
        project_id = _find_project(agent_id, task.project, self._projects)
        if not project_id:
            logger.warning("Heartbeat Task %s/%s — kein Projekt gefunden", agent_id, task.id)
            return False

        project_cfg = self._projects.get(project_id)
        if not project_cfg:
            return False

        # Task ausfuehren
        logger.info("Heartbeat Task %s/%s → Projekt %s", agent_id, task.id, project_id)
        try:
            await self._orchestrator.handle_message(
                project_id=project_id,
                project_cfg=project_cfg,
                content=task.message,
                sender=f"heartbeat:{task.id}",
            )
            state[task.id] = _now_ts()
            return True
        except Exception as e:
            logger.error("Heartbeat Task %s/%s Ausfuehrungsfehler: %s", agent_id, task.id, e)
            return False

    def get_status(self) -> dict:
        """Status aller Heartbeat-Tasks fuer die API."""
        result = {}
        for agent_id, agent_cfg in self._discovery.agents.items():
            tasks = getattr(agent_cfg, "heartbeat_tasks", [])
            if not tasks:
                continue
            agent_dir = getattr(agent_cfg, "agent_dir", None)
            state = _load_state(agent_dir) if agent_dir else {}
            result[agent_id] = []
            for task in tasks:
                last_run = state.get(task.id)
                next_run = None
                if task.schedule and last_run:
                    try:
                        from croniter import croniter
                        it = croniter(task.schedule, datetime.fromtimestamp(last_run))
                        next_run = it.get_next(datetime).isoformat()
                    except Exception:
                        pass
                elif task.interval and last_run:
                    from datetime import timedelta
                    next_run = datetime.fromtimestamp(last_run + task.interval).isoformat()
                result[agent_id].append({
                    "id":        task.id,
                    "message":   task.message,
                    "schedule":  task.schedule,
                    "interval":  task.interval,
                    "last_run":  datetime.fromtimestamp(last_run).isoformat() if last_run else None,
                    "next_run":  next_run,
                    "project":   task.project,
                })
        return result
