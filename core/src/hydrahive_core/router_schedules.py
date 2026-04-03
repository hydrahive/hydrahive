"""
router_schedules.py — CRUD-Endpunkte für den Cron-Scheduler (#45)

    GET    /schedules        — Liste (admin: alle, user: eigene)
    POST   /schedules        — Anlegen
    PATCH  /schedules/{id}   — Aktualisieren
    DELETE /schedules/{id}   — Löschen
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .scheduler_service import scheduler_service


class ScheduleCreate(BaseModel):
    name: str
    project_id: str
    agent_id: str
    cron: str
    message: str
    enabled: bool = True
    timezone: str = "UTC"


class ScheduleUpdate(BaseModel):
    name: str | None = None
    cron: str | None = None
    message: str | None = None
    enabled: bool | None = None
    timezone: str | None = None


def register_schedule_routes(router: APIRouter, *, require_auth) -> None:

    @router.get("/schedules")
    def list_schedules(auth: tuple[str, str] = Depends(require_auth)):
        user, role = auth
        return {"schedules": scheduler_service.list_schedules(user, role)}

    @router.post("/schedules", status_code=201)
    def create_schedule(body: ScheduleCreate, auth: tuple[str, str] = Depends(require_auth)):
        user, _ = auth
        s = scheduler_service.create(body.model_dump(), created_by=user)
        return s.to_dict()

    @router.patch("/schedules/{schedule_id}")
    def update_schedule(
        schedule_id: str,
        body: ScheduleUpdate,
        auth: tuple[str, str] = Depends(require_auth),
    ):
        user, role = auth
        data = {k: v for k, v in body.model_dump().items() if v is not None}
        s = scheduler_service.update(schedule_id, data, user, role)
        if s is None:
            raise HTTPException(404, "Schedule nicht gefunden")
        return s.to_dict()

    @router.delete("/schedules/{schedule_id}")
    def delete_schedule(
        schedule_id: str,
        auth: tuple[str, str] = Depends(require_auth),
    ):
        user, role = auth
        ok = scheduler_service.delete(schedule_id, user, role)
        if not ok:
            raise HTTPException(404, "Schedule nicht gefunden")
        return {"deleted": True}

    @router.post("/schedules/{schedule_id}/run", status_code=202)
    async def run_schedule_now(
        schedule_id: str,
        auth: tuple[str, str] = Depends(require_auth),
    ):
        import asyncio
        user, role = auth
        s = scheduler_service.get(schedule_id, user, role)
        if s is None:
            raise HTTPException(404, "Schedule nicht gefunden")
        asyncio.create_task(scheduler_service._run(s))
        return {"triggered": True, "schedule_id": schedule_id}
