"""router_skill_packages.py — Skill Package CRUD API

GET    /admin/skill-packages               → alle Pakete
POST   /admin/skill-packages               → neues Paket
GET    /admin/skill-packages/{pkg_id}      → einzelnes Paket
PUT    /admin/skill-packages/{pkg_id}      → Paket überschreiben (Nodes + Edges)
DELETE /admin/skill-packages/{pkg_id}      → Paket löschen
"""
from __future__ import annotations

import logging
import re
import uuid
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .skill_package_rule import (
    SkillPackage,
    delete_package,
    get_package,
    load_packages,
    save_package,
)

logger = logging.getLogger(__name__)

_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)


class SkillPackageSaveRequest(BaseModel):
    name: str
    description: str = ""
    enabled: bool = True
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


def _check_id(pkg_id: str) -> None:
    if not _UUID_RE.match(pkg_id):
        raise HTTPException(400, "Ungültige Paket-ID")


def register_skill_package_routes(router: APIRouter, require_admin) -> None:

    @router.get("/admin/skill-packages")
    async def list_packages(_auth=Depends(require_admin)):
        return [p.model_dump() for p in load_packages()]

    @router.post("/admin/skill-packages")
    async def create_package(req: SkillPackageSaveRequest, _auth=Depends(require_admin)):
        pkg = SkillPackage(
            id=str(uuid.uuid4()),
            name=req.name,
            description=req.description,
            enabled=req.enabled,
            nodes=req.nodes,
            edges=req.edges,
        )
        save_package(pkg)
        return pkg.model_dump()

    @router.get("/admin/skill-packages/{pkg_id}")
    async def get_one_package(pkg_id: str, _auth=Depends(require_admin)):
        _check_id(pkg_id)
        pkg = get_package(pkg_id)
        if pkg is None:
            raise HTTPException(404, "Paket nicht gefunden")
        return pkg.model_dump()

    @router.put("/admin/skill-packages/{pkg_id}")
    async def update_package(
        pkg_id: str, req: SkillPackageSaveRequest, _auth=Depends(require_admin)
    ):
        _check_id(pkg_id)
        if get_package(pkg_id) is None:
            raise HTTPException(404, "Paket nicht gefunden")
        pkg = SkillPackage(
            id=pkg_id,
            name=req.name,
            description=req.description,
            enabled=req.enabled,
            nodes=req.nodes,
            edges=req.edges,
        )
        save_package(pkg)
        return pkg.model_dump()

    @router.delete("/admin/skill-packages/{pkg_id}")
    async def delete_package_endpoint(pkg_id: str, _auth=Depends(require_admin)):
        _check_id(pkg_id)
        if not delete_package(pkg_id):
            raise HTTPException(404, "Paket nicht gefunden")
        return {"ok": True}
