"""
main.py — OctopOS Core Runtime Einstiegspunkt (#4, #6, #7, #8, #9, #10, #11)

FastAPI-App mit Lifespan-Management:
- AgentDiscovery + AgentRuntime + ProjectLoader + SessionManager + Orchestrator
- REST-Endpoints fuer Agenten, Projekte, Sessions und Nachrichten
"""

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .agent_config import AgentConfig
from .agent_discovery import AgentDiscovery
from .agent_runtime import AgentRuntime
from .orchestrator import Orchestrator
from .project_config import ProjectConfig
from .project_loader import ProjectLoader
from .provisioner import Provisioner, get_admin_access_token
from .session_manager import MessageRole, SessionManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

AGENTS_DIR   = "/agents"
PROJECTS_DIR = "/projects"

CRED_FILE   = "/etc/octopos/admin_credentials"

discovery    = AgentDiscovery(AGENTS_DIR)
runtime      = AgentRuntime()
projects     = ProjectLoader(PROJECTS_DIR)
sessions     = SessionManager(PROJECTS_DIR)
orchestrator = Orchestrator(discovery, runtime, sessions)
provisioner: Provisioner | None = None   # initialisiert im Lifespan


@asynccontextmanager
async def lifespan(app: FastAPI):
    global provisioner
    logger.info("OctopOS Core startet...")
    discovery.start()
    projects.start()
    sessions.start()
    await runtime.start(list(discovery.agents.values()))

    # Admin-Token für Matrix-Operationen holen
    try:
        cred_lines = {}
        for line in open(CRED_FILE).read().splitlines():
            if "=" in line:
                k, v = line.split("=", 1)
                cred_lines[k.strip()] = v.strip()
        admin_pass   = cred_lines.get("matrix_admin_password", "")
        server_name  = _read_server_name()
        access_token = await get_admin_access_token(admin_pass, server_name)
        provisioner  = Provisioner(access_token, server_name)
        logger.info("Matrix Admin-Token geladen (server: %s)", server_name)
    except Exception as e:
        logger.warning("Matrix Admin-Token konnte nicht geladen werden: %s", e)
        provisioner = Provisioner("", "your-hostname")

    logger.info("OctopOS Core bereit")
    yield
    logger.info("OctopOS Core faehrt herunter...")
    await runtime.stop()
    projects.stop()
    discovery.stop()
    logger.info("OctopOS Core gestoppt")


def _read_server_name(toml_path: str = "/etc/conduwuit/conduwuit.toml") -> str:
    try:
        for line in open(toml_path).read().splitlines():
            if line.strip().startswith("server_name"):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return "your-hostname"


app = FastAPI(
    title="OctopOS Core",
    version="0.1.0",
    lifespan=lifespan,
)


# ================================================================== Agenten

@app.get("/health")
def health():
    return {"status": "ok", "service": "octopos-core"}


@app.get("/agents")
def list_agents():
    registered = discovery.agents
    running    = runtime.status_all()
    return {
        agent_id: {
            "config": {
                "type":     cfg.type,
                "identity": cfg.identity,
                "model":    cfg.llm.model,
            },
            "runtime": running.get(agent_id),
        }
        for agent_id, cfg in registered.items()
    }


@app.get("/agents/{agent_id}")
def get_agent(agent_id: str):
    cfg = discovery.get(agent_id)
    if not cfg:
        raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")
    return {
        "config":  cfg.model_dump(exclude={"agent_dir"}),
        "runtime": runtime.status_all().get(agent_id),
    }


class SpawnRequest(BaseModel):
    agent_id: str


@app.post("/agents/spawn")
async def spawn_task_agent(req: SpawnRequest):
    cfg = discovery.get(req.agent_id)
    if not cfg:
        raise HTTPException(404, f"Agent '{req.agent_id}' nicht in Discovery")
    if cfg.type != "worker":
        raise HTTPException(400, f"Nur worker koennen gespawnt werden, nicht {cfg.type}")
    await runtime.spawn_task_agent(cfg)
    return {"spawned": req.agent_id}


@app.post("/agents/{agent_id}/heartbeat")
def agent_heartbeat(agent_id: str):
    runtime.heartbeat(agent_id)
    return {"ok": True}


# ================================================================== Projekte

@app.get("/projects")
def list_projects():
    return {
        pid: {
            "name":        cfg.identity.name,
            "description": cfg.identity.description,
            "boss":        cfg.agents.boss,
            "workers":     cfg.agents.workers,
            "matrix_room": cfg.matrix.room,
            "filesystem":  cfg.effective_filesystem_path(),
            "system_user": cfg.effective_system_user(),
            "show_swarm":  cfg.chat.show_swarm,
        }
        for pid, cfg in projects.projects.items()
    }


@app.get("/projects/{project_id}")
def get_project(project_id: str):
    cfg = projects.get(project_id)
    if not cfg:
        raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
    known = set(discovery.agents.keys())
    missing = [a for a in cfg.all_agents if a not in known]
    return {
        "config":          cfg.model_dump(exclude={"project_dir"}),
        "missing_agents":  missing,
        "system_user":     cfg.effective_system_user(),
        "filesystem_path": cfg.effective_filesystem_path(),
    }


@app.get("/projects/{project_id}/agents")
def project_agents(project_id: str):
    cfg = projects.get(project_id)
    if not cfg:
        raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
    running = runtime.status_all()
    return {
        agent_id: {
            "role":    "boss" if agent_id == cfg.agents.boss else "worker",
            "found":   discovery.get(agent_id) is not None,
            "runtime": running.get(agent_id),
        }
        for agent_id in cfg.all_agents
    }


# ================================================================== Sessions

@app.get("/projects/{project_id}/session")
def get_session(project_id: str):
    """Aktive Session eines Projekts."""
    if not projects.get(project_id):
        raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
    session = sessions.get_active(project_id)
    if not session:
        return {"active": False, "session": None}
    return {
        "active":      True,
        "session_id":  session.id,
        "started_at":  session.started_at,
        "message_count": len(session.messages),
    }


@app.post("/projects/{project_id}/session/start")
def start_session(project_id: str):
    """Neue Session starten (beendet ggf. vorherige)."""
    if not projects.get(project_id):
        raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
    session = sessions.new_session(project_id)
    return {"session_id": session.id, "started_at": session.started_at}


@app.post("/projects/{project_id}/session/end")
def end_session(project_id: str):
    """Aktive Session beenden."""
    if not projects.get(project_id):
        raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
    session = sessions.end_session(project_id)
    if not session:
        return {"ended": False}
    return {"ended": True, "session_id": session.id, "message_count": len(session.messages)}


class MessageRequest(BaseModel):
    role:     str
    content:  str
    agent_id: str | None = None


@app.post("/projects/{project_id}/session/message")
def append_message(project_id: str, req: MessageRequest):
    """Nachricht an aktive Session anhängen (Session wird ggf. angelegt)."""
    if not projects.get(project_id):
        raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
    try:
        role = MessageRole(req.role)
    except ValueError:
        raise HTTPException(400, f"Ungültige Rolle: {req.role}. Erlaubt: user, assistant, system, tool")
    msg = sessions.append(project_id, role, req.content, agent_id=req.agent_id)
    session = sessions.get_active(project_id)
    return {
        "appended":      True,
        "session_id":    session.id if session else None,
        "message_count": len(session.messages) if session else 1,
        "timestamp":     msg.timestamp,
    }


@app.get("/projects/{project_id}/session/history")
def session_history(project_id: str, limit: int = 50):
    """Nachrichten-History der aktiven Session."""
    if not projects.get(project_id):
        raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
    context = sessions.get_context(project_id, max_messages=limit)
    session = sessions.get_active(project_id)
    return {
        "session_id": session.id if session else None,
        "messages":   context,
        "count":      len(context),
    }


# ================================================================== Orchestrator

class IncomingMessage(BaseModel):
    content: str
    sender:  str = "user"


@app.post("/projects/{project_id}/message")
async def send_message(project_id: str, req: IncomingMessage):
    """
    User-Nachricht an Projekt senden — Boss-Agent verarbeitet und antwortet.
    Das ist der Haupt-Einstiegspunkt für die Web-Chat-UI und Matrix-Integration.
    """
    cfg = projects.get(project_id)
    if not cfg:
        raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")

    boss_id = cfg.agents.boss
    if not discovery.get(boss_id):
        raise HTTPException(503, f"Boss-Agent '{boss_id}' nicht in Discovery")

    response = await orchestrator.handle_message(
        project_id=project_id,
        project_cfg=cfg,
        content=req.content,
        sender=req.sender,
    )
    session = sessions.get_active(project_id)
    return {
        "response":      response,
        "session_id":    session.id if session else None,
        "message_count": len(session.messages) if session else 0,
    }


# ================================================================== Provisioning

@app.post("/projects/{project_id}/provision")
async def provision_project(project_id: str):
    """
    Projekt provisionieren: Linux-User + Samba-Share + Matrix-Room.
    Idempotent — kann mehrfach aufgerufen werden.
    """
    cfg = projects.get(project_id)
    if not cfg:
        raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
    if provisioner is None:
        raise HTTPException(503, "Provisioner nicht initialisiert")

    result = await provisioner.provision(cfg)

    # project.yaml mit Matrix-Room-ID aktualisieren wenn angelegt
    if result.matrix_room and not cfg.matrix.room:
        _update_project_matrix_room(project_id, result.matrix_room)

    return {
        "project_id":  result.project_id,
        "linux_user":  result.linux_user,
        "files_dir":   result.files_dir,
        "samba_share": result.samba_share,
        "matrix_room": result.matrix_room,
        "warnings":    result.warnings,
        "ok":          result.ok,
    }


@app.delete("/projects/{project_id}/provision")
async def deprovision_project(project_id: str):
    """Projekt-Ressourcen entfernen (User, Samba-Share)."""
    cfg = projects.get(project_id)
    if not cfg:
        raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
    if provisioner is None:
        raise HTTPException(503, "Provisioner nicht initialisiert")

    warnings = await provisioner.deprovision(cfg)
    return {"project_id": project_id, "deprovisioned": True, "warnings": warnings}


def _update_project_matrix_room(project_id: str, room_id: str) -> None:
    """Matrix-Room-ID in project.yaml zurückschreiben."""
    import re
    project_yaml = Path(PROJECTS_DIR) / project_id / "project.yaml"
    if not project_yaml.exists():
        return
    try:
        content = project_yaml.read_text(encoding="utf-8")
        # room: "" → room: "!xyz:server"
        updated = re.sub(
            r"(room:\s*)\"\"",
            f'\\1"{room_id}"',
            content,
        )
        if updated != content:
            project_yaml.write_text(updated, encoding="utf-8")
    except OSError as e:
        logger.warning("project.yaml konnte nicht aktualisiert werden: %s", e)


# ================================================================== Status

@app.get("/status")
def system_status():
    return {
        "discovery": {
            "agents_dir": AGENTS_DIR,
            "count":      len(discovery.agents),
        },
        "projects": {
            "projects_dir": PROJECTS_DIR,
            "count":        len(projects.projects),
        },
        "sessions": {
            "active_projects": sessions.active_projects(),
        },
        "runtime": runtime.status_all(),
    }
