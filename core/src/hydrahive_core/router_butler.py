"""router_butler.py — Butler Flow CRUD API

GET    /admin/butler/flows               → alle Flows
POST   /admin/butler/flows               → neuer Flow
PUT    /admin/butler/flows/{id}          → Flow überschreiben (Nodes + Edges)
PATCH  /admin/butler/flows/{id}/toggle   → aktivieren / deaktivieren
DELETE /admin/butler/flows/{id}          → Flow löschen
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .butler_rule import ButlerFlow, delete_flow, get_flow, load_flows, save_flow

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


def register_butler_routes(router: APIRouter, require_admin) -> None:

    @router.get("/butler/flows")
    async def list_flows(_auth=Depends(require_admin)):
        return [f.model_dump() for f in load_flows()]

    @router.post("/butler/flows")
    async def create_flow(req: FlowSaveRequest, _auth=Depends(require_admin)):
        flow = ButlerFlow(
            id=str(uuid.uuid4()),
            name=req.name,
            enabled=req.enabled,
            nodes=req.nodes,
            edges=req.edges,
        )
        save_flow(flow)
        return flow.model_dump()

    @router.put("/butler/flows/{flow_id}")
    async def update_flow(
        flow_id: str, req: FlowSaveRequest, _auth=Depends(require_admin)
    ):
        _check_id(flow_id)
        if get_flow(flow_id) is None:
            raise HTTPException(404, "Flow nicht gefunden")
        flow = ButlerFlow(
            id=flow_id,
            name=req.name,
            enabled=req.enabled,
            nodes=req.nodes,
            edges=req.edges,
        )
        save_flow(flow)
        return flow.model_dump()

    @router.patch("/butler/flows/{flow_id}/toggle")
    async def toggle_flow(flow_id: str, _auth=Depends(require_admin)):
        _check_id(flow_id)
        flow = get_flow(flow_id)
        if not flow:
            raise HTTPException(404, "Flow nicht gefunden")
        flow.enabled = not flow.enabled
        save_flow(flow)
        return {"enabled": flow.enabled}

    @router.delete("/butler/flows/{flow_id}")
    async def delete_flow_endpoint(flow_id: str, _auth=Depends(require_admin)):
        _check_id(flow_id)
        if not delete_flow(flow_id):
            raise HTTPException(404, "Flow nicht gefunden")
        return {"ok": True}
