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

_GROUP_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,30}$")


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
    def create_group(body: dict = Body(...), _a=Depends(require_admin)):
        """Neue Gruppe erstellen."""
        group_id = body.get("id", "").strip().lower()
        if not group_id or not _GROUP_ID_RE.fullmatch(group_id):
            raise HTTPException(400, "Ungültige Gruppen-ID (a-z, 0-9, _, -, max 31 Zeichen)")
        try:
            group = group_service.create_group(group_id, body)
            return {"created": True, "id": group_id, "group": group}
        except ValueError as e:
            raise HTTPException(409, str(e))

    @admin_router.put("/admin/groups/{group_id}")
    def update_group(group_id: str, body: dict = Body(...), _a=Depends(require_admin)):
        """Gruppe bearbeiten (Permissions, Label, Description)."""
        result = group_service.update_group(group_id, body)
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
