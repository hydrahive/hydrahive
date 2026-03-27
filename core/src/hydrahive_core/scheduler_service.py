"""
scheduler_service.py — Cron-Scheduler für autonome Agenten-Ausführung (#45)

Schedules werden in /etc/hydrahive/schedules.json persistiert.
Der SchedulerService prüft jede Minute fällige Jobs und löst sie via
Orchestrator aus. Notification-Center (#46) wird bei jedem Run informiert.

Verwendung:
    from .scheduler_service import scheduler_service
    scheduler_service.start(orchestrator, load_project_cfg_fn, load_users_fn)
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

logger = logging.getLogger(__name__)

SCHEDULES_FILE = Path("/etc/hydrahive/schedules.json")


# ---------------------------------------------------------------------------
# Datenmodell
# ---------------------------------------------------------------------------

class Schedule:
    __slots__ = (
        "id", "name", "project_id", "agent_id", "cron", "message",
        "enabled", "timezone", "last_run", "created_by",
    )

    def __init__(
        self,
        *,
        id: str,
        name: str,
        project_id: str,
        agent_id: str,
        cron: str,
        message: str,
        enabled: bool = True,
        timezone: str = "UTC",
        last_run: str | None = None,
        created_by: str = "admin",
    ):
        self.id         = id
        self.name       = name
        self.project_id = project_id
        self.agent_id   = agent_id
        self.cron       = cron
        self.message    = message
        self.enabled    = enabled
        self.timezone   = timezone
        self.last_run   = last_run
        self.created_by = created_by

    def to_dict(self) -> dict:
        return {s: getattr(self, s) for s in self.__slots__}

    @classmethod
    def from_dict(cls, d: dict) -> "Schedule":
        return cls(
            id=d["id"],
            name=d.get("name", d["id"]),
            project_id=d["project_id"],
            agent_id=d["agent_id"],
            cron=d["cron"],
            message=d["message"],
            enabled=d.get("enabled", True),
            timezone=d.get("timezone", "UTC"),
            last_run=d.get("last_run"),
            created_by=d.get("created_by", "admin"),
        )


def _next_run_iso(cron: str, tz: str = "UTC") -> str | None:
    """Berechnet den nächsten Ausführungszeitpunkt (ISO-8601)."""
    try:
        from croniter import croniter
        import pytz
        now = datetime.now(pytz.timezone(tz))
        it  = croniter(cron, now)
        return it.get_next(datetime).isoformat()
    except Exception:
        return None


def _is_due(cron: str, tz: str, last_run: str | None) -> bool:
    """True wenn der Job in dieser Minute fällig ist."""
    try:
        from croniter import croniter
        import pytz
        tzinfo = pytz.timezone(tz)
        now    = datetime.now(tzinfo)
        # Aktuelle Minute (Sekunden auf 0)
        minute_start = now.replace(second=0, microsecond=0)
        it = croniter(cron, minute_start)
        prev = it.get_prev(datetime)
        # Fällig wenn prev == aktuelle Minute UND nicht bereits in dieser Minute gelaufen
        if abs((prev - minute_start).total_seconds()) > 30:
            return False
        if last_run:
            try:
                lr = datetime.fromisoformat(last_run)
                if lr.tzinfo is None:
                    lr = lr.replace(tzinfo=timezone.utc)
                if (minute_start.astimezone(timezone.utc) - lr.astimezone(timezone.utc)).total_seconds() < 55:
                    return False
            except Exception:
                pass
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------

class SchedulerService:
    def __init__(self) -> None:
        self._schedules: dict[str, Schedule] = {}
        self._task: asyncio.Task | None = None
        self._orchestrator: Any = None
        self._load_project_cfg: Callable | None = None
        self._load_users: Callable | None = None

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    def start(
        self,
        orchestrator: Any,
        load_project_cfg_fn: Callable,
        load_users_fn: Callable,
    ) -> None:
        self._orchestrator     = orchestrator
        self._load_project_cfg = load_project_cfg_fn
        self._load_users       = load_users_fn
        self._load()
        self._task = asyncio.create_task(self._loop())
        logger.info("SchedulerService gestartet (%d Schedules)", len(self._schedules))

    def stop(self) -> None:
        if self._task:
            self._task.cancel()
            self._task = None

    # ------------------------------------------------------------------ #
    # Persistenz                                                           #
    # ------------------------------------------------------------------ #

    def _load(self) -> None:
        if not SCHEDULES_FILE.exists():
            return
        try:
            data = json.loads(SCHEDULES_FILE.read_text(encoding="utf-8"))
            self._schedules = {d["id"]: Schedule.from_dict(d) for d in data}
        except Exception as e:
            logger.warning("Schedules laden fehlgeschlagen: %s", e)

    def _save(self) -> None:
        try:
            SCHEDULES_FILE.parent.mkdir(parents=True, exist_ok=True)
            SCHEDULES_FILE.write_text(
                json.dumps([s.to_dict() for s in self._schedules.values()],
                           indent=2, ensure_ascii=False),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("Schedules speichern fehlgeschlagen: %s", e)

    # ------------------------------------------------------------------ #
    # CRUD                                                                 #
    # ------------------------------------------------------------------ #

    def list_schedules(self, user: str, role: str) -> list[dict]:
        result = []
        for s in self._schedules.values():
            if role == "admin" or s.created_by == user:
                d = s.to_dict()
                d["next_run"] = _next_run_iso(s.cron, s.timezone)
                result.append(d)
        return result

    def get(self, schedule_id: str, user: str, role: str) -> Schedule | None:
        s = self._schedules.get(schedule_id)
        if s is None:
            return None
        if role != "admin" and s.created_by != user:
            return None
        return s

    def create(self, data: dict, created_by: str) -> Schedule:
        s = Schedule(
            id=str(uuid.uuid4()),
            name=data["name"],
            project_id=data["project_id"],
            agent_id=data["agent_id"],
            cron=data["cron"],
            message=data["message"],
            enabled=data.get("enabled", True),
            timezone=data.get("timezone", "UTC"),
            created_by=created_by,
        )
        self._schedules[s.id] = s
        self._save()
        return s

    def update(self, schedule_id: str, data: dict, user: str, role: str) -> Schedule | None:
        s = self.get(schedule_id, user, role)
        if s is None:
            return None
        for field in ("name", "cron", "message", "enabled", "timezone"):
            if field in data:
                setattr(s, field, data[field])
        self._save()
        return s

    def delete(self, schedule_id: str, user: str, role: str) -> bool:
        s = self.get(schedule_id, user, role)
        if s is None:
            return False
        del self._schedules[schedule_id]
        self._save()
        return True

    # ------------------------------------------------------------------ #
    # Scheduler-Loop                                                       #
    # ------------------------------------------------------------------ #

    async def _loop(self) -> None:
        """Prüft jede Minute ob Jobs fällig sind."""
        while True:
            try:
                await asyncio.sleep(60)
                for s in list(self._schedules.values()):
                    if s.enabled and _is_due(s.cron, s.timezone, s.last_run):
                        asyncio.create_task(self._run(s))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning("Scheduler-Loop Fehler: %s", e)

    async def _run(self, s: Schedule) -> None:
        logger.info("Schedule ausführen: %s [%s] → %s", s.name, s.id, s.agent_id)
        s.last_run = datetime.now(timezone.utc).isoformat()
        self._save()

        # Projekt-Config laden
        cfg = None
        if self._load_project_cfg:
            try:
                cfg = self._load_project_cfg(s.project_id)
            except Exception as e:
                logger.warning("Schedule %s: Projekt-Config nicht geladen: %s", s.id, e)

        if cfg is None:
            logger.warning("Schedule %s: Projekt '%s' nicht gefunden — überspringe", s.id, s.project_id)
            await self._notify(s, success=False, detail=f"Projekt '{s.project_id}' nicht gefunden")
            return

        # Nachricht über Orchestrator senden
        try:
            response = ""
            async for chunk in self._orchestrator.handle_message_stream(
                project_id=s.project_id,
                project_cfg=cfg,
                content=s.message,
                sender=f"scheduler:{s.id}",
            ):
                # SSE-Chunks akkumulieren um Antwort-Preview zu bekommen
                if chunk.startswith("data: "):
                    try:
                        import json as _j
                        d = _j.loads(chunk[6:])
                        # {"type":"text","content":"..."} oder {"text":"..."}
                        if d.get("type") == "text":
                            response += d.get("content", "")
                        elif "text" in d and isinstance(d["text"], str):
                            response += d["text"]
                    except Exception:
                        pass
            await self._notify(s, success=True, detail=response[:120])
        except Exception as e:
            logger.error("Schedule %s Ausführungsfehler: %s", s.id, e)
            await self._notify(s, success=False, detail=str(e)[:120])

    async def _notify(self, s: Schedule, success: bool, detail: str = "") -> None:
        try:
            from .notification_service import notification_service as _ns
            users: list[str] = []
            if s.project_id.startswith("personal_"):
                users = [s.project_id[len("personal_"):]]
            elif self._load_users:
                all_users = self._load_users()
                users = [u for u, d in all_users.items()
                         if d.get("role") == "admin" or u == s.created_by]
            users = list(dict.fromkeys(users)) or ["admin"]  # dedup

            notif_type = "schedule_run" if success else "task_failed"
            title = f"{'✓' if success else '✗'} {s.name}"
            body  = detail or ("Ausgeführt" if success else "Fehlgeschlagen")
            for user in users:
                await _ns.push(user=user, type=notif_type,
                               title=title, body=body,
                               link=f"/chat/{s.project_id}")
        except Exception as e:
            logger.debug("Schedule Notification fehlgeschlagen: %s", e)


# Singleton
scheduler_service = SchedulerService()
