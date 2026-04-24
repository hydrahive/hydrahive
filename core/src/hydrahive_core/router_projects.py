from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Literal
import re

from fastapi import APIRouter, Depends, Header, HTTPException, Query, WebSocket, WebSocketDisconnect
from typing import Optional
from pydantic import BaseModel

from .execution_mode_policy import resolve_request_execution_mode
from .session_manager import MessageRole

# #554: Collab-Composer — gültige Room-Namen sind alphanumerisch plus "_-".
# Schützt den SQLite-Pfad vor Path-Traversal durch freie Projekt-IDs.
_COLLAB_ROOM_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+$")
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


# v2: Projekt-Erstellung mit Template (#592 erweitert: Provisioning + Messenger)
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
    # #592: Provisioning + Messenger
    samba: bool = True                    # Samba-Share + Linux-User anlegen
    nfs: bool = False
    execution_mode: str = "safe"
    github_repo: str = ""                 # "user/repo" oder Full-URL (leer = kein Repo)
    git_clone: bool = False               # Clone direkt nach Erstellung
    git_branch: str = "main"
    git_token: str = ""                   # Optional fuer private Repos
    messenger: dict = {}                  # {discord: {...}, telegram: {...}, whatsapp: {...}}


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
    max_tool_rounds: int | None = None  # Max Tool-Aufrufe pro Nachricht (#613)
    messenger: dict | None = None  # Discord/Telegram/Matrix Config (#569)
    risk_policy: str | None = None  # interactive | trusted — trusted nur durch Admin setzbar
    github_repo: str | None = None  # #859: nachträglich änderbar


# #641-Fix: ToolConfirmRequest MUSS Module-Scope sein, damit FastAPI mit
# `from __future__ import annotations` den String-Annotation-Hint auflösen
# kann. Lokal in register_project_routes definiert führte das zu
# `422 missing field 'req' in query` (FastAPI erkennt das BaseModel nicht
# und fällt auf Query-Parameter-Default zurück).
class ToolConfirmRequest(BaseModel):
    tool_call_id: str
    decision:     Literal["approve", "deny"]


# #584-A: Projekt-Target-Zuweisungen (Server + WKS)
class ProjectTargetServer(BaseModel):
    server_id: str
    role: str = ""
    note: str = ""


class ProjectTargetWks(BaseModel):
    username: str
    role: str = ""
    note: str = ""


class ProjectTargetsRequest(BaseModel):
    servers: list[ProjectTargetServer] = []
    wks:     list[ProjectTargetWks]    = []


class GitCloneRequest(BaseModel):
    url: str
    branch: str = "main"


class ProjectMemoryWriteRequest(BaseModel):
    filename: str
    content: str
    mode: Literal["append", "overwrite"] = "append"


def register_project_routes(
    auth_router: APIRouter,
    admin_router: APIRouter,
    public_router: APIRouter,
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
    invalidate_prompt_cache=None,
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
        """v2 (#592): Projekt erstellen mit Template + config.yaml + AGENT.md
        + Provisioning (Samba, Linux-User, Matrix) + optional Gitea-Repo + Messenger.

        Provisioning-Fehler sind nicht fatal — Config wird trotzdem geschrieben,
        User kann Setup manuell nachholen via POST /projects/{id}/provision."""
        import asyncio as _asyncio
        import subprocess
        import yaml as _yaml

        if not re.match(r"^[a-z0-9_-]+$", req.id):
            raise HTTPException(400, "Projekt-ID darf nur a-z, 0-9, _ und - enthalten")
        if projects.get(req.id):
            raise HTTPException(409, f"Projekt '{req.id}' existiert bereits")

        # Exec-Mode validieren
        exec_mode = req.execution_mode if req.execution_mode in ("safe", "elevated", "unrestricted") else "safe"

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
            "execution_mode": exec_mode,
            "filesystem": {
                "path": f"/projects/{req.id}",
                "samba": req.samba,
                "nfs": req.nfs,
            },
            "system": {"user": f"proj_{req.id}", "group": f"proj_{req.id}"},
            "plugins": [],
            "repos": [],
            "sources": [],
            "members": req.members or ["admin"],
            "github_repo": req.github_repo,
        }
        config_path = project_dir / "config.yaml"
        config_path.write_text(
            _yaml.dump(config_data, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )

        # #607: members → users.json.allowed_projects synchron halten (Codex-3 MEDIUM:
        # best-effort, Fehler geht in provision_warnings statt silent failure)
        _users_sync_error = ""
        _members_set = set(config_data.get("members") or [])
        if _members_set:
            try:
                from .main import _load_users, _save_users
                users = _load_users()
                for uname, udata in users.items():
                    ap = set(udata.get("allowed_projects") or [])
                    if uname in _members_set:
                        ap.add(req.id)
                        udata["allowed_projects"] = sorted(ap)
                _save_users(users)
            except Exception as _e:
                logger.error("Users-Sync bei Projekt-Creation fehlgeschlagen: %s", _e)
                _users_sync_error = str(_e)

        # AGENT.md — eigener Text oder aus Template
        agent_md_text = req.agent_md.strip() if req.agent_md else ""
        if not agent_md_text:
            # #609: settings.installer_dir statt hardcoded /opt/... — konsistent
            # mit der GET /templates API (router_core_misc.py)
            from .settings import settings as _s
            template_dir = _s.installer_dir / "templates" / req.template
            template_md = template_dir / "AGENT.md"
            if template_md.exists():
                agent_md_text = template_md.read_text(encoding="utf-8")
            else:
                agent_md_text = f"# {req.name}\n\nBeschreibe hier das Fachgebiet."

        agent_md_path = project_dir / "AGENT.md"
        agent_md_path.write_text(agent_md_text, encoding="utf-8")

        # messenger.yaml falls Daten vorhanden
        if req.messenger:
            messenger_path = project_dir / "messenger.yaml"
            messenger_path.write_text(
                _yaml.dump(req.messenger, allow_unicode=True, default_flow_style=False, sort_keys=False),
                encoding="utf-8",
            )

        # Berechtigungen
        subprocess.run(["chown", "-R", "hydrahive:hydrahive", str(project_dir)],
                       capture_output=True, timeout=5)

        # Im ProjectLoader registrieren
        await _asyncio.sleep(0.2)
        cfg = projects.get(req.id) or projects.register(project_dir)
        if cfg is None:
            raise HTTPException(500, "Projekt konnte nach Anlage nicht geladen werden")

        # Provisioning (Samba + Linux-User + Matrix) — nicht fatal bei Fehler
        provision_result = None
        provision_warnings: list[str] = []
        if _users_sync_error:
            provision_warnings.append(
                f"users.json-Sync fehlgeschlagen: {_users_sync_error}. "
                "Admin muss allowed_projects manuell in users.json ergaenzen "
                "oder PUT /projects/{id}/settings mit members erneut ausloesen."
            )
        try:
            provisioner = get_provisioner()
            if provisioner is not None:
                provision_result = await provisioner.provision(cfg)
                audit_log("project.provision", target=req.id, project_id=req.id)
                # Matrix-Room/Space in config.yaml nachtragen
                if provision_result and getattr(provision_result, "matrix_room", ""):
                    try:
                        update_project_matrix_room(req.id, provision_result.matrix_room)
                    except Exception as e:
                        logger.warning("Matrix-Room in config.yaml schreiben fehlgeschlagen: %s", e)
                if provision_result and getattr(provision_result, "matrix_space", ""):
                    try:
                        update_project_matrix_space(req.id, provision_result.matrix_space)
                    except Exception as e:
                        logger.warning("Matrix-Space in config.yaml schreiben fehlgeschlagen: %s", e)
            else:
                provision_warnings.append("Provisioner nicht initialisiert — Samba/Matrix nicht eingerichtet")
        except Exception as e:
            logger.warning("Provisioning fehlgeschlagen fuer %s: %s", req.id, e)
            provision_warnings.append(f"Provisioning: {e}")

        # Gitea-Repo erstellen (nur wenn github_repo gesetzt)
        gitea_repo_url = ""
        if req.github_repo:
            try:
                from .gitea import get_gitea_client
                gitea = get_gitea_client()
                repo = await gitea.create_repo(req.id, description=req.description or "")
                gitea_repo_url = repo.get("html_url", "")
                # Webhook anlegen fuer Projekt-Events
                try:
                    await gitea.create_webhook(req.id, f"http://127.0.0.1:8765/webhooks/gitea/{req.id}")
                except Exception as e:
                    logger.warning("Gitea-Webhook fehlgeschlagen: %s", e)
            except Exception as e:
                logger.warning("Gitea-Repo erstellen fehlgeschlagen: %s", e)
                provision_warnings.append(f"Gitea: {e}")

        # Git-Clone (wenn gewollt)
        if req.git_clone and req.github_repo:
            try:
                clone_url = req.github_repo.strip()
                if not clone_url.startswith("http"):
                    clone_url = f"https://github.com/{clone_url}"
                if not clone_url.endswith(".git"):
                    clone_url += ".git"
                if req.git_token.strip():
                    clone_url = clone_url.replace("https://", f"https://{req.git_token.strip()}@")
                files_dir = project_dir / "files"
                files_dir.mkdir(exist_ok=True)
                clone_result = subprocess.run(
                    ["git", "clone", "--branch", req.git_branch or "main", clone_url, str(files_dir / req.id)],
                    capture_output=True, text=True, timeout=120,
                )
                if clone_result.returncode != 0:
                    provision_warnings.append(f"Git-Clone: {clone_result.stderr[:200]}")
                else:
                    # #875: .git/ auf proj_<id>:hydrahive setzen
                    clone_target = files_dir / req.id
                    subprocess.run(
                        ["sudo", "chown", "-R", f"proj_{req.id}:hydrahive", str(clone_target)],
                        check=False, capture_output=True,
                    )
            except Exception as e:
                logger.warning("Git-Clone fehlgeschlagen: %s", e)
                provision_warnings.append(f"Git-Clone: {e}")

        # P4 (#614): Bootstrap-Memory nach Projekt-Anlage automatisch starten
        try:
            from .bootstrap_memory import bootstrap_project_memory as _bpm
            _asyncio.create_task(_bpm(req.id, project_dir))
        except Exception as _bpm_err:
            logger.warning("bootstrap_memory Task konnte nicht gestartet werden: %s", _bpm_err)

        audit_log("project.create_v2", target=req.id, project_id=req.id,
                  details={"template": req.template, "model": req.model,
                           "samba": req.samba, "has_messenger": bool(req.messenger),
                           "github_repo": bool(req.github_repo)})
        logger.info("v2-Projekt erstellt: %s (Template: %s, LLM: %s/%s, Warnings: %d)",
                    req.id, req.template, req.provider, req.model, len(provision_warnings))

        return {
            "created": True,
            "ok": True,
            "project_id": req.id,
            "version": "2.0.0",
            "template": req.template,
            "model": f"{req.provider}/{req.model}",
            "linux_user": getattr(provision_result, "linux_user", "") if provision_result else "",
            "files_dir":  getattr(provision_result, "files_dir",  "") if provision_result else "",
            "samba_share": getattr(provision_result, "samba_share", "") if provision_result else "",
            "matrix_room": getattr(provision_result, "matrix_room", "") if provision_result else "",
            "matrix_space": getattr(provision_result, "matrix_space", "") if provision_result else "",
            "gitea_repo": gitea_repo_url,
            "warnings": provision_warnings,
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
            "max_tool_rounds": getattr(cfg, "max_tool_rounds", 50),
            "risk_policy": getattr(cfg, "risk_policy", "interactive"),
            "plugins": getattr(cfg, "plugins", []),
            "repos": getattr(cfg, "repos", []),
            "sources": getattr(cfg, "sources", []),
            "github_repo": getattr(cfg, "github_repo", ""),
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

        if req.max_tool_rounds is not None:
            config_data["max_tool_rounds"] = max(1, min(200, req.max_tool_rounds))

        if req.risk_policy is not None:
            if req.risk_policy not in ("interactive", "trusted"):
                raise HTTPException(400, "risk_policy muss 'interactive' oder 'trusted' sein.")
            # Trusted ist eine bewusste Eskalation und nur durch Admins setzbar.
            if req.risk_policy == "trusted" and role != "admin":
                raise HTTPException(
                    403,
                    "risk_policy='trusted' darf nur durch Admins gesetzt werden.",
                )
            config_data["risk_policy"] = req.risk_policy

        # #859: github_repo nachträglich ändern
        if req.github_repo is not None:
            config_data["github_repo"] = req.github_repo

        config_data["version"] = "2.0.0"

        # #607/Codex-3: Strong Consistency — users.json ZUERST schreiben.
        # Bei Fehler 500 werfen BEVOR config.yaml geaendert wird. Damit ist
        # der Zustand atomar: entweder beides durch oder nichts.
        _warnings: list[str] = []
        if req.members is not None:
            try:
                from .main import _load_users, _save_users
                users = _load_users()
                req_members = set(req.members)
                for uname, udata in users.items():
                    ap = set(udata.get("allowed_projects") or [])
                    is_member = uname in req_members
                    if is_member:
                        ap.add(project_id)
                    else:
                        ap.discard(project_id)
                    udata["allowed_projects"] = sorted(ap)
                _save_users(users)
                logger.info("users.json synchronisiert (Projekt: %s, Members: %s)",
                            project_id, sorted(req_members))
            except Exception as _e:
                logger.error("Users-Sync fehlgeschlagen (config.yaml NICHT geaendert): %s", _e)
                raise HTTPException(
                    500,
                    f"users.json-Sync fehlgeschlagen: {_e}. "
                    "Keine Aenderung an config.yaml durchgefuehrt — Request bitte wiederholen. "
                    "Admin sollte /etc/hydrahive/users.json auf Schreibrechte pruefen."
                )

        # config.yaml schreiben (erst NACH erfolgreichem users.json-Sync)
        config_path.write_text(
            _yaml.dump(config_data, allow_unicode=True, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )

        # AGENT.md schreiben wenn im Request
        # #645 Phase 1e: Persona-Write-Guard — nur Admin + Personal-Projekt-Owner
        # dürfen AGENT.md über Settings ändern. Reguläre Project-Owner können
        # andere Settings weiter speichern, wenn sie dasselbe AGENT.md
        # mitsenden (No-Op); jede inhaltliche Änderung → 403.
        if req.agent_md is not None:
            agent_md_path = project_dir / "AGENT.md"
            caller_may_write_persona = is_admin or is_personal_owner
            current_md = (
                agent_md_path.read_text(encoding="utf-8") if agent_md_path.exists() else ""
            )
            if caller_may_write_persona:
                if req.agent_md != current_md:
                    agent_md_path.write_text(req.agent_md, encoding="utf-8")
                    if invalidate_prompt_cache is not None:
                        try:
                            invalidate_prompt_cache(project_id)
                        except Exception as e:  # pragma: no cover — defensiv
                            logger.warning("invalidate_prompt_cache fehlgeschlagen: %s", e)
            else:
                if req.agent_md != current_md:
                    raise HTTPException(
                        403,
                        "AGENT.md darf nur von Admin oder Personal-Projekt-Owner geändert werden.",
                    )
                # sonst: identischer Inhalt → kein Write, kein Fehler, kein Cache-Invalidate

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
        logger.info("v2-Projekt Settings aktualisiert: %s (warnings: %d)", project_id, len(_warnings))

        return {"updated": True, "project_id": project_id, "warnings": _warnings}

    # ── #584-A: Projekt-Target-Zuweisungen ─────────────────────────────

    def _hydrate_targets_response(project_id: str) -> dict:
        """Baut die GET-Response aus gespeicherten Targets + Stammdaten.
        Keine ssh_key_path, keine private keys im Output."""
        import json as _json
        from .project_targets import get_project_targets, compute_project_targets_etag
        from .router_servers import _load_servers, SERVERS_KEYS_DIR
        from .settings import settings as _settings

        raw = get_project_targets(project_id)
        server_lookup = {s["id"]: s for s in _load_servers()}
        try:
            users = _json.loads(_settings.users_config.read_text(encoding="utf-8"))
        except Exception:
            users = {}

        servers_out = []
        for t in raw["servers"]:
            srv = server_lookup.get(t["server_id"])
            if not srv:
                # Server wurde gelöscht, Zuweisung hängt → trotzdem ausgeben,
                # damit Admin den Stale-Eintrag sieht und entfernen kann.
                servers_out.append({
                    "server_id": t["server_id"],
                    "name": "", "ip": "", "ssh_user": "", "ssh_port": 22,
                    "role": t.get("role", ""), "note": t.get("note", ""),
                    "has_ssh_key": False, "stale": True,
                })
                continue
            servers_out.append({
                "server_id":  t["server_id"],
                "name":       srv.get("name", ""),
                "ip":         srv.get("ip", ""),
                "ssh_user":   srv.get("ssh_user", "root"),
                "ssh_port":   srv.get("ssh_port", 22),
                "role":       t.get("role", ""),
                "note":       t.get("note", ""),
                "has_ssh_key": (SERVERS_KEYS_DIR / t["server_id"]).exists(),
            })

        wks_out = []
        for t in raw["wks"]:
            user = users.get(t["username"]) or {}
            wks_entry = user.get("wks") or {}
            # #677: ssh_port backward-compatible aus users.json
            try:
                wks_ssh_port = int(wks_entry.get("ssh_port") or 22)
                if not (1 <= wks_ssh_port <= 65535):
                    wks_ssh_port = 22
            except (TypeError, ValueError):
                wks_ssh_port = 22
            wks_out.append({
                "username":   t["username"],
                "ip":         wks_entry.get("ip", ""),
                "ssh_user":   wks_entry.get("ssh_user", t["username"]),
                "ssh_port":   wks_ssh_port,
                "role":       t.get("role", ""),
                "note":       t.get("note", ""),
                "has_ssh_key": (_settings.wks_keys_dir / t["username"]).exists(),
            })

        return {
            "project_id": project_id,
            "etag":       compute_project_targets_etag(project_id),
            "servers":    servers_out,
            "wks":        wks_out,
        }

    def _require_targets_etag_match_strict(project_id: str, if_match: Optional[str]) -> None:
        """#676 Strict ETag-Guard für PUT /projects/{id}/targets.

        Fehlender Header → 428 mit `current_etag`, Mismatch → 409 mit
        `current_etag`. Shape {message, current_etag} konsistent zum
        Composer-Muster (#650) — Frontend kann Reload-Banner 1:1 wiederverwenden.
        """
        from .project_targets import compute_project_targets_etag
        current = compute_project_targets_etag(project_id)
        if if_match is None:
            raise HTTPException(
                status_code=428,
                detail={
                    "message":      "If-Match Header erforderlich. Aktuellen ETag aus GET /projects/{id}/targets laden.",
                    "current_etag": current,
                },
            )
        if if_match != current:
            raise HTTPException(
                status_code=409,
                detail={
                    "message":      "Projekt-Targets wurden seit dem Laden geändert. Bitte neu laden.",
                    "current_etag": current,
                },
            )

    @auth_router.get("/projects/{project_id}/targets")
    def get_project_targets_endpoint(
        project_id: str,
        auth: tuple[str, str] = Depends(require_auth),
    ):
        """Liefert Projekt-Targets + Stammdaten (read). Project-Access-Check."""
        _check_project_access(auth, project_id)
        if not projects.get(project_id):
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
        return _hydrate_targets_response(project_id)

    @admin_router.put("/projects/{project_id}/targets")
    def put_project_targets_endpoint(
        project_id: str,
        req: ProjectTargetsRequest,
        if_match: Optional[str] = Header(None, alias="If-Match"),
    ):
        """Setzt Projekt-Targets (admin-only V1). Validiert Stammdaten-Existenz.

        #676: If-Match ist strict Pflicht. Fehlend → 428, stale → 409 (jeweils
        mit current_etag im detail). Konsistent zum Composer-Muster.
        """
        import json as _json
        from .project_targets import set_project_targets, TargetValidationError
        from .router_servers import _load_servers
        from .settings import settings as _settings

        if not projects.get(project_id):
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")

        _require_targets_etag_match_strict(project_id, if_match)

        existing_servers = {s["id"] for s in _load_servers()}
        try:
            users = _json.loads(_settings.users_config.read_text(encoding="utf-8"))
        except Exception:
            users = {}

        # Stammdaten-Existenz prüfen
        for s in req.servers:
            if s.server_id not in existing_servers:
                raise HTTPException(404, f"Server '{s.server_id}' nicht gefunden")
        for w in req.wks:
            if w.username not in users:
                raise HTTPException(404, f"User '{w.username}' nicht gefunden")
            wks_entry = (users[w.username] or {}).get("wks") or {}
            if not (wks_entry.get("ip") or "").strip():
                raise HTTPException(
                    400,
                    f"WKS für User '{w.username}' ist nicht konfiguriert (keine IP).",
                )

        try:
            set_project_targets(project_id, req.model_dump())
        except TargetValidationError as e:
            raise HTTPException(400, str(e))

        audit_log("project.targets_update", target=project_id, project_id=project_id,
                  details={"servers": len(req.servers), "wks": len(req.wks)})
        logger.info(
            "Projekt-Targets aktualisiert: %s (servers=%d, wks=%d)",
            project_id, len(req.servers), len(req.wks),
        )
        if invalidate_prompt_cache is not None:
            try:
                invalidate_prompt_cache(project_id)
            except Exception as e:
                logger.warning("invalidate_prompt_cache fehlgeschlagen: %s", e)
        return _hydrate_targets_response(project_id)

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
            "llm": {"provider": "anthropic", "model": req.model, "temperature": 0.7, "max_tokens": 4096},
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
            # #875: .git/ auf proj_<project_id>:hydrahive setzen
            _sp.run(["sudo", "chown", "-R", f"proj_{project_id}:hydrahive", str(target / ".git")], check=False, capture_output=True)
            _sp.run(["sudo", "chown", "-R", f"proj_{project_id}:hydrahive", str(target)], check=False, capture_output=True)
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

    @auth_router.delete("/projects/{project_id}/session")
    async def delete_session(project_id: str, auth: tuple[str, str] = Depends(require_auth)):
        """Beendet die aktive Session — wird von /clear im Frontend aufgerufen."""
        _check_project_access(auth, project_id)
        if not projects.get(project_id):
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
        session = await sessions.end_session(project_id)
        if not session:
            return {"ended": False}
        return {"ended": True, "session_id": session.id, "message_count": len(session.messages)}

    @auth_router.post("/projects/{project_id}/session/start")
    async def start_session(project_id: str, auth: tuple[str, str] = Depends(require_auth)):
        _check_project_access(auth, project_id)
        if not projects.get(project_id):
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")

        # #313: Scratchpad bei neuer Session automatisch leeren
        _cfg = projects.get(project_id)
        _agent_id: str | None = _cfg.agents.boss if _cfg else None
        if _agent_id:
            try:
                from .scratchpad_service import clear_scratchpad
                clear_scratchpad(_agent_id)
            except Exception:
                pass   # Scratchpad ist flüchtig — Fehler nicht kritisch

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
            # as_history_message() bringt metadata (input_tokens / output_tokens /
            # cache_*_tokens / model) mit — sonst fehlen Token-Badges im Frontend
            # nach Resume einer alten Session.
            "messages": [m.as_history_message() for m in session.messages],
        }

    # ──────────────────────────────────────────────────────────────────────
    # #641: CONFIRM-Round-Trip — Auflösen pendinger Tool-Bestätigungen
    # (ToolConfirmRequest ist auf Module-Scope definiert, siehe oben)
    # ──────────────────────────────────────────────────────────────────────

    @auth_router.post("/projects/{project_id}/sessions/{session_id}/tool-confirm")
    def resolve_tool_confirm(
        project_id: str,
        session_id: str,
        req: ToolConfirmRequest,
        auth: tuple[str, str] = Depends(require_auth),
    ):
        """Löst eine pendingnde RiskLevel.CONFIRM-Anfrage auf.

        - approve: laufender Tool-Call wird nach dem Wait normal ausgeführt
        - deny:    Tool-Call wird abgebrochen, LLM bekommt risk: confirm_denied
        - 404:     keine pending Anfrage für (session_id, tool_call_id)
        - 409:     Anfrage wurde bereits aufgelöst (Doppel-Klick / Race)
        """
        _check_project_access(auth, project_id)
        if not projects.get(project_id):
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
        from .tool_confirmation import resolve_confirmation
        outcome = resolve_confirmation(session_id, req.tool_call_id, req.decision)
        if outcome == "not_found":
            raise HTTPException(404, "Keine pending Tool-Bestätigung für diese (session_id, tool_call_id)")
        if outcome == "already_resolved":
            raise HTTPException(409, "Tool-Bestätigung wurde bereits aufgelöst")
        return {"resolved": True, "decision": req.decision, "tool_call_id": req.tool_call_id}

    @auth_router.get("/projects/{project_id}/sessions/{session_id}/tool-confirms")
    def list_tool_confirms(
        project_id: str,
        session_id: str,
        auth: tuple[str, str] = Depends(require_auth),
    ):
        """Listet alle aktuell pending Tool-Bestätigungen einer Session.
        Für Frontend-Polling-Fallback (non-stream-Pfad ohne SSE-Bridge)."""
        _check_project_access(auth, project_id)
        if not projects.get(project_id):
            raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
        from .tool_confirmation import get_pending
        return {"pending": get_pending(session_id)}

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
        request_user = auth[0] if auth[0] != "internal" else None
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
        import uuid as _uuid

        if not _ss.acquire_turn(project_id, sender):
            _turn_owner = _ss.turn_owner(project_id)
            raise HTTPException(
                409, f"Projekt ist gerade belegt von '{_turn_owner}'. Bitte warten."
            )

        # #726 K2c: Replay-Buffer starten. Muss VOR dem _user_message-Broadcast
        # passieren, damit late-joiner auch die User-Nachricht im Priming sehen.
        _stream_id = _uuid.uuid4().hex[:8]
        _ss.start_stream(project_id, _stream_id)

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
                    request_user=request_user,
                ):
                    # Chunk an den sendenden Client
                    yield chunk
                    # Chunk an alle anderen Subscriber broadcasten
                    # SSE-Format: "data: {...}\n\n" → JSON extrahieren
                    if chunk.startswith("data: "):
                        _ss.broadcast(project_id, chunk[6:].strip())
            finally:
                _ss.release_turn(project_id, sender)
                _ss.end_stream(project_id)

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

    # #554: Collaborative Composer — WebSocket zu einem per-Projekt Yjs-Doc.
    # Mehrere User teilen den Composer-Text live (screen-x für Prompts).
    # Auth: Cookie (httpOnly, vom _AuthCookieMiddleware injected) zuerst,
    # Query-Param als Fallback für Rückkompatibilität. Token wird VOR
    # websocket.accept() validiert — sonst verliert Yjs die Initial-Sync-Messages.
    @public_router.websocket("/projects/{project_id}/collab")
    async def collab_ws(
        websocket: WebSocket,
        project_id: str,
        # #766: Optional — Cookie hat Priorität, Query-Param nur Fallback.
        token: str | None = Query(default=None, description="JWT-WS-Ticket oder Session-Cookie (Query-Fallback)"),
    ):
        from .collab_yjs import FastApiWsChannel, get_yjs_server, record_yjs_debug_event
        from .auth_utils import AUTH_COOKIE_NAME

        # Room-Name strikt validieren — verhindert Path-Traversal in der
        # SQLite-Datei, da project_id in den Dateinamen einfließt.
        if not _COLLAB_ROOM_NAME_RE.match(project_id):
            await websocket.close(code=4400, reason="Ungültige Projekt-ID")
            return

        # #766: Cookie zuerst (httpOnly, vom Browser automatisch im WS-Handshake
        # gesendet), Query-Param als Fallback für Rückkompatibilität.
        # Cookie-JWT = reguläres Session-JWT (kein aud="websocket" Claim).
        # Query-Param = kurzlebiges WS-Ticket mit aud="websocket".
        cookie_token = websocket.cookies.get(AUTH_COOKIE_NAME)
        if cookie_token:
            # Reguläres Session-JWT aus Cookie — kein aud-Check nötig.
            raw_token = cookie_token
            required_aud: str | None = None
        elif token:
            # WS-Ticket via Query-Param — muss aud="websocket" haben.
            raw_token = token
            required_aud = "websocket"
        else:
            await websocket.close(code=1008, reason="Auth fehlgeschlagen — kein Cookie/Token")
            return

        # Auth vor accept — sonst gehen Yjs-Initial-Messages verloren.
        # _verify_jwt kommt aus main.py; Lazy-Import gegen Zirkularität.
        from .main import _verify_jwt, _load_users as _lu
        try:
            username, role = _verify_jwt(raw_token, required_aud=required_aud)
        except HTTPException:
            await websocket.close(code=4401, reason="Auth fehlgeschlagen")
            return
        if username not in _lu():
            await websocket.close(code=4401, reason="User unbekannt")
            return
        try:
            _check_project_access((username, role), project_id)
        except HTTPException:
            await websocket.close(code=4403, reason="Kein Projektzugang")
            return

        server = get_yjs_server()
        if server is None:
            await websocket.close(code=4503, reason="Yjs-Server nicht gestartet")
            return

        await websocket.accept()
        channel = FastApiWsChannel(websocket, path=project_id, label=f"{project_id}/{username}")
        logger.info(
            "Collab-WS connected: user=%s room=%s clients_before=%d",
            username,
            project_id,
            len(server.rooms.get(project_id).clients) if project_id in server.rooms else 0,
        )
        record_yjs_debug_event(
            "serve_enter",
            room=project_id,
            label=f"{project_id}/{username}",
            clients_before=len(server.rooms.get(project_id).clients) if project_id in server.rooms else 0,
        )
        try:
            await server.serve(channel)
            record_yjs_debug_event("serve_return", room=project_id, label=f"{project_id}/{username}")
            logger.info("Collab-WS serve returned normally for %s/%s", project_id, username)
        except WebSocketDisconnect as e:
            record_yjs_debug_event("serve_disconnect", room=project_id, label=f"{project_id}/{username}", code=e.code)
            logger.info("Collab-WS client disconnected: %s/%s code=%s", project_id, username, e.code)
        except Exception as e:
            # WICHTIG: pycrdt's serve unterdrückt Exceptions via exception_handler,
            # aber FastAPI/ASGI-Fehler kommen hier an. Loggen damit wir sehen
            # warum der WS stirbt (#554 Debug).
            record_yjs_debug_event("serve_exception", room=project_id, label=f"{project_id}/{username}", error=repr(e))
            logger.exception("Collab-WS serve error for project %s user %s", project_id, username)
        finally:
            try:
                await websocket.close()
            except Exception:
                pass
            logger.info(
                "Collab-WS disconnected: user=%s room=%s clients_after=%d",
                username,
                project_id,
                len(server.rooms.get(project_id).clients) if project_id in server.rooms else 0,
            )
            record_yjs_debug_event(
                "serve_finally",
                room=project_id,
                label=f"{project_id}/{username}",
                clients_after=len(server.rooms.get(project_id).clients) if project_id in server.rooms else 0,
            )

    # #554 Debug: Collab-Room-State einsehen
    @auth_router.get("/projects/{project_id}/collab/state")
    def collab_state(project_id: str, _auth: tuple[str, str] = Depends(require_auth)):
        _check_project_access(_auth, project_id)
        from .collab_yjs import get_yjs_debug_events, get_yjs_server
        server = get_yjs_server()
        if server is None:
            return {"server": "not_started", "debug_events": get_yjs_debug_events(room=project_id)}
        room = server.rooms.get(project_id)
        if room is None:
            return {
                "server": "up",
                "room": None,
                "active_rooms": list(server.rooms.keys()),
                "debug_events": get_yjs_debug_events(room=project_id),
            }
        # paths aus den clients ziehen (Channel-Protokoll hat .path)
        clients = [getattr(c, "_label", getattr(c, "path", "?")) for c in room.clients]
        ydoc_text = ""
        try:
            from pycrdt import Text as _Text
            ydoc_text = str(room.ydoc.get("composer", type=_Text))
        except Exception as e:
            ydoc_text = f"<err: {e}>"
        return {
            "server": "up",
            "room": project_id,
            "client_count": len(room.clients),
            "clients": clients,
            "ydoc_composer_text": ydoc_text,
            "ydoc_composer_len": len(ydoc_text),
            "debug_events": get_yjs_debug_events(room=project_id),
        }

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
        request_user = auth[0] if auth[0] != "internal" else None
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
            request_user=request_user,
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

    # ── Bootstrap-Memory (#614) ────────────────────────────────────────────────

    @auth_router.post("/projects/{project_id}/bootstrap-memory", status_code=202)
    async def trigger_bootstrap_memory(
        project_id: str,
        force: bool = False,
        _auth: tuple[str, str] = Depends(require_auth),
    ):
        """P1: Bootstrap-Memory on-demand (User-Button in Settings).
        Startet async, gibt 202 zurück. force=true überschreibt bestehendes Bootstrap."""
        _check_project_access(_auth, project_id)
        cfg = projects.get(project_id)
        if not cfg:
            raise HTTPException(404, "Projekt nicht gefunden")

        from .bootstrap_memory import is_bootstrap_done, bootstrap_project_memory
        project_dir = Path(projects_dir) / project_id

        if not force and is_bootstrap_done(project_dir):
            return {"ok": True, "skipped": True, "reason": "Bootstrap bereits erledigt (force=true zum Wiederholen)"}

        # Async starten — Client pollt GET endpoint
        async def _run():
            result = await bootstrap_project_memory(project_id, project_dir, force=force)
            logger.info("bootstrap_memory abgeschlossen: %s → %s", project_id, result)

        asyncio.create_task(_run())
        return {"ok": True, "skipped": False, "started": True, "project_id": project_id}

    @auth_router.get("/projects/{project_id}/bootstrap-memory")
    def get_bootstrap_memory_status(
        project_id: str,
        _auth: tuple[str, str] = Depends(require_auth),
    ):
        """P1: Bootstrap-Memory Status abfragen."""
        _check_project_access(_auth, project_id)
        cfg = projects.get(project_id)
        if not cfg:
            raise HTTPException(404, "Projekt nicht gefunden")

        from .bootstrap_memory import is_bootstrap_done
        project_dir = Path(projects_dir) / project_id
        memory_dir = project_dir / "memory"

        done = is_bootstrap_done(project_dir)
        memory_files = []
        if memory_dir.exists():
            memory_files = sorted(f.name for f in memory_dir.iterdir() if f.is_file() and not f.name.startswith("."))

        return {
            "project_id": project_id,
            "bootstrap_done": done,
            "memory_files": memory_files,
            "memory_file_count": len(memory_files),
        }

    # ── Memory-Stats (#614 P2) ─────────────────────────────────────────────────

    @auth_router.get("/projects/{project_id}/memory/stats")
    def get_memory_stats(
        project_id: str,
        _auth: tuple[str, str] = Depends(require_auth),
    ):
        """P2: Memory-Statistiken — Dateiliste, Größen, letztes Update."""
        _check_project_access(_auth, project_id)
        cfg = projects.get(project_id)
        if not cfg:
            raise HTTPException(404, "Projekt nicht gefunden")

        import os as _os
        project_dir = Path(projects_dir) / project_id
        memory_dir = project_dir / "memory"

        if not memory_dir.exists():
            return {"project_id": project_id, "total_bytes": 0, "file_count": 0, "files": []}

        files_info = []
        total_bytes = 0
        for f in sorted(memory_dir.iterdir()):
            if not f.is_file() or f.name.startswith("."):
                continue
            try:
                st = f.stat()
                files_info.append({
                    "name": f.name,
                    "size_bytes": st.st_size,
                    "modified_at": int(st.st_mtime),
                })
                total_bytes += st.st_size
            except Exception:
                pass

        from .bootstrap_memory import is_bootstrap_done
        return {
            "project_id": project_id,
            "bootstrap_done": is_bootstrap_done(project_dir),
            "total_bytes": total_bytes,
            "file_count": len(files_info),
            "files": files_info,
        }

    # ── Project Memory Write (#852) ─────────────────────────────────────────────
    class ProjectMemoryWriteRequest(BaseModel):
        filename: str
        content: str
        mode: Literal["append", "overwrite"] = "append"

    def write_project_memory_file(
        *,
        project_id: str,
        filename: str,
        content: str,
        mode: str = "append",
        projects_dir: str,
    ) -> dict:
        """Write project memory to projects/{id}/memory/{filename}.md."""
        import re as _re

        safe_name = filename.strip().removesuffix(".md")
        if not _re.match(r"^[a-zA-Z0-9_-]{1,64}$", safe_name):
            raise HTTPException(400, "Ungültiger Dateiname (nur a-z, 0-9, -, _, max 64 Zeichen)")

        clean_content = content.strip()
        if not clean_content:
            raise HTTPException(400, "content fehlt")

        project_dir = Path(projects_dir) / project_id
        memory_dir = project_dir / "memory"
        memory_dir.mkdir(parents=True, exist_ok=True)
        target = memory_dir / f"{safe_name}.md"

        if mode == "append":
            target.open("a", encoding="utf-8").write(clean_content)
        else:
            target.open("w", encoding="utf-8").write(clean_content)

        target.chmod(0o600)
        return {"ok": True, "path": str(target), "bytes": len(clean_content.encode())}

    @auth_router.post("/projects/{project_id}/memory", status_code=201)
    def write_project_memory(
        project_id: str,
        req: ProjectMemoryWriteRequest,
        _auth: tuple[str, str] = Depends(require_auth),
    ):
        """POST /projects/{id}/memory — external memory write."""
        _check_project_access(_auth, project_id)
        cfg = projects.get(project_id)
        if not cfg:
            raise HTTPException(404, "Projekt nicht gefunden")
        return write_project_memory_file(
            project_id=project_id,
            filename=req.filename,
            content=req.content,
            mode=req.mode,
            projects_dir=projects_dir,
        )

    # ── WhatsApp pro Projekt (#615) ────────────────────────────────────────────

    from pydantic import BaseModel as _BM

    class _WhatsAppCfgReq(_BM):
        private_chats_enabled: bool | None = None
        group_chats_enabled: bool | None = None
        require_keyword: str | None = None
        allowed_numbers: list[str] | None = None
        blocked_numbers: list[str] | None = None
        owner_numbers: list[str] | None = None
        voice_mode: str | None = None
        voice_name: str | None = None

    @auth_router.get("/projects/{project_id}/whatsapp")
    async def get_project_whatsapp(
        project_id: str,
        _auth: tuple[str, str] = Depends(require_auth),
    ):
        """Status + Config der WhatsApp-Session des Projekts."""
        _check_project_access(_auth, project_id)
        if not projects.get(project_id):
            raise HTTPException(404, "Projekt nicht gefunden")

        from .whatsapp_agent import bridge_get_status, load_whatsapp_config
        cfg = load_whatsapp_config(project_id)
        if not cfg or not cfg.get("enabled"):
            return {
                "project_id": project_id,
                "configured": False,
                "status": "disconnected",
                "qr": None,
                "phone": None,
                "private_chats_enabled": (cfg or {}).get("private_chats_enabled", True),
                "group_chats_enabled":   (cfg or {}).get("group_chats_enabled", False),
                "require_keyword":       (cfg or {}).get("require_keyword", ""),
                "allowed_numbers":       (cfg or {}).get("allowed_numbers", []),
                "blocked_numbers":       (cfg or {}).get("blocked_numbers", []),
                "owner_numbers":         (cfg or {}).get("owner_numbers", []),
                "voice_mode":            (cfg or {}).get("voice_mode", "never"),
                "voice_name":            (cfg or {}).get("voice_name", "de-DE-KatjaNeural"),
            }

        bridge = await bridge_get_status(project_id)
        bridge_phone = bridge.get("phone") or ""
        # Telefonnummer in Config speichern (Loop-Schutz)
        if bridge_phone and cfg.get("phone") != bridge_phone:
            from .whatsapp_agent import save_whatsapp_config as _save_wa
            cfg["phone"] = bridge_phone
            _save_wa(project_id, cfg)

        return {
            "project_id": project_id,
            "configured": True,
            "status": bridge.get("status", "disconnected"),
            "qr": bridge.get("qr"),
            "bridge_error": bridge.get("error") or None,
            "phone": bridge_phone or cfg.get("phone", ""),
            "private_chats_enabled": cfg.get("private_chats_enabled", True),
            "group_chats_enabled":   cfg.get("group_chats_enabled", False),
            "require_keyword":       cfg.get("require_keyword", ""),
            "allowed_numbers":       cfg.get("allowed_numbers", []),
            "blocked_numbers":       cfg.get("blocked_numbers", []),
            "owner_numbers":         cfg.get("owner_numbers", []),
            "voice_mode":            cfg.get("voice_mode", "never"),
            "voice_name":            cfg.get("voice_name", "de-DE-KatjaNeural"),
        }

    @auth_router.post("/projects/{project_id}/whatsapp/connect")
    async def connect_project_whatsapp(
        project_id: str,
        _auth: tuple[str, str] = Depends(require_auth),
    ):
        """Startet eine eigene WhatsApp-Session für dieses Projekt.

        Jedes Projekt bekommt damit seine eigene Handynummer — keine
        geteilten Sessions mehr zwischen Projekten (#615).
        """
        _check_project_access(_auth, project_id)
        if not projects.get(project_id):
            raise HTTPException(404, "Projekt nicht gefunden")

        from .whatsapp_agent import (
            bridge_start_session,
            load_whatsapp_config,
            save_whatsapp_config,
        )
        existing = load_whatsapp_config(project_id) or {}
        existing["enabled"] = True
        save_whatsapp_config(project_id, existing)
        result = await bridge_start_session(project_id)
        audit_log("whatsapp.connect", target=project_id, project_id=project_id)
        return {
            "configured": True,
            "status": result.get("status"),
            "qr": result.get("qr"),
            "phone": result.get("phone"),
        }

    @auth_router.delete("/projects/{project_id}/whatsapp")
    async def disconnect_project_whatsapp(
        project_id: str,
        _auth: tuple[str, str] = Depends(require_auth),
    ):
        """Trennt die WhatsApp-Session des Projekts und löscht die Config."""
        _check_project_access(_auth, project_id)
        if not projects.get(project_id):
            raise HTTPException(404, "Projekt nicht gefunden")

        from .whatsapp_agent import bridge_disconnect, delete_whatsapp_config
        result = await bridge_disconnect(project_id)
        delete_whatsapp_config(project_id)
        audit_log("whatsapp.disconnect", target=project_id, project_id=project_id)
        return {"disconnected": True, "bridge_result": result}

    @auth_router.put("/projects/{project_id}/whatsapp/config")
    async def update_project_whatsapp_config(
        project_id: str,
        req: _WhatsAppCfgReq,
        _auth: tuple[str, str] = Depends(require_auth),
    ):
        """Filter- und Voice-Config des Projekts aktualisieren."""
        _check_project_access(_auth, project_id)
        if not projects.get(project_id):
            raise HTTPException(404, "Projekt nicht gefunden")

        from .whatsapp_agent import load_whatsapp_config, save_whatsapp_config
        cfg = load_whatsapp_config(project_id) or {"enabled": True}
        for k, v in req.model_dump(exclude_none=True).items():
            cfg[k] = v
        save_whatsapp_config(project_id, cfg)
        return {"updated": True}
