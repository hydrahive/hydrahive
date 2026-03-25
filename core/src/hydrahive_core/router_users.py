from __future__ import annotations

from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from .execution_mode_policy import resolve_request_execution_mode


def default_personal_agent_execution_modes() -> dict:
    return {
        "default": "elevated",
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
                "git.issue",
                "workstation.read",
                "discord",
                "mail",
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
                "git.issue",
                "git.write",
                "workstation.read",
                "workstation.write",
                "workstation.shell",
                "discord",
                "mail",
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
                "git.issue",
                "git.write",
                "git.push",
                "git.pr",
                "shell.exec",
                "workstation.read",
                "workstation.write",
                "workstation.shell",
                "discord",
                "mail",
            ],
        },
    }


def upgrade_personal_agent_data(agent_data: dict, agent_dir: Path | None = None) -> tuple[dict, bool]:
    changed = False
    defaults = default_personal_agent_execution_modes()
    execution_modes = agent_data.setdefault("execution_modes", {})
    if execution_modes.get("default") != defaults["default"]:
        execution_modes["default"] = defaults["default"]
        changed = True

    for mode_name in ("safe", "elevated", "root"):
        profile = execution_modes.setdefault(mode_name, {})
        permissions = list(profile.get("permissions") or [])
        for permission in defaults[mode_name]["permissions"]:
            if permission not in permissions:
                permissions.append(permission)
                changed = True
        profile["permissions"] = permissions

    tools = list(agent_data.get("tools") or [])
    has_gitea_tools = any(tool.startswith("gitea_repo_") for tool in tools)
    if has_gitea_tools and "gitea_create_issue" not in tools:
        tools.append("gitea_create_issue")
        agent_data["tools"] = tools
        changed = True

    if agent_dir is not None and (agent_dir / "mail.json").exists():
        for tool_id in ("send_mail", "receive_mail"):
            if tool_id not in tools:
                tools.append(tool_id)
                agent_data["tools"] = tools
                changed = True

    return agent_data, changed


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"


class UpdateUserRequest(BaseModel):
    role: str | None = None
    allowed_projects: list[str] | None = None
    allowed_agents: list[str] | None = None
    datasources: list[str] | None = None
    wks_ip: str | None = None
    discord_user_id: str | None = None


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
                "allowed_projects": data.get("allowed_projects", []),
                "allowed_agents": data.get("allowed_agents", []),
                "datasources": data.get("datasources", []),
                "wks_ip": (data.get("wks") or {}).get("ip", ""),
                "discord_user_id": data.get("discord_user_id", ""),
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

    @admin_router.put("/users/{username}")
    async def update_user(username: str, req: UpdateUserRequest):
        users = load_users()
        if username not in users:
            raise HTTPException(404, f"User '{username}' nicht gefunden")
        if req.role is not None:
            if req.role not in {"user", "admin"}:
                raise HTTPException(400, "Rolle muss 'user' oder 'admin' sein")
            if username == "admin" and req.role != "admin":
                raise HTTPException(403, "Admin-User kann nicht auf 'user' gesetzt werden")
            users[username]["role"] = req.role
        if req.allowed_projects is not None:
            users[username]["allowed_projects"] = req.allowed_projects
        if req.allowed_agents is not None:
            users[username]["allowed_agents"] = req.allowed_agents
        if req.datasources is not None:
            users[username]["datasources"] = req.datasources
        if req.wks_ip is not None:
            wks = users[username].get("wks") or {}
            if req.wks_ip:
                wks["ip"] = req.wks_ip
                users[username]["wks"] = wks
            else:
                users[username].pop("wks", None)
        if req.discord_user_id is not None:
            if req.discord_user_id.strip():
                users[username]["discord_user_id"] = req.discord_user_id.strip()
            else:
                users[username].pop("discord_user_id", None)
        save_users(users)
        audit_log("user.update", target=username, details={"fields": [k for k, v in req.model_dump().items() if v is not None]})
        return {"updated": True, "username": username}

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
            personal_agent=True,
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
    async def my_agent_session_clear(auth: tuple[str, str] = Depends(require_auth)):
        from .router_agent_chat import _save_session_transcript
        username, _role = auth
        agent_id = f"personal_{username}"
        context = agent_sessions.get_context(agent_id, max_messages=200)
        _save_session_transcript(Path(agents_dir) / agent_id, context, agent_id)
        await agent_sessions.end_session(agent_id)
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

    @auth_router.patch("/me/agent/heartbeat")
    async def patch_my_agent_heartbeat(
        body: dict = Body(...),
        auth: tuple[str, str] = Depends(require_auth),
    ):
        import yaml as _yaml

        username, _role = auth
        agent_id, _cfg = ensure_personal_agent(username)
        agent_dir = Path(agents_dir) / agent_id
        yaml_path = agent_dir / "agent.yaml"
        try:
            raw = _yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            if "heartbeat" in body:
                raw["heartbeat"] = body["heartbeat"]
            if "heartbeat_tasks" in body:
                raw["heartbeat_tasks"] = body["heartbeat_tasks"]
            yaml_path.write_text(
                _yaml.dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
            )
        except Exception as e:
            raise HTTPException(500, f"Fehler beim Speichern: {e}")
        load_agent_config_direct(agent_dir)
        logger.info("Heartbeat konfiguriert: %s", agent_id)
        return {"updated": True, "agent_id": agent_id}
