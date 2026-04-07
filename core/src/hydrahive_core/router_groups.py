"""
router_groups.py — Gruppen-Verwaltungs-API (#165)

GET    /admin/groups              — Alle Gruppen auflisten
POST   /admin/groups              — Neue Gruppe erstellen
PUT    /admin/groups/{group_id}   — Gruppe bearbeiten
DELETE /admin/groups/{group_id}   — Gruppe löschen (nicht builtin)
GET    /admin/groups/permissions/{username} — Effektive Permissions eines Users
"""
from __future__ import annotations

import re
from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field
from typing import Any

_GROUP_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,30}$")


# #457: Pydantic Models statt raw dict für Input-Validierung
class CreateGroupRequest(BaseModel):
    id: str = Field(..., min_length=1, max_length=31, pattern=r"^[a-z][a-z0-9_-]*$")
    label: str = Field("", max_length=100)
    description: str = Field("", max_length=500)
    permissions: dict[str, Any] = Field(default_factory=dict)

class UpdateGroupRequest(BaseModel):
    label: str | None = Field(None, max_length=100)
    description: str | None = Field(None, max_length=500)
    permissions: dict[str, Any] | None = None


def register_group_routes(
    admin_router: APIRouter,
    auth_router: APIRouter,
    *,
    require_admin,
    require_auth,
    group_service,
) -> None:

    @admin_router.get("/admin/groups")
    def list_groups(_a=Depends(require_admin)):
        """Alle Gruppen mit Permissions auflisten."""
        groups = group_service.list_groups()
        return {"groups": groups}

    @admin_router.post("/admin/groups", status_code=201)
    def create_group(req: CreateGroupRequest, _a=Depends(require_admin)):
        """Neue Gruppe erstellen (#457: Pydantic-Validierung)."""
        try:
            group = group_service.create_group(req.id, req.model_dump())
            return {"created": True, "id": req.id, "group": group}
        except ValueError as e:
            raise HTTPException(409, str(e))

    @admin_router.put("/admin/groups/{group_id}")
    def update_group(group_id: str, req: UpdateGroupRequest, _a=Depends(require_admin)):
        """Gruppe bearbeiten (#457: Pydantic-Validierung)."""
        result = group_service.update_group(group_id, req.model_dump(exclude_none=True))
        if result is None:
            raise HTTPException(404, f"Gruppe '{group_id}' nicht gefunden")
        return {"updated": True, "id": group_id, "group": result}

    @admin_router.delete("/admin/groups/{group_id}")
    def delete_group(group_id: str, _a=Depends(require_admin)):
        """Gruppe löschen (builtin-Gruppen können nicht gelöscht werden)."""
        ok = group_service.delete_group(group_id)
        if not ok:
            raise HTTPException(400, f"Gruppe '{group_id}' nicht gefunden oder ist eine System-Gruppe")
        return {"deleted": True, "id": group_id}

    @auth_router.get("/me/permissions")
    def my_permissions(auth: tuple = Depends(require_auth)):
        """Eigene effektive Permissions abrufen (für Frontend-Filtering)."""
        username, _ = auth
        perms = group_service.get_permissions(username)
        group_id = group_service._get_user_group(username)
        group = group_service.get_group(group_id) or {}
        return {
            "group": group_id,
            "group_label": group.get("label", group_id),
            "permissions": perms,
        }
