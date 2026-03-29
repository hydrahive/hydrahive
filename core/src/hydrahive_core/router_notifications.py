"""
router_notifications.py — Notification-Center API (#46)

GET  /notifications              — letzte 50 Notifications des Users
GET  /notifications/unread-count — Anzahl ungelesener
GET  /notifications/stream       — SSE-Stream für neue Notifications
PATCH /notifications/{id}/read   — als gelesen markieren
POST  /notifications/read-all    — alle als gelesen markieren
DELETE /notifications/{id}       — löschen
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import StreamingResponse

from .notification_service import notification_service

logger = logging.getLogger(__name__)


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

    # SSE-Stream — primär fetch+Authorization-Header, Fallback ?token= für alte Clients
    @public_router.get("/notifications/stream")
    async def notification_stream(request: Request, token: str | None = None):
        from fastapi import HTTPException as _HTTPException
        # Bevorzuge Authorization-Header (kein Token in URL/Logs)
        auth_header = request.headers.get("Authorization", "")
        if auth_header.startswith("Bearer "):
            resolved_token = auth_header[7:]
        elif token:
            resolved_token = token
        else:
            raise _HTTPException(401, "Kein Token")
        username, _ = verify_jwt(resolved_token)

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
