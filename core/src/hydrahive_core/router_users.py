from __future__ import annotations

import asyncio as _asyncio
from pathlib import Path
from typing import Callable, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, UploadFile, File
from pydantic import BaseModel, Field

from .execution_mode_policy import resolve_request_execution_mode


ADMIN_TOOLS = ["create_agent", "delete_agent", "create_project", "delete_project"]


def default_personal_agent_execution_modes() -> dict:
    """Minimal execution_modes-Shape für Personal-Agents (#644).

    Seit #638 ist `ExecutionModeProfile.permissions` entfernt; Listen
    wurden beim Loader stumm verworfen. Hier bleibt nur noch der
    autoritativ genutzte `default`-Key übrig.
    """
    return {"default": "elevated"}


def upgrade_personal_agent_data(agent_data: dict, agent_dir: Path | None = None, *, is_admin: bool = False) -> tuple[dict, bool]:
    # #492: Agents mit role brauchen kein Legacy-Upgrade
    if agent_data.get("role"):
        return agent_data, False
    changed = False
    defaults = default_personal_agent_execution_modes()
    execution_modes = agent_data.setdefault("execution_modes", {})
    # Default NICHT überschreiben wenn Admin ihn auf root/unrestricted gesetzt hat
    current_default = execution_modes.get("default", "")
    if current_default not in ("root", "unrestricted") and current_default != defaults["default"]:
        execution_modes["default"] = defaults["default"]
        changed = True

    # #644: Tote permissions-Listen aus Legacy-YAMLs einmalig strippen.
    # ExecutionModeProfile.permissions ist seit #638 weg — Listen wurden beim
    # Loader stumm verworfen, bleiben aber in alten YAMLs stehen.
    for mode_name in ("safe", "elevated", "root", "unrestricted"):
        profile = execution_modes.get(mode_name)
        if isinstance(profile, dict) and "permissions" in profile:
            profile.pop("permissions", None)
            changed = True

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

    # Admin-Tools automatisch für Admin-Personal-Agenten
    if is_admin:
        for tool_id in ADMIN_TOOLS:
            if tool_id not in tools:
                tools.append(tool_id)
                agent_data["tools"] = tools
                changed = True

    return agent_data, changed


class CreateUserRequest(BaseModel):
    username: str
    password: str
    role: str = "user"
    group: str = "standard"
    allowed_projects: list[str] = []


class UpdateUserRequest(BaseModel):
    role: str | None = None
    group: str | None = None
    allowed_projects: list[str] | None = None
    datasources: list[str] | None = None
    wks_ip: str | None = None
    discord_user_id: str | None = None


class ChangePasswordRequest(BaseModel):
    password: str


class MyAgentUpdateRequest(BaseModel):
    identity: str
    soul: str = ""
    model: str
    temperature: float = 0.7
    max_tokens: int = 4096
    fallback_models: list[str] = Field(default_factory=list)
    tools: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    ollama_base_url: str | None = None
    risk_policy: Literal["interactive", "trusted"] = "interactive"


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
        "mcp_servers": list(req.mcp_servers),
        "heartbeat": {"interval": "60s", "timeout": "180s", "on_failure": "ignore"},
    }
    agent_data["tools"] = list(req.tools)
    agent_data["execution_modes"] = default_personal_agent_execution_modes()
    if req.risk_policy != "interactive":
        agent_data["risk_policy"] = req.risk_policy
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
    discovery=None,
    projects_dir: str = "/projects",
    get_provisioner=None,
    projects=None,
) -> dict:
    @admin_router.get("/users")
    def list_users():
        users = load_users()
        return {
            username: {
                "username": username,
                "role": data.get("role", "user"),
                "group": data.get("group", "standard"),
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
        matrix_ok = False
        try:
            provisioner = get_provisioner() if get_provisioner else None
            if provisioner:
                matrix_ok = await provisioner.register_matrix_user(req.username, req.password)
            else:
                logger.warning("Matrix-Registrierung für '%s' übersprungen: kein Provisioner", req.username)
        except Exception as _me:
            logger.warning("Matrix-Registrierung '%s' Exception: %s", req.username, _me)
        users[req.username] = {
            "password_hash": hash_password(req.password),
            "role": req.role,
            "group": req.group,
            "matrix_id": f"@{req.username}:{server_name}",
            "matrix_ok": matrix_ok,
            "created_at": _dt.now().isoformat(),
            "allowed_projects": req.allowed_projects,
        }
        save_users(users)

        # Alten Personal-Agent-Dir entfernen falls vorhanden (z.B. nach Neu-Anlage)
        import shutil as _shutil
        personal_dir = Path(agents_dir) / f"personal_{req.username}"
        disabled_dir = Path(agents_dir) / f"_personal_{req.username}_disabled"
        if personal_dir.exists():
            _shutil.rmtree(personal_dir)
            logger.info("Alter Personal-Agent-Dir entfernt: %s", personal_dir)
        if disabled_dir.exists():
            _shutil.rmtree(disabled_dir)
            logger.info("Deaktivierter Personal-Agent-Dir entfernt: %s", disabled_dir)

        # Persönlichen Agenten + Projekt anlegen
        ensure_personal_agent(req.username)

        # Persönliches Projekt mit Matrix-Room provisionieren
        personal_id = f"personal_{req.username}"
        room_warn = None
        if get_provisioner and projects:
            provisioner = get_provisioner()
            if provisioner:
                await _asyncio.sleep(0.3)  # kurz warten bis Discovery den neuen Agenten kennt
                cfg = projects.get(personal_id)
                if cfg and not cfg.matrix.room:
                    try:
                        result = await provisioner.provision(cfg)
                        if result.matrix_room:
                            from .router_project_lifecycle import update_project_matrix_room, update_project_matrix_space
                            update_project_matrix_room(projects_dir, personal_id, result.matrix_room, logger=logger)
                            logger.info("Persönlicher Matrix-Room für %s: %s", req.username, result.matrix_room)
                        if result.matrix_space:
                            update_project_matrix_space(projects_dir, personal_id, result.matrix_space, logger=logger)
                            logger.info("Persönlicher Matrix-Space für %s: %s", req.username, result.matrix_space)
                        if result.warnings:
                            room_warn = "; ".join(result.warnings)
                    except Exception as _e:
                        room_warn = str(_e)
                        logger.warning("Matrix-Provisioning für %s fehlgeschlagen: %s", req.username, _e)

        logger.info("User angelegt: %s (role=%s, matrix=%s)", req.username, req.role, matrix_ok)
        audit_log("user.create", target=req.username, details={"role": req.role})
        return {
            "created": True,
            "username": req.username,
            "matrix_id": f"@{req.username}:{server_name}",
            "matrix_ok": matrix_ok,
            "matrix_room_warn": room_warn,
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

        import shutil as _shutil
        personal_id = f"personal_{username}"

        # Persönlichen Matrix-Room deprovisionieren BEVOR Dirs gelöscht werden
        deprov_warnings = []
        if get_provisioner and projects:
            cfg = projects.get(personal_id)
            if cfg:
                try:
                    _prov = get_provisioner()
                    if _prov:
                        deprov_warnings = await _prov.deprovision(cfg)
                        if deprov_warnings:
                            logger.warning("Deprovision-Warnungen für %s: %s", personal_id, deprov_warnings)
                except Exception as _e:
                    logger.warning("Deprovision für %s fehlgeschlagen: %s", personal_id, _e)

        # Agent-Verzeichnis + deaktivierte Kopie löschen
        personal_dir = Path(agents_dir) / personal_id
        disabled_dir = Path(agents_dir) / f"_{personal_id}_disabled"
        for d in (personal_dir, disabled_dir):
            if d.exists():
                _shutil.rmtree(d)
                logger.info("Personal-Agent-Dir gelöscht: %s", d)
        # Projekt-Verzeichnis löschen
        project_dir = Path(projects_dir) / personal_id
        if project_dir.exists():
            _shutil.rmtree(project_dir)
            logger.info("Personal-Projekt-Dir gelöscht: %s", project_dir)

        # Session aus dem In-Memory-State des SessionManagers entfernen
        await agent_sessions.end_session(personal_id)

        # SQLite Sessions für den Agent löschen
        try:
            db = agent_sessions._init_db()
            db.execute("DELETE FROM sessions WHERE project_id = ?", (personal_id,))
            db.execute("DELETE FROM messages WHERE session_id IN (SELECT id FROM sessions WHERE project_id = ?)", (personal_id,))
            db.commit()
        except Exception as _e:
            logger.warning("Session-DB-Bereinigung für %s fehlgeschlagen: %s", personal_id, _e)

        logger.info("User gelöscht (GDPR): %s", username)
        audit_log("user.delete", target=username)
        return {"deleted": True, "username": username}

    # ── GDPR Data Export (#400) ─────────────────────────────────────────────

    @admin_router.get("/users/{username}/export")
    async def export_user_data(username: str):
        """GDPR Art. 20: Vollständiger Daten-Export eines Users als JSON."""
        users = load_users()
        if username not in users:
            raise HTTPException(404, f"User '{username}' nicht gefunden")

        import json as _json
        personal_id = f"personal_{username}"
        export: dict = {"username": username, "role": users[username].get("role"), "group": users[username].get("group")}

        # Agent-Config
        agent_dir = Path(agents_dir) / personal_id
        agent_yaml = agent_dir / "agent.yaml"
        if agent_yaml.exists():
            import yaml as _yaml
            export["agent_config"] = _yaml.safe_load(agent_yaml.read_text(encoding="utf-8"))

        # Memory
        memory_dir = agent_dir / "memory"
        if memory_dir.exists():
            export["memory"] = {}
            for f in memory_dir.iterdir():
                if f.is_file():
                    try:
                        export["memory"][f.name] = f.read_text(encoding="utf-8")
                    except Exception:
                        pass

        # Sessions aus SQLite
        try:
            db = agent_sessions._init_db()
            rows = db.execute(
                "SELECT id, started_at, ended_at FROM sessions WHERE project_id = ? ORDER BY started_at DESC",
                (personal_id,),
            ).fetchall()
            sessions = []
            for row in rows:
                msgs = db.execute(
                    "SELECT role, content, metadata, created_at FROM messages WHERE session_id = ? ORDER BY created_at",
                    (row["id"],),
                ).fetchall()
                sessions.append({
                    "id": row["id"],
                    "started_at": row["started_at"],
                    "ended_at": row["ended_at"],
                    "messages": [{"role": m["role"], "content": m["content"], "created_at": m["created_at"]} for m in msgs],
                })
            export["sessions"] = sessions
        except Exception as _e:
            export["sessions_error"] = str(_e)

        # Soul
        soul_file = agent_dir / "soul.md"
        if soul_file.exists():
            try:
                export["soul"] = soul_file.read_text(encoding="utf-8")
            except Exception:
                pass

        audit_log("user.export", target=username)
        return export

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
        if req.group is not None:
            users[username]["group"] = req.group
        if req.allowed_projects is not None:
            users[username]["allowed_projects"] = req.allowed_projects
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
    async def change_user_password(username: str, req: ChangePasswordRequest):
        new_password = req.password.strip()
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

        # #414: Images als Content-Blocks für Vision
        _user_content = req.content
        _images = getattr(req, "images", None) or []
        if _images:
            _content_blocks = []
            for img in _images[:5]:
                _content_blocks.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": img.get("media_type", "image/png"), "data": img["data"]},
                })
            _content_blocks.append({"type": "text", "text": req.content or "Was siehst du auf diesem Bild?"})
            _user_content = _content_blocks

        async def event_stream():
            async for chunk in agent_orchestrator.handle_message_stream(
                project_id=agent_id,
                project_cfg=virtual_cfg,
                content=_user_content,
                sender=req.sender,
                execution_mode=execution_mode,
            ):
                yield chunk

        return _SR(event_stream(), media_type="text/event-stream",
                   headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @auth_router.post("/me/agent/interrupt")
    async def my_agent_interrupt(auth: tuple[str, str] = Depends(require_auth)):
        """Bricht einen laufenden ask_agent-Request des persönlichen Agenten ab (#34)."""
        from .tool_registry import set_interrupt as _set_interrupt
        username, _role = auth
        agent_id = f"personal_{username}"
        _set_interrupt(agent_id)
        return {"ok": True, "agent_id": agent_id}

    @auth_router.get("/me/agent/session/history")
    def my_agent_session_history(
        limit: int = 50,
        auth: tuple[str, str] = Depends(require_auth),
    ):
        username, _role = auth
        agent_id = f"personal_{username}"
        context = agent_sessions.get_history(agent_id, max_messages=limit)
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
        # Trusted-Risk-Policy ist eine bewusste Eskalation und nur für Admins
        # zulässig — verhindert Selbst-Eskalation eines Standard-Users über
        # das eigene Personal-Agent-Form.
        if req.risk_policy == "trusted" and _role != "admin":
            raise HTTPException(
                403,
                "risk_policy='trusted' darf nur durch Admins gesetzt werden.",
            )
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

    @auth_router.get("/me/wizard-status")
    def get_wizard_status(auth: tuple[str, str] = Depends(require_auth)):
        username, _role = auth
        users = load_users()
        user_data = users.get(username, {})
        wizard_done = user_data.get("wizard_done", False)
        # Bestehende User (Agent-Dir schon vorhanden) gelten als fertig
        if not wizard_done:
            personal_dir = Path(agents_dir) / f"personal_{username}"
            if personal_dir.exists() and not (personal_dir / "startup.md").exists():
                wizard_done = True
        return {
            "done": wizard_done,
            "group": user_data.get("group", "standard"),
        }

    @auth_router.post("/me/wizard")
    async def complete_wizard(
        body: dict = Body(...),
        auth: tuple[str, str] = Depends(require_auth),
    ):
        import yaml as _yaml

        username, _role = auth
        users = load_users()
        if username not in users:
            raise HTTPException(404, "User nicht gefunden")
        users[username]["wizard_done"] = True
        save_users(users)

        # Ausgewählte Tools in agent.yaml übernehmen
        selected_tools: list[str] | None = body.get("tools")
        if selected_tools is not None:
            agent_id = f"personal_{username}"
            agent_dir = Path(agents_dir) / agent_id
            yaml_path = agent_dir / "agent.yaml"
            if yaml_path.exists():
                try:
                    raw = _yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
                    raw["tools"] = selected_tools
                    yaml_path.write_text(
                        _yaml.dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8"
                    )
                    if load_agent_config_direct is not None:
                        load_agent_config_direct(agent_dir)
                except Exception as e:
                    logger.warning("Wizard: Fehler beim Speichern der Tools: %s", e)

        audit_log("user.wizard_done", target=username)
        return {"done": True}

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
            logger.error("Heartbeat-Config speichern fehlgeschlagen für %s: %s", agent_id, e)
            raise HTTPException(500, "Fehler beim Speichern der Konfiguration")
        load_agent_config_direct(agent_dir)
        logger.info("Heartbeat konfiguriert: %s", agent_id)
        return {"updated": True, "agent_id": agent_id}

    # ── Agent Export ───────────────────────────────────────────────────────────

    @auth_router.get("/me/agent/export")
    async def export_my_agent(auth: tuple[str, str] = Depends(require_auth)):
        """Packt das persönliche Agent-Verzeichnis als tar.gz und streamt es."""
        import io
        import tarfile as _tar
        from fastapi.responses import StreamingResponse as _SR

        username, _role = auth
        agent_id = f"personal_{username}"
        agent_dir = Path(agents_dir) / agent_id
        if not agent_dir.exists():
            raise HTTPException(404, "Kein Agent-Verzeichnis gefunden")

        buf = io.BytesIO()
        with _tar.open(fileobj=buf, mode="w:gz") as tf:
            # Nur sichere Dateitypen einpacken
            for f in agent_dir.rglob("*"):
                if f.is_file() and f.suffix in {
                    ".yaml", ".md", ".json", ".txt", ".py", ".toml"
                }:
                    # Pfad im Archiv: agent_id/relativer/pfad (kein absoluter Pfad)
                    arcname = str(f.relative_to(agent_dir.parent))
                    tf.add(f, arcname=arcname)
        buf.seek(0)

        filename = f"{agent_id}.tar.gz"
        return _SR(
            iter([buf.read()]),
            media_type="application/gzip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @auth_router.get("/me/gdpr-export")
    async def gdpr_export_self(auth: tuple[str, str] = Depends(require_auth)):
        """GDPR Art. 20: Self-Service Daten-Export als JSON."""
        username, _role = auth
        # Delegiere an den Admin-Endpoint
        return await export_user_data(username)

    @auth_router.delete("/me/gdpr-delete")
    async def gdpr_delete_self(auth: tuple[str, str] = Depends(require_auth)):
        """GDPR Art. 17: Recht auf Löschung — löscht eigene Daten (nicht den Account)."""
        username, _role = auth
        personal_id = f"personal_{username}"
        import shutil as _shutil

        deleted_items = []

        # Sessions aus SQLite löschen
        try:
            db = agent_sessions._init_db()
            db.execute("DELETE FROM messages WHERE session_id IN (SELECT id FROM sessions WHERE project_id = ?)", (personal_id,))
            db.execute("DELETE FROM sessions WHERE project_id = ?", (personal_id,))
            db.commit()
            deleted_items.append("sessions")
        except Exception:
            pass

        # Memory löschen
        memory_dir = Path(agents_dir) / personal_id / "memory"
        if memory_dir.exists():
            _shutil.rmtree(memory_dir)
            memory_dir.mkdir(exist_ok=True)
            deleted_items.append("memory")

        # Session-Dateien löschen (legacy .json)
        sessions_dir = Path(agents_dir) / personal_id / ".sessions"
        if sessions_dir.exists():
            _shutil.rmtree(sessions_dir)
            sessions_dir.mkdir(exist_ok=True)
            deleted_items.append("session_files")

        # Aktive Session beenden
        await agent_sessions.end_session(personal_id)

        audit_log("user.gdpr_delete", target=username)
        logger.info("GDPR-Löschung für %s: %s", username, deleted_items)
        return {"deleted": deleted_items, "username": username, "note": "Account bleibt erhalten, Daten gelöscht"}

    # ── Agent Import ───────────────────────────────────────────────────────────

    @auth_router.post("/me/agent/import")
    async def import_my_agent(
        file: UploadFile = File(...),
        auth: tuple[str, str] = Depends(require_auth),
    ):
        """
        Importiert ein Agent-tar.gz. Entpackt sicher in das persönliche Agent-Verzeichnis.
        Path-Traversal-Schutz: nur erlaubte Dateitypen, keine absoluten Pfade oder '..'.
        Bestehende Konfiguration wird überschrieben, Memory/Skills bleiben erhalten.
        """
        import io
        import shutil as _shutil
        import tarfile as _tar

        ALLOWED_SUFFIXES = {".yaml", ".md", ".json", ".txt", ".py", ".toml"}
        MAX_SIZE = 50 * 1024 * 1024  # 50 MB

        username, _role = auth
        agent_id = f"personal_{username}"
        agent_dir = Path(agents_dir) / agent_id

        raw = await file.read()
        if len(raw) > MAX_SIZE:
            raise HTTPException(400, "Datei zu groß (max 50 MB)")

        try:
            buf = io.BytesIO(raw)
            with _tar.open(fileobj=buf, mode="r:gz") as tf:
                members = tf.getmembers()

                # Sicherheitsprüfung: alle Einträge validieren
                for m in members:
                    # Keine absoluten Pfade, kein Path Traversal
                    if m.name.startswith("/") or ".." in m.name.split("/"):
                        raise HTTPException(400, f"Ungültiger Pfad im Archiv: {m.name}")
                    if m.isfile():
                        suffix = Path(m.name).suffix.lower()
                        if suffix not in ALLOWED_SUFFIXES:
                            raise HTTPException(400, f"Nicht erlaubter Dateityp: {m.name}")
                    # Keine Symlinks oder Device-Files
                    if not (m.isfile() or m.isdir()):
                        raise HTTPException(400, f"Nur Dateien und Verzeichnisse erlaubt: {m.name}")

                # Archiv enthält typischerweise personal_<name>/... oder flache Struktur
                # Normalisieren: ersten Pfadkomponente (agent_id im Archiv) entfernen
                agent_dir.mkdir(parents=True, exist_ok=True)
                extracted = 0
                for m in members:
                    parts = Path(m.name).parts
                    # Ersten Teil (Archiv-Root) überspringen → direkt in agent_dir
                    rel_parts = parts[1:] if len(parts) > 1 else parts
                    if not rel_parts:
                        continue
                    target = agent_dir.joinpath(*rel_parts)
                    # Nochmal sicherstellen dass target wirklich unter agent_dir liegt
                    try:
                        target.relative_to(agent_dir)
                    except ValueError:
                        raise HTTPException(400, "Path Traversal erkannt")
                    if m.isdir():
                        target.mkdir(parents=True, exist_ok=True)
                    elif m.isfile():
                        target.parent.mkdir(parents=True, exist_ok=True)
                        fobj = tf.extractfile(m)
                        if fobj:
                            target.write_bytes(fobj.read())
                            extracted += 1

        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(400, f"Ungültiges Archiv: {e}")

        # Agent neu registrieren
        try:
            load_agent_config_direct(agent_dir)
            if discovery:
                discovery._register(agent_dir)
        except Exception:
            pass

        logger.info("Agent importiert: %s (%d Dateien)", agent_id, extracted)
        return {"ok": True, "agent_id": agent_id, "files": extracted}

    return {"create_user": create_user}
