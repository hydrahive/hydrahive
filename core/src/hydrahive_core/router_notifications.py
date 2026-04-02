"""
router_notifications.py — Notification-Center API (#46)

GET  /notifications              — letzte 50 Notifications des Users
GET  /notifications/unread-count — Anzahl ungelesener
GET  /notifications/stream       — SSE-Stream für neue Notifications
PATCH /notifications/{id}/read   — als gelesen markieren
POST  /notifications/read-all    — alle als gelesen markieren
DELETE /notifications/{id}       — löschen
GET  /admin/notification-routes  — Notification-Routing laden
PUT  /admin/notification-routes  — Notification-Routing speichern
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from fastapi import APIRouter, Body, Depends, Request
from fastapi.responses import StreamingResponse

from .notification_service import notification_service

logger = logging.getLogger(__name__)

_NOTIF_ROUTES_FILE = Path("/etc/hydrahive/notification_routes.json")


def _load_notif_routes() -> dict:
    if _NOTIF_ROUTES_FILE.exists():
        try:
            return json.loads(_NOTIF_ROUTES_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {}


def _save_notif_routes(data: dict) -> None:
    _NOTIF_ROUTES_FILE.parent.mkdir(parents=True, exist_ok=True)
    _NOTIF_ROUTES_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def register_notification_routes(router: APIRouter, *, require_auth, verify_jwt, public_router: APIRouter) -> None:

    @router.get("/notifications")
    async def list_notifications(auth: tuple[str, str] = Depends(require_auth)):
        username, _ = auth
        items = notification_service.get_all(username, limit=50)
        return {"notifications": [_serialize(n) for n in items]}

    @router.get("/notifications/unread-count")
    async def unread_count(auth: tuple[str, str] = Depends(require_auth)):
        username, _ = auth
        return {"count": notification_service.unread_count(username)}

    # SSE-Stream — Auth nur via Authorization-Header (#136: kein Token in URL)
    @public_router.get("/notifications/stream")
    async def notification_stream(request: Request):
        from fastapi import HTTPException as _HTTPException
        auth_header = request.headers.get("Authorization", "")
        if not auth_header.startswith("Bearer "):
            raise _HTTPException(401, "Kein Token")
        username, _ = verify_jwt(auth_header[7:])

        async def event_stream():
            async for notif in notification_service.subscribe(username):
                data = json.dumps(_serialize(notif))
                yield f"data: {data}\n\n"

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @router.patch("/notifications/{notification_id}/read")
    async def mark_read(
        notification_id: str,
        auth: tuple[str, str] = Depends(require_auth),
    ):
        username, _ = auth
        ok = notification_service.mark_read(notification_id, username)
        return {"ok": ok}

    @router.post("/notifications/read-all")
    async def read_all(auth: tuple[str, str] = Depends(require_auth)):
        username, _ = auth
        count = notification_service.mark_all_read(username)
        return {"marked": count}

    @router.delete("/notifications/{notification_id}")
    async def delete_notification(
        notification_id: str,
        auth: tuple[str, str] = Depends(require_auth),
    ):
        username, _ = auth
        ok = notification_service.delete(notification_id, username)
        return {"ok": ok}

    # ── Notification-Routing (Blueprint) ─────────────────────────────────────

    @public_router.get("/admin/notification-routes")
    async def get_notification_routes(auth: tuple[str, str] = Depends(require_auth)):
        _, role = auth
        if role != "admin":
            from fastapi import HTTPException as _HTTPException
            raise _HTTPException(403, "Nur Admins")
        return _load_notif_routes()

    @public_router.put("/admin/notification-routes")
    async def save_notification_routes(
        body: dict = Body(...),
        auth: tuple[str, str] = Depends(require_auth),
    ):
        _, role = auth
        if role != "admin":
            from fastapi import HTTPException as _HTTPException
            raise _HTTPException(403, "Nur Admins")
        _save_notif_routes(body)
        return {"saved": True}


def _serialize(n) -> dict:
    return {
        "id":         n.id,
        "user":       n.user,
        "type":       n.type,
        "title":      n.title,
        "body":       n.body,
        "link":       n.link,
        "read":       n.read,
        "created_at": n.created_at,
    }
