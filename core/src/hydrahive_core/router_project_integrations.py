from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel

VALID_EVENTS = {"message", "agent_error", "provision", "agent_start", "agent_stop"}


class AgentLinkCreateRequest(BaseModel):
    from_agent: str = "manual"
    to_agent: str = ""
    context: str = ""
    data: dict = {}
    ttl_seconds: int = 3600


class WebhookTestRequest(BaseModel):
    url: str
    secret: str = ""


class WebhookRequest(BaseModel):
    name: str
    url: str
    secret: str = ""
    events: list[str] = ["message"]


def _webhooks_file(projects_dir: str, project_id: str) -> Path:
    return Path(projects_dir) / project_id / "webhooks.json"


def _load_webhooks(projects_dir: str, project_id: str) -> list[dict]:
    f = _webhooks_file(projects_dir, project_id)
    try:
        return json.loads(f.read_text())
    except (OSError, ValueError):
        return []


def _save_webhooks(projects_dir: str, project_id: str, webhooks: list[dict]) -> None:
    f = _webhooks_file(projects_dir, project_id)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(webhooks, indent=2), encoding="utf-8")


async def _fire_webhook(webhook: dict, event: str, data: dict, *, logger) -> None:
    import hashlib as _hl
    import hmac as _hm
    import time as _time

    payload = json.dumps({
        "event": event,
        "project_id": data.get("project_id", ""),
        "timestamp": _time.time(),
        "data": data,
    })
    headers = {
        "Content-Type": "application/json",
        "User-Agent": "HydraHive-Webhook/1.0",
        "X-HydraHive-Event": event,
    }
    secret = webhook.get("secret", "")
    if secret:
        sig = _hm.new(secret.encode(), payload.encode(), _hl.sha256).hexdigest()
        headers["X-HydraHive-Signature"] = f"sha256={sig}"

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(webhook["url"], content=payload, headers=headers)
            logger.info("Webhook %s -> %s: HTTP %d", event, webhook["url"], resp.status_code)
    except Exception as e:
        logger.warning("Webhook fehlgeschlagen (%s -> %s): %s", event, webhook["url"], e)


async def fire_project_webhooks(projects_dir: str, project_id: str, event: str, data: dict, *, logger) -> None:
    webhooks = _load_webhooks(projects_dir, project_id)
    tasks = [_fire_webhook(wh, event, data, logger=logger) for wh in webhooks if event in wh.get("events", [])]
    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)


def register_project_integration_routes(
    auth_router: APIRouter,
    admin_router: APIRouter,
    public_router: APIRouter,
    *,
    require_auth,
    require_admin,
    projects,
    projects_dir: str,
    discovery,
    orchestrator,
    audit_log,
    logger,
    run_self_update,
) -> None:
    def _check_project_access(project_id: str, auth: tuple[str, str]) -> None:
        """#279: Prüft ob der User Zugriff auf das Projekt hat."""
        username, role = auth
        if role == "admin":
            return
        cfg = projects.get(project_id)
        if not cfg:
            raise HTTPException(404, "Projekt nicht gefunden")
        # Personal-Projekt gehört dem User
        if project_id == f"personal_{username}":
            return
        # Für andere Projekte: Owner-Check (owner-Feld oder project_id muss in allowed_projects sein)
        owner = getattr(cfg, "owner", None) or ""
        if owner == username:
            return
        raise HTTPException(403, f"Keine Berechtigung für Projekt '{project_id}'")

    @auth_router.get("/projects/{project_id}/agentlink")
    def list_agentlink(project_id: str, _a: tuple[str, str] = Depends(require_auth)):
        from .agentlink import list_handoffs as _lh

        _check_project_access(project_id, _a)
        project_dir = Path(projects_dir) / project_id
        if not project_dir.exists():
            raise HTTPException(404, "Projekt nicht gefunden")
        handoffs = _lh(project_dir)
        return {"project_id": project_id, "handoffs": handoffs, "count": len(handoffs)}

    @auth_router.delete("/projects/{project_id}/agentlink/{handoff_id}")
    def delete_agentlink(project_id: str, handoff_id: str, _a: tuple[str, str] = Depends(require_auth)):
        from .agentlink import delete_handoff as _dh

        _check_project_access(project_id, _a)
        project_dir = Path(projects_dir) / project_id
        if not project_dir.exists():
            raise HTTPException(404, "Projekt nicht gefunden")
        deleted = _dh(project_dir, handoff_id)
        if not deleted:
            raise HTTPException(404, f"Handoff '{handoff_id}' nicht gefunden")
        return {"deleted": True, "handoff_id": handoff_id}

    @auth_router.post("/projects/{project_id}/agentlink")
    def create_agentlink(project_id: str, req: AgentLinkCreateRequest, _a: tuple[str, str] = Depends(require_auth)):
        from .agentlink import write_handoff as _wh

        _check_project_access(project_id, _a)
        project_dir = Path(projects_dir) / project_id
        if not project_dir.exists():
            raise HTTPException(404, "Projekt nicht gefunden")
        return _wh(
            project_dir,
            from_agent=req.from_agent,
            to_agent=req.to_agent,
            context=req.context,
            data=req.data,
            ttl_seconds=req.ttl_seconds,
        )

    @admin_router.get("/projects/{project_id}/webhooks")
    def list_webhooks(project_id: str, _a: tuple = Depends(require_admin)):
        if not projects.get(project_id):
            raise HTTPException(404, "Projekt nicht gefunden")
        webhooks = _load_webhooks(projects_dir, project_id)
        masked = [{**w, "secret": "***" if w.get("secret") else ""} for w in webhooks]
        return {"project_id": project_id, "webhooks": masked}

    @admin_router.post("/projects/{project_id}/webhooks", status_code=201)
    def create_webhook(project_id: str, req: WebhookRequest, _a: tuple = Depends(require_admin)):
        import secrets as _sec
        import time as _time

        if not projects.get(project_id):
            raise HTTPException(404, "Projekt nicht gefunden")
        invalid = [e for e in req.events if e not in VALID_EVENTS]
        if invalid:
            raise HTTPException(400, f"Unbekannte Events: {invalid}. Gueltig: {sorted(VALID_EVENTS)}")

        webhooks = _load_webhooks(projects_dir, project_id)
        wh = {
            "id": _sec.token_hex(8),
            "name": req.name,
            "url": req.url,
            "secret": req.secret,
            "events": req.events,
            "created_at": _time.time(),
        }
        webhooks.append(wh)
        _save_webhooks(projects_dir, project_id, webhooks)
        logger.info("Webhook angelegt: %s -> %s (%s)", project_id, req.url, req.events)
        audit_log("webhook.create", target=req.url, project_id=project_id, details={"events": req.events})
        return {**wh, "secret": "***" if wh["secret"] else ""}

    @admin_router.delete("/projects/{project_id}/webhooks/{webhook_id}")
    def delete_webhook(project_id: str, webhook_id: str, _a: tuple = Depends(require_admin)):
        if not projects.get(project_id):
            raise HTTPException(404, "Projekt nicht gefunden")
        webhooks = _load_webhooks(projects_dir, project_id)
        updated = [w for w in webhooks if w["id"] != webhook_id]
        if len(updated) == len(webhooks):
            raise HTTPException(404, f"Webhook '{webhook_id}' nicht gefunden")
        _save_webhooks(projects_dir, project_id, updated)
        return {"deleted": True, "webhook_id": webhook_id}

    @admin_router.post("/projects/{project_id}/webhooks/test")
    async def test_webhook(project_id: str, req: WebhookTestRequest, _a: tuple = Depends(require_admin)):
        url = req.url
        secret = req.secret
        if not url:
            raise HTTPException(400, "url fehlt")
        await _fire_webhook({"url": url, "secret": secret}, "ping", {"project_id": project_id, "message": "HydraHive Webhook Test"}, logger=logger)
        return {"sent": True, "url": url}

    @public_router.post("/hooks/{project_id}/wake")
    async def webhook_wake(project_id: str, request: Request):
        cfg = projects.get(project_id)
        if not cfg:
            raise HTTPException(404, "Projekt nicht gefunden")

        webhooks = _load_webhooks(projects_dir, project_id)
        wake_hooks = [w for w in webhooks if "agent_start" in w.get("events", [])]
        body_bytes = await request.body()
        sig_header = request.headers.get("X-HydraHive-Signature", "")

        if wake_hooks:
            # Wenn Wake-Hooks konfiguriert sind, ist Signatur verpflichtend
            hooks_with_secret = [w for w in wake_hooks if w.get("secret", "")]
            if hooks_with_secret:
                if not sig_header:
                    raise HTTPException(401, "Signatur erforderlich (X-HydraHive-Signature fehlt)")
                import hashlib as _hl
                import hmac as _hm
                valid = False
                for wh in hooks_with_secret:
                    secret = wh.get("secret", "")
                    expected = "sha256=" + _hm.new(secret.encode(), body_bytes, _hl.sha256).hexdigest()
                    if _hm.compare_digest(sig_header, expected):
                        valid = True
                        break
                if not valid:
                    raise HTTPException(401, "Ungueltige Signatur")
        else:
            # Keine Hooks konfiguriert → Wake-Endpoint gesperrt
            raise HTTPException(403, "Wake-Endpoint nicht konfiguriert")

        try:
            data = json.loads(body_bytes) if body_bytes else {}
        except Exception:
            data = {}

        message = data.get("message", "Wake-up call")
        sender = data.get("sender", "webhook")

        # v2 (#600): Kein Boss-Discovery-Check mehr — v2-Projekt ist sein eigener Agent
        # Bei v1-Legacy-Projekten (agents.boss gesetzt) weiterhin Discovery pruefen
        is_v2 = getattr(cfg, "is_v2", False) or not getattr(cfg.agents, "boss", "")
        if not is_v2:
            boss_id = cfg.agents.boss
            if not discovery.get(boss_id):
                raise HTTPException(503, f"Boss-Agent '{boss_id}' nicht verfuegbar")

        asyncio.create_task(
            orchestrator.handle_message(project_id, cfg, message, sender),
            name=f"webhook-wake-{project_id}",
        )
        return {"triggered": True, "project_id": project_id, "message": message}

    @public_router.post("/webhooks/gitea/{project_id}")
    async def gitea_webhook(project_id: str, request: Request):
        import hmac
        import hashlib

        body = await request.body()

        from .gitea import _load_config as _gitea_cfg
        cfg = _gitea_cfg()
        webhook_secret = cfg.get("webhook_secret", "")
        if webhook_secret:
            sig = request.headers.get("X-Gitea-Signature", "")
            expected = hmac.new(webhook_secret.encode(), body, hashlib.sha256).hexdigest()  # type: ignore[attr-defined]
            if not hmac.compare_digest(sig, expected):
                raise HTTPException(403, "Webhook-Signatur ungültig")

        try:
            payload = await request.json()
        except Exception:
            payload = {}

        ref     = payload.get("ref", "")
        pusher  = payload.get("pusher", {}).get("login", "unknown")
        commits = len(payload.get("commits", []))

        logger.info("Gitea Webhook: project=%s ref=%s pusher=%s commits=%d",
                    project_id, ref, pusher, commits)

        if ref != "refs/heads/main":
            return {"status": "ignored", "reason": "not main branch", "ref": ref}

        audit_log("gitea.webhook.push", target=project_id, project_id=project_id,
                  details={"ref": ref, "pusher": pusher, "commits": commits})

        if project_id == "hydrahive-core":
            asyncio.create_task(run_self_update(pusher, commits))
            return {"status": "deploying", "project": project_id, "ref": ref}

        return {"status": "ok", "project": project_id, "ref": ref}
