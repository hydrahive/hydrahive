from __future__ import annotations

from pathlib import Path
import re

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .execution_mode_policy import resolve_request_execution_mode
from .session_manager import MessageRole
# v2: plugin_manager entfernt


class UpdateProjectRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    boss: str | None = None
    workers: list[str] | None = None
    show_swarm: bool | None = None
    members: list[str] | None = None   # HydraHive-Usernames mit Zugang zum Projekt-Room


class CreateProjectRequest(BaseModel):
    id: str
    name: str
    description: str = ""
    boss: str
    workers: list[str] = []
    samba: bool = True
    nfs: bool = False
    show_swarm: bool = False
    members: list[str] = []   # HydraHive-Usernames die sofort eingeladen werden
    github_repo: str = ""    # z.B. "org/repo" oder vollständige GitHub-URL


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
                "boss": cfg.agents.boss,
                "workers": cfg.agents.workers,
                "matrix_room": cfg.matrix.room,
                "filesystem": cfg.effective_filesystem_path(),
                "system_user": cfg.effective_system_user(),
                "show_swarm": cfg.chat.show_swarm,
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

        if req.boss and not discovery.get(req.boss):
            raise HTTPException(422, f"Boss-Agent '{req.boss}' nicht gefunden")
        if req.workers:
            missing = [w for w in req.workers if not discovery.get(w)]
            if missing:
                raise HTTPException(422, f"Worker-Agenten nicht gefunden: {missing}")

        project_dir = Path(projects_dir) / project_id
        yaml_path = project_dir / "project.yaml"
        data = _yaml.safe_load(yaml_path.read_text(encoding="utf-8"))

        if req.name is not None:
            data["identity"]["name"] = req.name
        if req.description is not None:
            data["identity"]["description"] = req.description
        if req.boss is not None:
            data["agents"]["boss"] = req.boss
        if req.workers is not None:
            data["agents"]["workers"] = req.workers
        if req.show_swarm is not None:
            data.setdefault("chat", {})["show_swarm"] = req.show_swarm
        if req.members is not None:
            data["members"] = req.members

        yaml_path.write_text(_yaml.dump(data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
        projects.register(project_dir)  # neu einlesen
        audit_log("project.update", target=project_id, project_id=project_id)
        logger.info("Projekt aktualisiert: %s", project_id)
        return {"updated": True, "project_id": project_id}

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
        if not discovery.get(req.boss):
            raise HTTPException(422, f"Boss-Agent '{req.boss}' nicht in Discovery")

        project_dir = Path(projects_dir) / req.id
        project_dir.mkdir(parents=True, exist_ok=True)

        project_data = {
            "id": req.id,
            "version": "1.0.0",
            "identity": {"name": req.name, "description": req.description},
            "agents": {"boss": req.boss, "workers": req.workers},
            "matrix": {"room": ""},
            "filesystem": {"path": f"/projects/{req.id}", "samba": req.samba, "nfs": req.nfs},
            "system": {"user": f"proj_{req.id}", "group": f"proj_{req.id}"},
            "chat": {"show_swarm": req.show_swarm},
            "members": req.members,
            "github_repo": req.github_repo,
        }
        yaml_path = project_dir / "project.yaml"
        yaml_path.write_text(_yaml.dump(project_data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
        logger.info("project.yaml geschrieben: %s", yaml_path)
        audit_log("project.create", target=req.id, project_id=req.id, details={"boss": req.boss})

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
        execution_mode = resolve_request_execution_mode(
            auth,
            req.execution_mode,
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

        async def event_stream():
            async for chunk in orchestrator.handle_message_stream(
                project_id=project_id,
                project_cfg=cfg,
                content=_user_content,
                sender=sender,
                execution_mode=execution_mode,
            ):
                yield chunk

        return _SR(event_stream(), media_type="text/event-stream",
                   headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})

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
        execution_mode = resolve_request_execution_mode(
            auth,
            req.execution_mode,
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
