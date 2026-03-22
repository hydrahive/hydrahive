from __future__ import annotations

from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from .execution_mode_policy import resolve_request_execution_mode


def default_personal_agent_execution_modes() -> dict:
    return {
        "default": "safe",
        "safe": {
            "permissions": [
                "filesystem.read",
                "system.read",
                "memory.read",
                "memory.write",
                "handoff.read",
                "handoff.write",
                "agents.ask",
                "agents.delegate",
                "git.read",
                "workstation.read",
                "discord",
            ],
        },
        "elevated": {
            "permissions": [
                "filesystem.read",
                "filesystem.write",
                "system.read",
                "system.write",
                "memory.read",
                "memory.write",
                "handoff.read",
                "handoff.write",
                "agents.ask",
                "agents.delegate",
                "git.read",
                "git.write",
                "workstation.read",
                "workstation.write",
                "discord",
            ],
        },
        "root": {
            "permissions": [
                "filesystem.read",
                "filesystem.write",
                "system.read",
                "system.write",
                "memory.read",
                "memory.write",
                "handoff.read",
                "handoff.write",
                "agents.ask",
                "agents.delegate",
                "git.read",
                "git.write",
                "git.push",
                "git.pr",
                "shell.exec",
                "workstation.read",
                "workstation.write",
                "workstation.shell",
                "discord",
            ],
        },
    }


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"


class MyAgentUpdateRequest(BaseModel):
    identity: str
    soul: str = ""
    model: str
    temperature: float = 0.7
    max_tokens: int = 4096
    fallback_models: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    allowed_agents: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    ollama_base_url: str | None = None


def build_personal_agent_llm_data(req: MyAgentUpdateRequest) -> dict:
    llm_data: dict = {
        "model": req.model,
        "temperature": req.temperature,
        "max_tokens": req.max_tokens,
        "fallback_models": list(req.fallback_models),
    }
    if req.ollama_base_url:
        llm_data["ollama_base_url"] = req.ollama_base_url
    return llm_data


def build_personal_agent_data(agent_id: str, req: MyAgentUpdateRequest) -> dict:
    agent_data: dict = {
        "id": agent_id,
        "type": "specialist",
        "identity": req.identity,
        "llm": build_personal_agent_llm_data(req),
        "soul": "./soul.md",
        "tools": list(req.tools),
        "allowed_agents": list(req.allowed_agents),
        "mcp_servers": list(req.mcp_servers),
        "execution_modes": default_personal_agent_execution_modes(),
        "heartbeat": {"interval": "60s", "timeout": "180s", "on_failure": "ignore"},
    }
    return agent_data


def persist_personal_agent_config(
    agent_dir: Path,
    agent_id: str,
    req: MyAgentUpdateRequest,
    *,
    load_agent_config_direct: Callable[[Path], object] | None = None,
) -> dict:
    import yaml as _yaml

    agent_data = build_personal_agent_data(agent_id, req)
    (agent_dir / "agent.yaml").write_text(
        _yaml.dump(agent_data, allow_unicode=True, default_flow_style=False), encoding="utf-8"
    )
    if req.soul is not None:
        (agent_dir / "soul.md").write_text(req.soul, encoding="utf-8")
    if load_agent_config_direct is not None:
        load_agent_config_direct(agent_dir)
    return agent_data


def register_user_routes(
    auth_router: APIRouter,
    admin_router: APIRouter,
    *,
    require_auth,
    require_admin,
    load_users,
    save_users,
    read_server_name,
    matrix_register,
    hash_password,
    agents_dir: str,
    ensure_personal_agent,
    runtime,
    agent_sessions,
    agent_orchestrator,
    audit_log,
    logger,
    incoming_message_model,
    load_agent_config_direct=None,
) -> None:
    @admin_router.get("/users")
    def list_users():
        users = load_users()
        return {
            username: {
                "username": username,
                "role": data.get("role", "user"),
                "matrix_id": f"@{username}:{read_server_name()}",
                "created_at": data.get("created_at", ""),
            }
            for username, data in users.items()
        }

    @admin_router.post("/users", status_code=201)
    async def create_user(req: CreateUserRequest):
        import re as _re
        from datetime import datetime as _dt

        if not _re.match(r"^[a-z0-9_.-]+$", req.username):
            raise HTTPException(400, "Username darf nur a-z, 0-9, _ . - enthalten")
        if len(req.password) < 8:
            raise HTTPException(400, "Passwort muss mindestens 8 Zeichen haben")

        users = load_users()
        if req.username in users:
            raise HTTPException(409, f"User '{req.username}' existiert bereits")

        server_name = read_server_name()
        matrix_ok = await matrix_register(req.username, req.password, server_name)
        users[req.username] = {
            "password_hash": hash_password(req.password),
            "role": req.role,
            "matrix_id": f"@{req.username}:{server_name}",
            "matrix_ok": matrix_ok,
            "created_at": _dt.now().isoformat(),
        }
        save_users(users)
        logger.info("User angelegt: %s (role=%s, matrix=%s)", req.username, req.role, matrix_ok)
        audit_log("user.create", target=req.username, details={"role": req.role})
        return {
            "created": True,
            "username": req.username,
            "matrix_id": f"@{req.username}:{server_name}",
            "matrix_ok": matrix_ok,
        }

    @admin_router.delete("/users/{username}")
    async def delete_user(username: str):
        users = load_users()
        if username not in users:
            raise HTTPException(404, f"User '{username}' nicht gefunden")
        if username == "admin":
            raise HTTPException(403, "Admin-User kann nicht gelöscht werden")
        del users[username]
        save_users(users)

        personal_id = f"personal_{username}"
        personal_dir = Path(agents_dir) / personal_id
        if personal_dir.exists():
            disabled = Path(agents_dir) / f"_{personal_id}_disabled"
            personal_dir.rename(disabled)
            logger.info("Persönlicher Agent deaktiviert: %s", personal_id)

        logger.info("User gelöscht: %s", username)
        audit_log("user.delete", target=username)
        return {"deleted": True, "username": username}

    @admin_router.put("/users/{username}/password")
    async def change_user_password(username: str, body: dict):
        new_password = body.get("password", "").strip()
        if len(new_password) < 8:
            raise HTTPException(400, "Passwort muss mindestens 8 Zeichen haben")
        users = load_users()
        if username not in users:
            raise HTTPException(404, f"User '{username}' nicht gefunden")
        users[username]["password_hash"] = hash_password(new_password)
        save_users(users)
        return {"updated": True, "username": username}

    @auth_router.get("/me/agent")
    def get_my_agent(auth: tuple[str, str] = Depends(require_auth)):
        username, _role = auth
        agent_id, cfg = ensure_personal_agent(username)
        if cfg is None:
            raise HTTPException(500, "Persönlicher Agent konnte nicht erstellt werden")
        return {
            "agent_id": agent_id,
            "config": cfg.model_dump(exclude={"agent_dir"}),
            "runtime": runtime.status_all().get(agent_id),
        }

    @auth_router.post("/me/agent/message/stream")
    async def my_agent_message_stream(
        body: dict = Body(...),
        auth: tuple[str, str] = Depends(require_auth),
    ):
        from fastapi.responses import StreamingResponse as _SR
        from .project_config import ProjectAgents as _PA
        from .project_config import ProjectConfig as _PC
        from .project_config import ProjectIdentity as _PI

        req = incoming_message_model.model_validate(body)
        username, _role = auth
        agent_id, cfg = ensure_personal_agent(username)
        if cfg is None:
            raise HTTPException(503, "Persönlicher Agent nicht verfügbar")
        execution_mode = resolve_request_execution_mode(
            auth,
            req.execution_mode,
            audit_log=audit_log,
            audit_target=agent_id,
            audit_source="me.agent.message.stream",
        )

        virtual_cfg = _PC(
            id=agent_id,
            identity=_PI(name=cfg.identity),
            agents=_PA(boss=agent_id, workers=[]),
        )

        async def event_stream():
            async for chunk in agent_orchestrator.handle_message_stream(
                project_id=agent_id,
                project_cfg=virtual_cfg,
                content=req.content,
                sender=req.sender,
                execution_mode=execution_mode,
            ):
                yield chunk

        return _SR(event_stream(), media_type="text/event-stream",
                   headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @auth_router.get("/me/agent/session/history")
    def my_agent_session_history(
        limit: int = 50,
        auth: tuple[str, str] = Depends(require_auth),
    ):
        username, _role = auth
        agent_id = f"personal_{username}"
        context = agent_sessions.get_context(agent_id, max_messages=limit)
        session = agent_sessions.get_active(agent_id)
        return {
            "session_id": session.id if session else None,
            "messages": context,
            "count": len(context),
        }

    @auth_router.delete("/me/agent/session")
    def my_agent_session_clear(auth: tuple[str, str] = Depends(require_auth)):
        username, _role = auth
        agent_sessions.end_session(f"personal_{username}")
        return {"cleared": True}

    @auth_router.put("/me/agent")
    async def update_my_agent(
        req: MyAgentUpdateRequest,
        auth: tuple[str, str] = Depends(require_auth),
    ):
        username, _role = auth
        agent_id, _cfg = ensure_personal_agent(username)
        agent_dir = Path(agents_dir) / agent_id

        persist_personal_agent_config(
            agent_dir,
            agent_id,
            req,
            load_agent_config_direct=load_agent_config_direct,
        )

        logger.info("Persönlicher Agent konfiguriert: %s", agent_id)
        return {"updated": True, "agent_id": agent_id}
