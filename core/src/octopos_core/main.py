"""
main.py — OctopOS Core Runtime Einstiegspunkt (#4, #6, #7)

FastAPI-App mit Lifespan-Management:
- AgentDiscovery + AgentRuntime + ProjectLoader + SessionManager
- REST-Endpoints fuer Agenten, Projekte und Sessions
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .agent_config import AgentConfig
from .agent_discovery import AgentDiscovery
from .agent_runtime import AgentRuntime
from .project_config import ProjectConfig
from .project_loader import ProjectLoader
from .session_manager import MessageRole, SessionManager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

AGENTS_DIR   = "/agents"
PROJECTS_DIR = "/projects"

discovery = AgentDiscovery(AGENTS_DIR)
runtime   = AgentRuntime()
projects  = ProjectLoader(PROJECTS_DIR)
sessions  = SessionManager(PROJECTS_DIR)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("OctopOS Core startet...")
    discovery.start()
    projects.start()
    sessions.start()
    await runtime.start(list(discovery.agents.values()))
    logger.info("OctopOS Core bereit")
    yield
    logger.info("OctopOS Core faehrt herunter...")
    await runtime.stop()
    projects.stop()
    discovery.stop()
    logger.info("OctopOS Core gestoppt")


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
