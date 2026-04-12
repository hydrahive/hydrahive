from __future__ import annotations

import asyncio
from pathlib import Path
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .execution_mode_policy import resolve_request_execution_mode
from .session_manager import MessageRole
# v2: plugin_manager entfernt


class UpdateProjectRequest(BaseModel):
    """v1 Legacy — wird nur noch fuer PUT /projects/{id} genutzt."""
    name: str | None = None
    description: str | None = None
    members: list[str] | None = None


class CreateProjectRequest(BaseModel):
    id: str
    name: str
    description: str = ""
    boss: str = ""  # v1 deprecated — wird ignoriert, Projekt ist sein eigener Agent
    workers: list[str] = []  # v1 deprecated
    samba: bool = True
    nfs: bool = False
    members: list[str] = []
    github_repo: str = ""


class TypingRequest(BaseModel):
    active: bool = True


class MessageRequest(BaseModel):
    role: str
    content: str
    agent_id: str | None = None


class ProjectIncomingMessage(BaseModel):
    content: str
    sender: str = "user"
    execution_mode: str | None = None
    images: list[dict] | None = None  # #414: Vision-Support


class ProjectWorkflowRequest(BaseModel):
    nodes: list = []
    edges: list = []


# v2: Projekt-Erstellung mit Template
class CreateProjectV2Request(BaseModel):
    id: str
    name: str
    description: str = ""
    template: str = "general"
    provider: str = "anthropic"
    model: str = "claude-sonnet-4-6"
    temperature: float = 0.5
    max_tokens: int = 4096
    api_key_env: str = ""
    failover: list[dict] = []
    agent_md: str = ""
    members: list[str] = []


# v2: Projekt-Settings aktualisieren
class UpdateProjectSettingsRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    provider: str | None = None
    model: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    api_key_env: str | None = None
    failover: list[dict] | None = None
    agent_md: str | None = None
    members: list[str] | None = None
    execution_mode: str | None = None  # safe | elevated | unrestricted (#568)
    messenger: dict | None = None  # Discord/Telegram/Matrix Config (#569)


class GitCloneRequest(BaseModel):
    url: str
    branch: str = "main"


def register_project_routes(
    auth_router: APIRouter,
    admin_router: APIRouter,
    *,
    require_auth,
    projects,
    discovery,
    runtime,
    sessions,
    orchestrator,
    projects_dir: str,
    get_provisioner,
    update_project_matrix_room,
    update_project_matrix_space,
    get_user_allowed_projects,
    audit_log,
    check_message_rate,
    logger,
) -> None:
    def _check_project_access(auth: tuple[str, str], project_id: str) -> None:
        """Wirft 403 wenn der User keinen Zugriff auf das Projekt hat."""
        username, role = auth
        allowed = get_user_allowed_projects(username, role)
        if allowed is not None and project_id not in allowed:
            raise HTTPException(403, f"Kein Zugriff auf Projekt '{project_id}'")

    @auth_router.get("/projects")
    def list_projects(auth: tuple[str, str] = Depends(require_auth)):
        username, role = auth
        allowed = get_user_allowed_projects(username, role)
        return {
            pid: {
                "name": cfg.identity.name,
                "description": cfg.identity.description,
                "matrix_room": cfg.matrix.room,
                "filesystem": cfg.effective_filesystem_path(),
                "system_user": cfg.effective_system_user(),
                "members": list(getattr(cfg, "members", [])),
            }
            for pid, cfg in projects.projects.items()
            if allowed is None or pid in allowed
        }

    @auth_router.get("/projects/{project_id}")
    def get_project(project_id: str, auth: tuple[str, str] = Depends(require_auth)):
        _check_project_access(auth, project_id)
        cfg = projects.get(project_id)
        if not cfg:
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
        known = set(discovery.agents.keys())
        missing = [a for a in cfg.all_agents if a not in known]
        return {
            "config": cfg.model_dump(exclude={"project_dir"}),
            "missing_agents": missing,
            "system_user": cfg.effective_system_user(),
            "filesystem_path": cfg.effective_filesystem_path(),
        }

    @auth_router.get("/projects/{project_id}/agents")
    def project_agents(project_id: str, auth: tuple[str, str] = Depends(require_auth)):
        _check_project_access(auth, project_id)
        cfg = projects.get(project_id)
        if not cfg:
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
        running = runtime.status_all()
        return {
            agent_id: {
                "role": "boss" if agent_id == cfg.agents.boss else "worker",
                "found": discovery.get(agent_id) is not None,
                "runtime": running.get(agent_id),
            }
            for agent_id in cfg.all_agents
        }

    @auth_router.get("/projects/{project_id}/monitor")
    def project_monitor(project_id: str, auth: tuple[str, str] = Depends(require_auth)):
        """EKG Monitor — gebündelte Live-Daten für alle Agenten eines Projekts (#534)."""
        _check_project_access(auth, project_id)
        cfg = projects.get(project_id)
        if not cfg:
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")

        running = runtime.status_all()

        # Session-Metriken laden
        from .session_metrics import metrics as _metrics
        session_snap = _metrics.snapshot(project_id)

        agents_data = {}
        for agent_id in cfg.all_agents:
            agent_cfg = discovery.get(agent_id)
            rt = running.get(agent_id, {})
            agents_data[agent_id] = {
                "role": "boss" if agent_id == cfg.agents.boss else "worker",
                "identity": getattr(agent_cfg, "identity", agent_id) if agent_cfg else agent_id,
                "model": getattr(getattr(agent_cfg, "llm", None), "model", None) if agent_cfg else None,
                "type": agent_cfg.type if agent_cfg else "unknown",
                "tools": [t if isinstance(t, str) else getattr(t, "name", str(t)) for t in (agent_cfg.tools if agent_cfg else [])],
                "status": rt.get("status", "unknown"),
                "current_activity": rt.get("current_activity"),
                "total_requests": rt.get("total_requests", 0),
                "avg_response_ms": rt.get("avg_response_ms", 0),
                "last_response_ms": rt.get("last_response_ms", 0),
                "error_rate": rt.get("error_rate", 0),
            }

        return {
            "project_id": project_id,
            "project_name": getattr(getattr(cfg, "identity", None), "name", project_id) if hasattr(cfg, "identity") else project_id,
            "agents": agents_data,
            "metrics": session_snap,
        }

    @admin_router.put("/projects/{project_id}")
    async def update_project(project_id: str, req: UpdateProjectRequest):
        import yaml as _yaml

        cfg = projects.get(project_id)
        if not cfg:
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")

        project_dir = Path(projects_dir) / project_id
        # v2: config.yaml bevorzugen, Fallback auf project.yaml
        yaml_path = project_dir / "config.yaml"
        if not yaml_path.exists():
            yaml_path = project_dir / "project.yaml"
        if not yaml_path.exists():
            raise HTTPException(404, "Keine Config-Datei gefunden")
        data = _yaml.safe_load(yaml_path.read_text(encoding="utf-8"))

        if req.name is not None:
            data.setdefault("identity", {})["name"] = req.name
        if req.description is not None:
            data.setdefault("identity", {})["description"] = req.description
        if req.members is not None:
            data["members"] = req.members

        yaml_path.write_text(_yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
        projects.register(project_dir)  # neu einlesen
        audit_log("project.update", target=project_id, project_id=project_id)
        logger.info("Projekt aktualisiert: %s", project_id)
        return {"updated": True, "project_id": project_id}

    # ── v2: Projekt-Erstellung mit Template ──────────────────────────────

    @admin_router.post("/projects/v2", status_code=201)
    async def create_project_v2(req: CreateProjectV2Request):
        """v2: Projekt erstellen mit Template + config.yaml + AGENT.md."""
        import yaml as _yaml

        if not re.match(r"^[a-z0-9_-]+$", req.id):
            raise HTTPException(400, "Projekt-ID darf nur a-z, 0-9, _ und - enthalten")
        if projects.get(req.id):
            raise HTTPException(409, f"Projekt '{req.id}' existiert bereits")

        project_dir = Path(projects_dir) / req.id
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "memory").mkdir(exist_ok=True)

        # config.yaml aus Request-Daten
        config_data = {
            "id": req.id,
            "version": "2.0.0",
            "identity": {"name": req.name, "description": req.description},
            "llm": {
                "provider": req.provider,
                "model": req.model,
                "temperature": req.temperature,
                "max_tokens": req.max_tokens,
                "api_key_env": req.api_key_env,
                "failover": req.failover,
            },
            "plugins": [],
            "repos": [],
            "sources": [],
            "members": req.members or ["admin"],
        }
        config_path = project_dir / "config.yaml"
        config_path.write_text(
            _yaml.dump(config_data, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )

        # AGENT.md — eigener Text oder aus Template
        agent_md_text = req.agent_md.strip() if req.agent_md else ""
        if not agent_md_text:
            template_dir = Path("/opt/hydrahive/installer/templates") / req.template
            template_md = template_dir / "AGENT.md"
            if template_md.exists():
                agent_md_text = template_md.read_text(encoding="utf-8")
            else:
                agent_md_text = f"# {req.name}\n\nBeschreibe hier das Fachgebiet."

        agent_md_path = project_dir / "AGENT.md"
        agent_md_path.write_text(agent_md_text, encoding="utf-8")

        # Berechtigungen
        import subprocess
        subprocess.run(["chown", "-R", "hydrahive:hydrahive", str(project_dir)],
                       capture_output=True, timeout=5)

        # Im ProjectLoader registrieren
        import asyncio as _asyncio
        await _asyncio.sleep(0.2)
        cfg = projects.get(req.id) or projects.register(project_dir)

        audit_log("project.create_v2", target=req.id, project_id=req.id,
                  details={"template": req.template, "model": req.model})
        logger.info("v2-Projekt erstellt: %s (Template: %s, LLM: %s/%s)",
                    req.id, req.template, req.provider, req.model)

        return {
            "created": True,
            "project_id": req.id,
            "version": "2.0.0",
            "template": req.template,
            "model": f"{req.provider}/{req.model}",
        }

    # ── v2: Projekt-Settings lesen/schreiben ──────────────────────────

    @auth_router.get("/projects/{project_id}/settings")
    def get_project_settings(project_id: str, _auth: tuple[str, str] = Depends(require_auth)):
        """v2: Projekt-Config + AGENT.md laden für Settings-Seite."""
        _check_project_access(_auth, project_id)
        cfg = projects.get(project_id)
        if not cfg:
            raise HTTPException(404, "Projekt nicht gefunden")

        project_dir = Path(projects_dir) / project_id
        agent_md = ""
        agent_md_path = project_dir / "AGENT.md"
        if agent_md_path.exists():
            agent_md = agent_md_path.read_text(encoding="utf-8")

        return {
            "project_id": project_id,
            "is_v2": getattr(cfg, "is_v2", False),
            "identity": {"name": cfg.identity.name, "description": cfg.identity.description},
            "llm": {
                "provider": getattr(cfg.llm, "provider", "anthropic"),
                "model": getattr(cfg.llm, "model", ""),
                "temperature": getattr(cfg.llm, "temperature", 0.5),
                "max_tokens": getattr(cfg.llm, "max_tokens", 4096),
                "api_key_env": getattr(cfg.llm, "api_key_env", ""),
                "failover": getattr(cfg.llm, "failover", []),
            },
            "agent_md": agent_md,
            "members": cfg.members,
            "execution_mode": getattr(cfg, "execution_mode", "safe"),
            "plugins": getattr(cfg, "plugins", []),
            "repos": getattr(cfg, "repos", []),
            "sources": getattr(cfg, "sources", []),
            "messenger": _load_messenger_config(project_dir),
        }

    def _load_messenger_config(project_dir: Path) -> dict:
        """messenger.yaml laden wenn vorhanden."""
        import yaml as _yaml
        mp = project_dir / "messenger.yaml"
        if not mp.exists():
            return {}
        try:
            return _yaml.safe_load(mp.read_text(encoding="utf-8")) or {}
        except Exception:
            return {}

    @auth_router.put("/projects/{project_id}/settings")
    def update_project_settings(
        project_id: str,
        req: UpdateProjectSettingsRequest,
        _auth: tuple[str, str] = Depends(require_auth),
    ):
        """v2: Projekt-Config + AGENT.md speichern.
        #586: Nur Admin oder Owner (erster Member) darf aendern."""
        import yaml as _yaml

        _check_project_access(_auth, project_id)
        username, role = _auth
        cfg = projects.get(project_id)
        if not cfg:
            raise HTTPException(404, "Projekt nicht gefunden")

        # #586: Schreibrechte nur fuer Admin oder Personal-Projekt-Owner
        is_admin = role == "admin"
        is_personal_owner = project_id == f"personal_{username}"
        members_list = list(getattr(cfg, "members", []) or [])
        is_project_owner = bool(members_list) and members_list[0] == username
        if not (is_admin or is_personal_owner or is_project_owner):
            raise HTTPException(
                403,
                "Nur Admins oder Projekt-Owner (erster Member) duerfen Settings aendern.",
            )

        project_dir = Path(projects_dir) / project_id
        config_path = project_dir / "config.yaml"

        # Bestehende config.yaml laden oder neu erstellen
        if config_path.exists():
            try:
                config_data = _yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            except Exception:
                config_data = {}
        else:
            config_data = {"id": project_id, "version": "2.0.0"}

        # Felder aktualisieren (nur wenn im Request gesetzt)
        if req.name is not None or req.description is not None:
            config_data.setdefault("identity", {})
            if req.name is not None:
                config_data["identity"]["name"] = req.name
            if req.description is not None:
                config_data["identity"]["description"] = req.description

        llm = config_data.setdefault("llm", {})
        if req.provider is not None:
            llm["provider"] = req.provider
        if req.model is not None:
            llm["model"] = req.model
        if req.temperature is not None:
            llm["temperature"] = req.temperature
        if req.max_tokens is not None:
            llm["max_tokens"] = req.max_tokens
        if req.api_key_env is not None:
            llm["api_key_env"] = req.api_key_env
        if req.failover is not None:
            llm["failover"] = req.failover

        if req.members is not None:
            config_data["members"] = req.members

        if req.execution_mode is not None:
            if req.execution_mode in ("safe", "elevated", "unrestricted"):
                config_data["execution_mode"] = req.execution_mode

        config_data["version"] = "2.0.0"

        # config.yaml schreiben
        config_path.write_text(
            _yaml.dump(config_data, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )

        # AGENT.md schreiben wenn im Request
        if req.agent_md is not None:
            agent_md_path = project_dir / "AGENT.md"
            agent_md_path.write_text(req.agent_md, encoding="utf-8")

        # messenger.yaml schreiben wenn im Request (#569)
        if req.messenger is not None:
            messenger_path = project_dir / "messenger.yaml"
            messenger_path.write_text(
                _yaml.dump(req.messenger, allow_unicode=True, default_flow_style=False, sort_keys=False),
                encoding="utf-8",
            )
            # Messenger-Router neu laden
            try:
                from .messenger_router import messenger_router as _mr
                _mr.rebuild()
            except Exception:
                pass

        # ProjectLoader neu laden
        projects.register(project_dir)

        audit_log("project.settings_update", target=project_id, project_id=project_id)
        logger.info("v2-Projekt Settings aktualisiert: %s", project_id)

        return {"updated": True, "project_id": project_id}

    # ── v1: Projekt-Erstellung (Legacy) ────────────────────────────────

    @admin_router.post("/projects", status_code=201)
    async def create_project(req: CreateProjectRequest):
        import asyncio as _asyncio
        import yaml as _yaml

        if not re.match(r"^[a-z0-9_-]+$", req.id):
            raise HTTPException(400, "Projekt-ID darf nur a-z, 0-9, _ und - enthalten")
        if projects.get(req.id):
            # Project already exists — but Gitea repo might be missing (e.g. first creation failed).
            # Try to ensure the Gitea repo exists before returning 409.
            try:
                from .gitea import get_gitea_client
                import aiohttp as _aiohttp
                gitea = get_gitea_client()
                repo_exists = True
                try:
                    await gitea.get_repo_info(req.id)
                except _aiohttp.ClientResponseError as ce:
                    if ce.status == 404:
                        repo_exists = False
                    else:
                        raise
                if not repo_exists:
                    repo = await gitea.create_repo(req.id, description=req.description or "")
                    webhook_url = f"http://127.0.0.1:8765/webhooks/gitea/{req.id}"
                    await gitea.create_webhook(req.id, webhook_url)
                    logger.info("Gitea-Repo nachträglich angelegt für '%s'", req.id)
                    return {"created": False, "project_id": req.id, "gitea_repo": repo.get("html_url", ""), "gitea_retroactive": True}
            except Exception as e:
                logger.warning("Gitea-Retro-Check fehlgeschlagen für '%s': %s", req.id, e)
            raise HTTPException(409, f"Projekt '{req.id}' existiert bereits")
        project_dir = Path(projects_dir) / req.id
        project_dir.mkdir(parents=True, exist_ok=True)
        (project_dir / "memory").mkdir(exist_ok=True)

        # v2: config.yaml statt project.yaml
        config_data = {
            "id": req.id,
            "version": "2.0.0",
            "identity": {"name": req.name, "description": req.description},
            "llm": {"provider": "anthropic", "model": "claude-sonnet-4-6", "temperature": 0.7, "max_tokens": 4096},
            "filesystem": {"path": f"/projects/{req.id}", "samba": req.samba, "nfs": req.nfs},
            "system": {"user": f"proj_{req.id}", "group": f"proj_{req.id}"},
            "members": req.members,
            "github_repo": req.github_repo,
        }
        config_path = project_dir / "config.yaml"
        config_path.write_text(_yaml.dump(config_data, allow_unicode=True, default_flow_style=False), encoding="utf-8")

        # AGENT.md Grundgeruest
        agent_md_path = project_dir / "AGENT.md"
        if not agent_md_path.exists():
            agent_md_path.write_text(f"# {req.name}\n\nBeschreibe hier das Fachgebiet, die Regeln und den Kontext.\n")

        logger.info("v2-Projekt erstellt: %s", config_path)
        audit_log("project.create", target=req.id, project_id=req.id)

        await _asyncio.sleep(0.3)

        cfg = projects.get(req.id) or projects.register(project_dir)
        if cfg is None:
            raise HTTPException(500, "Projekt konnte nach Anlage nicht geladen werden")

        provisioner = get_provisioner()
        if provisioner is None:
            raise HTTPException(503, "Provisioner nicht initialisiert")

        result = await provisioner.provision(cfg)
        audit_log("project.provision", target=req.id, project_id=req.id)
        if result.matrix_room and not cfg.matrix.room:
            update_project_matrix_room(req.id, result.matrix_room)
        if result.matrix_space and not cfg.matrix.space:
            update_project_matrix_space(req.id, result.matrix_space)

        gitea_repo_url = ""
        gitea_error = ""
        try:
            from .gitea import get_gitea_client
            gitea = get_gitea_client()
            repo = await gitea.create_repo(req.id, description=req.description or "")
            gitea_repo_url = repo.get("html_url", "")
            webhook_url = f"http://127.0.0.1:8765/webhooks/gitea/{req.id}"
            await gitea.create_webhook(req.id, webhook_url)
            logger.info("Gitea-Repo '%s' angelegt: %s", req.id, gitea_repo_url)
        except Exception as e:
            gitea_error = str(e)
            logger.warning("Gitea-Repo konnte nicht angelegt werden: %s", e)

        return {
            "created": True,
            "project_id": req.id,
            "linux_user": result.linux_user,
            "files_dir": result.files_dir,
            "samba_share": result.samba_share,
            "matrix_room": result.matrix_room,
            "matrix_space": result.matrix_space,
            "warnings": result.warnings,
            "ok": result.ok,
            "gitea_repo": gitea_repo_url,
            "gitea_error": gitea_error,
        }

    @admin_router.post("/projects/{project_id}/git-clone")
    async def git_clone_into_project(project_id: str, req: GitCloneRequest):
        """Klont ein Git-Repo in das Projektverzeichnis."""
        import asyncio as _asyncio
        import re as _re
        import subprocess as _sp
        # #335: project_id validieren gegen Path-Traversal
        if not _re.match(r"^[a-z0-9_.\-]+$", project_id) or ".." in project_id:
            raise HTTPException(400, "Ungültige project_id")
        project_dir = Path(projects_dir) / project_id
        if not project_dir.exists():
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")

        # Repo-Name aus URL → Unterordner
        repo_name = req.url.rstrip("/").removesuffix(".git").split("/")[-1] or "repo"
        target = project_dir / repo_name

        try:
            result = await _asyncio.to_thread(
                _sp.run,
                ["git", "clone", "--branch", req.branch, "--single-branch", req.url, str(target)],
                capture_output=True, text=True, timeout=300,
                cwd=str(project_dir),
            )
            if result.returncode != 0:
                logger.error("Git clone fehlgeschlagen für %s: %s", req.url, result.stderr.strip()[-500:])
                raise HTTPException(500, "Git clone fehlgeschlagen")
            # Berechtigungen setzen
            _sp.run(["chown", "-R", "hydrahive:hydrahive", str(target)], check=False, capture_output=True)
            return {"ok": True, "project_id": project_id, "cloned_to": str(target), "branch": req.branch}
        except _sp.TimeoutExpired:
            raise HTTPException(504, "Git clone Timeout (5 Minuten)")
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Git clone Exception für %s: %s", project_id, e)
            raise HTTPException(500, "Interner Fehler beim Klonen")

    @auth_router.get("/projects/{project_id}/session")
    def get_session(project_id: str, auth: tuple[str, str] = Depends(require_auth)):
        _check_project_access(auth, project_id)
        if not projects.get(project_id):
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
        session = sessions.get_active(project_id)
        if not session:
            return {"active": False, "session": None}
        return {
            "active": True,
            "session_id": session.id,
            "started_at": session.started_at,
            "message_count": len(session.messages),
        }

    @auth_router.post("/projects/{project_id}/session/start")
    async def start_session(project_id: str, auth: tuple[str, str] = Depends(require_auth)):
        _check_project_access(auth, project_id)
        if not projects.get(project_id):
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
        session = await sessions.new_session(project_id)
        return {"session_id": session.id, "started_at": session.started_at}

    @auth_router.post("/projects/{project_id}/session/end")
    async def end_session(project_id: str, auth: tuple[str, str] = Depends(require_auth)):
        _check_project_access(auth, project_id)
        if not projects.get(project_id):
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
        session = await sessions.end_session(project_id)
        if not session:
            return {"ended": False}
        return {"ended": True, "session_id": session.id, "message_count": len(session.messages)}

    @auth_router.post("/projects/{project_id}/session/message")
    async def append_message(project_id: str, req: MessageRequest, auth: tuple[str, str] = Depends(require_auth)):
        _check_project_access(auth, project_id)
        if not projects.get(project_id):
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
        try:
            role = MessageRole(req.role)
        except ValueError:
            raise HTTPException(400, f"Ungültige Rolle: {req.role}. Erlaubt: user, assistant, system, tool")
        msg = await sessions.append(project_id, role, req.content, agent_id=req.agent_id)
        session = sessions.get_active(project_id)
        return {
            "appended": True,
            "session_id": session.id if session else None,
            "message_count": len(session.messages) if session else 1,
            "timestamp": msg.timestamp,
        }

    @auth_router.get("/projects/{project_id}/session/history")
    def session_history(project_id: str, limit: int = 50, auth: tuple[str, str] = Depends(require_auth)):
        _check_project_access(auth, project_id)
        if not projects.get(project_id):
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
        context = sessions.get_history(project_id, max_messages=limit)
        session = sessions.get_active(project_id)
        return {
            "session_id": session.id if session else None,
            "messages": context,
            "count": len(context),
        }

    @auth_router.get("/projects/{project_id}/sessions")
    def list_sessions(project_id: str, limit: int = 20, auth: tuple[str, str] = Depends(require_auth)):
        _check_project_access(auth, project_id)
        if not projects.get(project_id):
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
        return {"sessions": sessions.list_sessions(project_id, limit)}

    @auth_router.get("/projects/{project_id}/sessions/{session_id}")
    def get_session_by_id(project_id: str, session_id: str, auth: tuple[str, str] = Depends(require_auth)):
        _check_project_access(auth, project_id)
        if not projects.get(project_id):
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
        session = sessions.get_session_by_id(project_id, session_id)
        if not session:
            raise HTTPException(404, "Session nicht gefunden")
        return session.to_dict()

    @auth_router.post("/projects/{project_id}/sessions/{session_id}/resume")
    async def resume_project_session(project_id: str, session_id: str, auth: tuple[str, str] = Depends(require_auth)):
        _check_project_access(auth, project_id)
        if not projects.get(project_id):
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
        session = await sessions.resume_session(project_id, session_id)
        if not session:
            raise HTTPException(404, "Session nicht gefunden")
        return {
            "resumed": True,
            "id": session.id,
            "messages": [
                {"role": m.role.value, "content": m.content, "timestamp": m.timestamp}
                for m in session.messages
            ],
        }

    @auth_router.post("/projects/{project_id}/message/stream")
    async def send_message_stream(
        project_id: str,
        req: ProjectIncomingMessage,
        auth: tuple[str, str] = Depends(require_auth),
    ):
        from fastapi.responses import StreamingResponse as _SR

        _check_project_access(auth, project_id)
        # #330: sender aus Auth, nicht aus Body
        sender = auth[0] if auth[0] != "internal" else (req.sender or "user")
        check_message_rate(sender, project_id)
        cfg = projects.get(project_id)
        if not cfg:
            raise HTTPException(404, "Projekt nicht gefunden")
        # v2: Projekt ist sein eigener Agent — kein Boss-Agent nötig
        if not getattr(cfg, "is_v2", False):
            if not discovery.get(cfg.agents.boss):
                raise HTTPException(503, "Boss-Agent nicht verfügbar")
        # v2: Projekt-Default execution_mode als Fallback (#568)
        _req_mode = req.execution_mode or getattr(cfg, "execution_mode", None)
        execution_mode = resolve_request_execution_mode(
            auth,
            _req_mode,
            audit_log=audit_log,
            audit_target=project_id,
            audit_source="projects.message.stream",
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

        # v2: Shared Sessions — Turn-Lock + Broadcast
        from .shared_session import shared_sessions as _ss

        if not _ss.acquire_turn(project_id, sender):
            _turn_owner = _ss.turn_owner(project_id)
            raise HTTPException(
                409, f"Projekt ist gerade belegt von '{_turn_owner}'. Bitte warten."
            )

        # User-Nachricht an alle Subscriber broadcasten (fuer Multi-Browser-Sync)
        import json as _json
        _ss.broadcast(project_id, _json.dumps({"_user_message": req.content, "_sender": sender}))

        async def event_stream():
            try:
                async for chunk in orchestrator.handle_message_stream(
                    project_id=project_id,
                    project_cfg=cfg,
                    content=_user_content,
                    sender=sender,
                    execution_mode=execution_mode,
                ):
                    # Chunk an den sendenden Client
                    yield chunk
                    # Chunk an alle anderen Subscriber broadcasten
                    # SSE-Format: "data: {...}\n\n" → JSON extrahieren
                    if chunk.startswith("data: "):
                        _ss.broadcast(project_id, chunk[6:].strip())
            finally:
                _ss.release_turn(project_id, sender)

        return _SR(event_stream(), media_type="text/event-stream",
                   headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @auth_router.get("/projects/{project_id}/subscribe")
    async def subscribe_project_stream(
        project_id: str,
        auth: tuple[str, str] = Depends(require_auth),
    ):
        """v2: Shared Session — passiv zuschauen was im Projekt passiert.
        Empfängt alle SSE-Events die andere User auslösen.
        Für Multi-User: User A sendet, User B sieht in Echtzeit mit."""
        from fastapi.responses import StreamingResponse as _SR
        from starlette.requests import Request
        from .shared_session import shared_sessions as _ss

        _check_project_access(auth, project_id)
        username = auth[0]

        queue = _ss.subscribe(project_id, username)

        async def event_stream():
            try:
                while True:
                    try:
                        event = await asyncio.wait_for(queue.get(), timeout=30)
                        # #587: Timestamp refreshen bei jedem Event → User bleibt "online"
                        _ss.touch_presence(project_id, username)
                        yield f"data: {event}\n\n"
                    except asyncio.TimeoutError:
                        # Keepalive + Presence refreshen
                        _ss.touch_presence(project_id, username)
                        yield ": keepalive\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                _ss.unsubscribe(project_id, queue, username)

        return _SR(event_stream(), media_type="text/event-stream",
                   headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

    @auth_router.get("/projects/{project_id}/presence")
    def project_presence(
        project_id: str,
        _auth: tuple[str, str] = Depends(require_auth),
    ):
        """v2: Wer ist gerade in diesem Projekt online?"""
        from .shared_session import shared_sessions as _ss
        _check_project_access(_auth, project_id)
        return {
            "project_id": project_id,
            "online": _ss.online_users(project_id),
            "subscribers": _ss.subscriber_count(project_id),
            "turn_owner": _ss.turn_owner(project_id),
        }

    @auth_router.post("/projects/{project_id}/typing")
    async def project_typing(
        project_id: str,
        req: TypingRequest,
        auth: tuple[str, str] = Depends(require_auth),
    ):
        """v2: Typing-Indicator an alle Subscriber broadcasten (#553)."""
        _check_project_access(auth, project_id)
        username = auth[0]
        from .shared_session import shared_sessions as _ss
        import json as _json
        _ss.broadcast(project_id, _json.dumps({"_typing": {"user": username, "active": req.active}}))
        return {"ok": True}

    @auth_router.post("/projects/{project_id}/interrupt")
    async def interrupt_project_stream(
        project_id: str,
        _auth: tuple[str, str] = Depends(require_auth),
    ):
        _check_project_access(_auth, project_id)
        """Bricht einen laufenden ask_agent-Request ab (#34)."""
        from .tool_registry import set_interrupt as _set_interrupt
        _set_interrupt(project_id)
        return {"ok": True, "project_id": project_id}

    @auth_router.post("/projects/{project_id}/message")
    async def send_message(
        project_id: str,
        req: ProjectIncomingMessage,
        auth: tuple[str, str] = Depends(require_auth),
    ):
        _check_project_access(auth, project_id)
        # #330: sender aus Auth
        sender = auth[0] if auth[0] != "internal" else (req.sender or "user")
        check_message_rate(sender, project_id)
        cfg = projects.get(project_id)
        if not cfg:
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")

        # v2: Projekt ist sein eigener Agent — kein Boss-Agent nötig
        if not getattr(cfg, "is_v2", False):
            boss_id = cfg.agents.boss
            if not discovery.get(boss_id):
                raise HTTPException(503, f"Boss-Agent '{boss_id}' nicht in Discovery")
        _req_mode2 = req.execution_mode or getattr(cfg, "execution_mode", None)
        execution_mode = resolve_request_execution_mode(
            auth,
            _req_mode2,
            audit_log=audit_log,
            audit_target=project_id,
            audit_source="projects.message",
        )

        # v2: plugin_manager.emit entfernt
        # await plugin_manager.emit("message.before", project_id=project_id, content=req.content, sender=sender)
        response, workers = await orchestrator.handle_message(
            project_id=project_id,
            project_cfg=cfg,
            content=req.content,
            sender=sender,
            execution_mode=execution_mode,
        )
        # v2: plugin_manager.emit entfernt
        # await plugin_manager.emit("message.after", project_id=project_id, content=req.content, response=response)
        session = sessions.get_active(project_id)
        return {
            "response": response,
            "workers": workers,
            "session_id": session.id if session else None,
            "message_count": len(session.messages) if session else 0,
        }

    # ── Projekt-Workflow ──────────────────────────────────────────────────────

    @auth_router.get("/projects/{project_id}/workflow")
    def get_workflow(project_id: str, _a: tuple[str, str] = Depends(require_auth)):
        _check_project_access(_a, project_id)
        import json as _json
        project_dir = Path(projects_dir) / project_id
        wf_path = project_dir / "workflow.json"
        if not wf_path.exists():
            return {"nodes": [], "edges": []}
        try:
            return _json.loads(wf_path.read_text(encoding="utf-8"))
        except Exception:
            return {"nodes": [], "edges": []}

    @auth_router.put("/projects/{project_id}/workflow")
    def save_workflow(project_id: str, req: ProjectWorkflowRequest, _a: tuple[str, str] = Depends(require_auth)):
        _check_project_access(_a, project_id)
        import json as _json
        project_dir = Path(projects_dir) / project_id
        if not project_dir.exists():
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
        wf_path = project_dir / "workflow.json"
        wf_path.write_text(_json.dumps(req.model_dump(), indent=2, ensure_ascii=False), encoding="utf-8")
        logger.info("workflow.json gespeichert: %s", wf_path)
        return {"saved": True}
