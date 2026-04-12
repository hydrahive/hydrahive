"""router_butler.py — Butler Flow CRUD API (user-scoped)

GET    /butler/flows               → eigene Flows
POST   /butler/flows               → neuer Flow
PUT    /butler/flows/{id}          → Flow überschreiben (Nodes + Edges)
PATCH  /butler/flows/{id}/toggle   → aktivieren / deaktivieren
DELETE /butler/flows/{id}          → Flow löschen

Flows sind an den eingeloggten User gebunden — andere User sehen
und bearbeiten ihre eigenen Flows unabhängig.
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .butler_rule import (
    ButlerFlow, delete_flow, get_flow, load_flows, save_flow,
    load_flows_for_project, get_flow_for_project,
    save_flow_for_project, delete_flow_for_project,
)

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


class FlowSaveRequest(BaseModel):
    name: str
    enabled: bool = True
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


def _check_id(flow_id: str) -> None:
    if not _UUID_RE.match(flow_id):
        raise HTTPException(400, "Ungültige Flow-ID")


def register_butler_routes(router: APIRouter, require_auth) -> None:

    @router.get("/butler/flows")
    async def list_flows(auth=Depends(require_auth)):
        username, _ = auth
        return [f.model_dump() for f in load_flows(owner=username)]

    @router.post("/butler/flows")
    async def create_flow(req: FlowSaveRequest, auth=Depends(require_auth)):
        username, _ = auth
        flow = ButlerFlow(
            id=str(uuid.uuid4()),
            name=req.name,
            owner=username,
            enabled=req.enabled,
            nodes=req.nodes,
            edges=req.edges,
        )
        save_flow(flow)
        return flow.model_dump()

    @router.put("/butler/flows/{flow_id}")
    async def update_flow(
        flow_id: str, req: FlowSaveRequest, auth=Depends(require_auth)
    ):
        username, _ = auth
        _check_id(flow_id)
        if get_flow(flow_id, username) is None:
            raise HTTPException(404, "Flow nicht gefunden")
        flow = ButlerFlow(
            id=flow_id,
            name=req.name,
            owner=username,
            enabled=req.enabled,
            nodes=req.nodes,
            edges=req.edges,
        )
        save_flow(flow)
        return flow.model_dump()

    @router.patch("/butler/flows/{flow_id}/toggle")
    async def toggle_flow(flow_id: str, auth=Depends(require_auth)):
        username, _ = auth
        _check_id(flow_id)
        flow = get_flow(flow_id, username)
        if not flow:
            raise HTTPException(404, "Flow nicht gefunden")
        flow.enabled = not flow.enabled
        save_flow(flow)
        return {"enabled": flow.enabled}

    @router.delete("/butler/flows/{flow_id}")
    async def delete_flow_endpoint(flow_id: str, auth=Depends(require_auth)):
        username, _ = auth
        _check_id(flow_id)
        if not delete_flow(flow_id, username):
            raise HTTPException(404, "Flow nicht gefunden")
        return {"ok": True}

    # ── Projekt-scoped Butler-Flows (#566) ──────────────────────────────

    def _check_project_butler_access(auth: tuple, project_id: str) -> None:
        """Prüft ob das Projekt existiert und der User darauf zugreifen darf."""
        from pathlib import Path as _Path
        from .settings import settings as _s
        project_dir = _Path(_s.projects_dir) / project_id
        if not project_dir.exists():
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
        # Admins haben immer Zugang
        role = auth[1] if len(auth) > 1 else ""
        if role == "admin":
            return
        # Members-Check via config.yaml (#596: HTTPException nicht mehr verschlucken)
        config_path = project_dir / "config.yaml"
        members = []
        if config_path.exists():
            import yaml
            try:
                cfg = yaml.safe_load(config_path.read_text()) or {}
                members = cfg.get("members", []) or []
            except (yaml.YAMLError, OSError) as e:
                logger.warning("Butler-Access: config.yaml von %s nicht lesbar: %s",
                               project_id, e)
                raise HTTPException(500, "Projekt-Config nicht lesbar")
        # #595: Fail-closed — User muss explizit Member sein (oder Admin, oben gecheckt)
        # Ausnahme: Personal-Projekt des Users
        if auth[0] not in members and project_id != f"personal_{auth[0]}":
            raise HTTPException(403, "Kein Zugang zu diesem Projekt")

    @router.get("/projects/{project_id}/butler/flows")
    async def list_project_flows(project_id: str, auth=Depends(require_auth)):
        _check_project_butler_access(auth, project_id)
        return [f.model_dump() for f in load_flows_for_project(project_id)]

    @router.post("/projects/{project_id}/butler/flows")
    async def create_project_flow(
        project_id: str, req: FlowSaveRequest, auth=Depends(require_auth)
    ):
        _check_project_butler_access(auth, project_id)
        flow = ButlerFlow(
            id=str(uuid.uuid4()),
            name=req.name,
            owner=f"project:{project_id}",
            enabled=req.enabled,
            nodes=req.nodes,
            edges=req.edges,
        )
        save_flow_for_project(flow, project_id)
        return flow.model_dump()

    @router.put("/projects/{project_id}/butler/flows/{flow_id}")
    async def update_project_flow(
        project_id: str, flow_id: str, req: FlowSaveRequest, auth=Depends(require_auth)
    ):
        _check_project_butler_access(auth, project_id)
        _check_id(flow_id)
        if get_flow_for_project(flow_id, project_id) is None:
            raise HTTPException(404, "Flow nicht gefunden")
        flow = ButlerFlow(
            id=flow_id,
            name=req.name,
            owner=f"project:{project_id}",
            enabled=req.enabled,
            nodes=req.nodes,
            edges=req.edges,
        )
        save_flow_for_project(flow, project_id)
        return flow.model_dump()

    @router.patch("/projects/{project_id}/butler/flows/{flow_id}/toggle")
    async def toggle_project_flow(
        project_id: str, flow_id: str, auth=Depends(require_auth)
    ):
        _check_project_butler_access(auth, project_id)
        _check_id(flow_id)
        flow = get_flow_for_project(flow_id, project_id)
        if not flow:
            raise HTTPException(404, "Flow nicht gefunden")
        flow.enabled = not flow.enabled
        save_flow_for_project(flow, project_id)
        return {"enabled": flow.enabled}

    @router.delete("/projects/{project_id}/butler/flows/{flow_id}")
    async def delete_project_flow_endpoint(
        project_id: str, flow_id: str, auth=Depends(require_auth)
    ):
        _check_project_butler_access(auth, project_id)
        _check_id(flow_id)
        if not delete_flow_for_project(flow_id, project_id):
            raise HTTPException(404, "Flow nicht gefunden")
        return {"ok": True}
