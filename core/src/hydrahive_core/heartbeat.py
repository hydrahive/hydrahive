"""
heartbeat.py — Periodische Agenten-Aktivierung (#77)

Liest heartbeat_tasks aus agent.yaml und führt sie zur konfigurierten Zeit aus.
State (last_run pro Task) wird in /agents/{id}/.heartbeat_state.json gespeichert.

Unterstützte Formate:
  schedule: "0 8 * * *"   # Cron-Ausdruck (via croniter)
  interval: 1800           # Sekunden-Intervall

Projekt-Auflösung:
  - Explizit via task.project
  - Fallback: erstes Projekt in dem der Agent als Boss konfiguriert ist
  - Kein Projekt → Task wird nicht ausgeführt

Missed runs (Server war aus) → werden übersprungen, kein Backlog.
"""

import asyncio
import json
import logging
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

_STATE_FILE = ".heartbeat_state.json"


def _load_state(agent_dir: Path) -> dict[str, datetime]:
    path = agent_dir / _STATE_FILE
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
        return {k: datetime.fromisoformat(v) for k, v in raw.items()}
    except Exception:
        return {}


def _save_state(agent_dir: Path, task_id: str, dt: datetime) -> None:
    path = agent_dir / _STATE_FILE
    state = _load_state(agent_dir)
    state[task_id] = dt
    path.write_text(json.dumps({k: v.isoformat() for k, v in state.items()}))


def _in_active_hours(active_hours: str, now: datetime) -> bool:
    """Prüft ob 'now' im Fenster 'HH:MM-HH:MM' liegt."""
    try:
        start_s, end_s = active_hours.split("-")
        sh, sm = map(int, start_s.strip().split(":"))
        eh, em = map(int, end_s.strip().split(":"))
        start_min = sh * 60 + sm
        end_min   = eh * 60 + em
        cur_min   = now.hour * 60 + now.minute
        return start_min <= cur_min <= end_min
    except Exception:
        return True  # bei Parse-Fehler: immer aktiv


def _is_due_cron(schedule: str, last_run: datetime | None, now: datetime) -> bool:
    try:
        from croniter import croniter
        if last_run is None:
            base = now.replace(second=0, microsecond=0)
            cron = croniter(schedule, base)
            prev = cron.get_prev(datetime)
            return prev >= base
        cron = croniter(schedule, last_run)
        return cron.get_next(datetime) <= now
    except Exception as e:
        logger.warning("Cron-Ausdruck ungültig '%s': %s", schedule, e)
        return False


def _is_due_interval(interval: int, last_run: datetime | None, now: datetime) -> bool:
    if last_run is None:
        return True
    return (now - last_run).total_seconds() >= interval


class AgentHeartbeatScheduler:
    """
    Läuft als asyncio-Task. Prüft alle 60 Sekunden welche Heartbeat-Tasks fällig sind.
    """

    def __init__(self, discovery, projects, orchestrator, agents_dir: str | Path):
        self._discovery    = discovery
        self._projects     = projects
        self._orchestrator = orchestrator
        self._agents_dir   = Path(agents_dir)

    async def run(self) -> None:
        logger.info("HeartbeatScheduler gestartet")
        while True:
            try:
                await asyncio.sleep(60)
                await self._tick()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("HeartbeatScheduler Fehler: %s", e)

    async def _tick(self) -> None:
        now = datetime.now().replace(second=0, microsecond=0)
        for agent_id, cfg in self._discovery.agents.items():
            if not cfg.heartbeat_tasks:
                continue
            agent_dir = self._agents_dir / agent_id
            state     = _load_state(agent_dir)
            for task in cfg.heartbeat_tasks:
                try:
                    await self._check_task(agent_id, cfg, task, agent_dir, state, now)
                except Exception as e:
                    logger.warning("Heartbeat-Task '%s/%s' Fehler: %s", agent_id, task.id, e)

    async def _check_task(self, agent_id, cfg, task, agent_dir, state, now) -> None:
        last_run = state.get(task.id)

        # active_hours prüfen
        if task.active_hours and not _in_active_hours(task.active_hours, now):
            return

        # Fälligkeitsprüfung
        if task.schedule:
            due = _is_due_cron(task.schedule, last_run, now)
        elif task.interval:
            due = _is_due_interval(task.interval, last_run, now)
        else:
            return

        if not due:
            return

        # Projekt auflösen
        project_id = task.project or self._find_project(agent_id)
        if not project_id:
            logger.debug("Heartbeat-Task '%s/%s': kein Projekt gefunden → übersprungen", agent_id, task.id)
            return

        project_cfg = self._projects.get(project_id)
        if not project_cfg:
            logger.debug("Heartbeat-Task '%s/%s': Projekt '%s' nicht geladen → übersprungen", agent_id, task.id, project_id)
            return

        logger.info("Heartbeat-Task '%s/%s' → Projekt '%s': %s", agent_id, task.id, project_id, task.message[:60])
        try:
            await self._orchestrator.handle_message(project_id, project_cfg, task.message, sender="heartbeat")
            _save_state(agent_dir, task.id, now)
        except Exception as e:
            logger.warning("Heartbeat-Task '%s/%s' Ausführung fehlgeschlagen: %s", agent_id, task.id, e)

    def _find_project(self, agent_id: str) -> str | None:
        """Erstes Projekt in dem der Agent als Boss konfiguriert ist."""
        for proj_id, proj_cfg in self._projects.projects.items():
            if getattr(proj_cfg.agents, "boss", None) == agent_id:
                return proj_id
        return None

    def task_summary(self) -> list[dict]:
        """Für API-Endpunkt: alle registrierten Tasks mit letztem Lauf."""
        result = []
        for agent_id, cfg in self._discovery.agents.items():
            if not cfg.heartbeat_tasks:
                continue
            agent_dir = self._agents_dir / agent_id
            state     = _load_state(agent_dir)
            for task in cfg.heartbeat_tasks:
                result.append({
                    "agent_id": agent_id,
                    "task_id":  task.id,
                    "schedule": task.schedule,
                    "interval": task.interval,
                    "message":  task.message[:80],
                    "project":  task.project,
                    "last_run": state[task.id].isoformat() if task.id in state else None,
                })
        return result
