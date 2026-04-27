"""
router_dream.py — Admin-API für AutoDream

Endpoints:
  GET  /api/admin/dream/config   → aktuelle Konfiguration
  PUT  /api/admin/dream/config   → Konfiguration speichern
  GET  /api/admin/dream/status   → Status aller Agenten (last_dream_at, count, hours_since)
  POST /api/admin/dream/run      → Dream sofort auslösen (optional ?agent_id=xxx)
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Callable

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .auto_dream import DEFAULT_CONFIG, _load_dream_config, save_dream_config, _read_dream_state, _run_dream_for_agent
from .settings import settings


class DreamConfig(BaseModel):
    enabled: bool = True
    min_hours: float = 12.0
    min_sessions: int = 1
    check_interval_seconds: int = 600
    max_transcript_chars: int = 60000
    summary_model: str = "claude-haiku-4-5-20251001"


class DreamAgentStatus(BaseModel):
    agent_id: str
    last_dream_at: str | None
    hours_since_dream: float | None
    dream_count: int


def register_dream_routes(router: APIRouter, require_admin: Callable) -> None:

    @router.get("/admin/dream/config", response_model=DreamConfig)
    async def get_dream_config(_: Any = Depends(require_admin)):
        cfg = _load_dream_config()
        return DreamConfig(**{k: cfg[k] for k in DreamConfig.model_fields if k in cfg})

    @router.put("/admin/dream/config", response_model=DreamConfig)
    async def put_dream_config(body: DreamConfig, _: Any = Depends(require_admin)):
        save_dream_config(body.model_dump())
        return body

    @router.get("/admin/dream/status", response_model=list[DreamAgentStatus])
    async def get_dream_status(_: Any = Depends(require_admin)):
        projects_dir = settings.projects_dir
        results: list[DreamAgentStatus] = []

        if not projects_dir.exists():
            return results

        for proj_dir in sorted(projects_dir.iterdir()):
            if not proj_dir.is_dir():
                continue
            state = _read_dream_state(proj_dir)
            last_ts: float = state.get("last_dream_at", 0) or 0
            count: int = state.get("dream_count", 0)

            if last_ts > 0:
                last_iso = datetime.fromtimestamp(last_ts, tz=timezone.utc).isoformat()
                hours_since = round((time.time() - last_ts) / 3600, 1)
            else:
                last_iso = None
                hours_since = None

            results.append(DreamAgentStatus(
                agent_id=proj_dir.name,
                last_dream_at=last_iso,
                hours_since_dream=hours_since,
                dream_count=count,
            ))

        return results

    @router.post("/admin/dream/run")
    async def run_dream_now(agent_id: str | None = None, _: Any = Depends(require_admin)):
        """Dream sofort auslösen — Gates werden durch force_min_hours=0 umgangen."""
        projects_dir = settings.projects_dir
        triggered: list[str] = []
        skipped: list[str] = []

        if agent_id:
            target = projects_dir / agent_id
            if not target.exists():
                raise HTTPException(status_code=404, detail=f"Agent/Project not found: {agent_id}")
            dirs = [target]
        else:
            dirs = [d for d in sorted(projects_dir.iterdir()) if d.is_dir()]

        # Force: min_hours=0 damit Gates nicht blockieren
        cfg = {**_load_dream_config(), "min_hours": 0, "min_sessions": 0}

        for proj_dir in dirs:
            try:
                await _run_dream_for_agent(proj_dir.name, proj_dir, cfg, notify_fn=None)
                triggered.append(proj_dir.name)
            except Exception:
                skipped.append(proj_dir.name)

        return {"triggered": triggered, "skipped": skipped}
