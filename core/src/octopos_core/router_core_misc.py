from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class SetupRequest(BaseModel):
    username: str
    password: str


class AgentLlmPatchRequest(BaseModel):
    fallback_models: list[str]


class IncomingMessage(BaseModel):
    content: str
    sender: str = "user"
    execution_mode: Literal["safe", "elevated", "root"] | None = None


def register_core_misc_routes(
    public_router: APIRouter,
    auth_router: APIRouter,
    admin_router: APIRouter,
    *,
    require_auth,
    setup_lock: asyncio.Lock,
    load_users,
    save_users,
    read_server_name,
    matrix_register,
    hash_password,
    verify_password,
    make_jwt,
    read_admin_password,
    check_login_rate,
    discovery,
    runtime,
    read_audit_logs,
    logger: logging.Logger,
) -> type[IncomingMessage]:
    @public_router.get("/setup/status")
    def setup_status():
        users = load_users()
        return {"needs_setup": len(users) == 0}

    @public_router.post("/setup", status_code=201)
    async def run_setup(req: SetupRequest):
        import re as _re

        async with setup_lock:
            users = load_users()
            if users:
                raise HTTPException(403, "Setup bereits abgeschlossen")
            if not _re.match(r"^[a-z0-9_.-]+$", req.username):
                raise HTTPException(400, "Username darf nur a-z, 0-9, _ . - enthalten")
            if len(req.password) < 8:
                raise HTTPException(400, "Passwort muss mindestens 8 Zeichen haben")

            server_name = read_server_name()
            matrix_ok = await matrix_register(req.username, req.password, server_name)
            users[req.username] = {
                "password_hash": hash_password(req.password),
                "role": "admin",
                "matrix_id": f"@{req.username}:{server_name}",
                "matrix_ok": matrix_ok,
                "created_at": datetime.now().isoformat(),
            }
            save_users(users)
            logger.info("Setup abgeschlossen: erster Admin-User '%s' angelegt", req.username)
            return {"created": True, "username": req.username, "role": "admin"}

    @public_router.post("/auth/login")
    def login(req: LoginRequest, request: Request):
        check_login_rate(request.client.host if request.client else "unknown")
        users = load_users()
        if users:
            user = users.get(req.username)
            if user and verify_password(req.password, user.get("password_hash", "")):
                role = user.get("role", "user")
                token = make_jwt(req.username, role)
                logger.info("Login erfolgreich (users.json): %s", req.username)
                return {"access_token": token, "token_type": "bearer", "role": role, "username": req.username}
            raise HTTPException(401, "Ungültige Zugangsdaten")

        admin_pass = read_admin_password()
        if not admin_pass:
            raise HTTPException(503, "Kein Admin-Passwort konfiguriert — Setup erforderlich")
        if req.username != "admin" or req.password != admin_pass:
            raise HTTPException(401, "Ungültige Zugangsdaten")
        token = make_jwt(req.username, "admin")
        logger.info("Login erfolgreich (admin_credentials): %s", req.username)
        return {"access_token": token, "token_type": "bearer", "role": "admin", "username": req.username}

    @auth_router.get("/auth/me")
    def whoami(auth: tuple[str, str] = Depends(require_auth)):
        username, role = auth
        return {"username": username, "role": role}

    @public_router.get("/health")
    def health():
        return {"status": "ok", "service": "octopos-core"}

    @auth_router.get("/agents")
    def list_agents():
        registered = discovery.agents
        running = runtime.status_all()
        return {
            agent_id: {
                "config": {
                    "type": cfg.type,
                    "identity": cfg.identity,
                    "model": cfg.llm.model,
                    "fallback_models": cfg.llm.fallback_models,
                },
                "runtime": running.get(agent_id),
            }
            for agent_id, cfg in registered.items()
        }

    @auth_router.get("/agents/{agent_id}")
    def get_agent(agent_id: str):
        cfg = discovery.get(agent_id)
        if not cfg:
            raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")
        return {
            "config": cfg.model_dump(exclude={"agent_dir"}),
            "runtime": runtime.status_all().get(agent_id),
        }

    @admin_router.patch("/agents/{agent_id}/llm")
    def patch_agent_llm(agent_id: str, req: AgentLlmPatchRequest):
        cfg = discovery.get(agent_id)
        if not cfg or not cfg.agent_dir:
            raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")
        yaml_path = cfg.agent_dir / "agent.yaml"
        try:
            import yaml as _yaml

            raw = _yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            if "llm" not in raw:
                raw["llm"] = {}
            if req.fallback_models:
                raw["llm"]["fallback_models"] = req.fallback_models
            else:
                raw["llm"].pop("fallback_models", None)
            yaml_path.write_text(_yaml.dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
        except Exception as e:
            raise HTTPException(500, f"Fehler beim Speichern: {e}")
        return {"ok": True, "agent_id": agent_id, "fallback_models": req.fallback_models}

    @admin_router.get("/logs/core")
    def get_core_logs(lines: int = 200):
        import subprocess as _sub

        lines = max(10, min(lines, 2000))
        try:
            result = _sub.run(
                ["journalctl", "-u", "octopos-core", "-n", str(lines), "--no-pager", "--output=short-iso"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            log_lines = result.stdout.splitlines()
            return {"lines": log_lines, "count": len(log_lines)}
        except Exception as e:
            return {"lines": [str(e)], "count": 1}

    @admin_router.get("/audit/logs")
    def get_audit_logs(limit: int = 100, project_id: str = "", user: str = "", action: str = ""):
        limit = max(10, min(limit, 1000))
        logs = read_audit_logs(limit, project_id, user, action)
        return {"logs": logs, "count": len(logs)}

    @auth_router.get("/tools")
    def list_tools():
        from .tool_registry import registry

        result = {}
        for tool_id in registry.all_ids():
            tool = registry.get(tool_id)
            if tool:
                result[tool_id] = {
                    "name": tool.name,
                    "description": tool.description,
                    "permissions_required": tool.permissions_required,
                    "parameters": tool.parameters,
                }
        return result

    return IncomingMessage
