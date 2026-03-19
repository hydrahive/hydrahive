"""
main.py — OctopOS Core Runtime Einstiegspunkt (#4, #7)

FastAPI-App mit Lifespan-Management:
- AgentDiscovery + AgentRuntime + ProjectLoader
- REST-Endpoints fuer Agenten und Projekte
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


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("OctopOS Core startet...")
    discovery.start()
    projects.start()
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
    """Alle registrierten Projekte."""
    return {
        pid: {
            "name":       cfg.identity.name,
            "description": cfg.identity.description,
            "boss":       cfg.agents.boss,
            "workers":    cfg.agents.workers,
            "matrix_room": cfg.matrix.room,
            "filesystem": cfg.effective_filesystem_path(),
            "system_user": cfg.effective_system_user(),
            "show_swarm": cfg.chat.show_swarm,
        }
        for pid, cfg in projects.projects.items()
    }


@app.get("/projects/{project_id}")
def get_project(project_id: str):
    cfg = projects.get(project_id)
    if not cfg:
        raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
    # Pruefe ob alle zugewiesenen Agenten bekannt sind
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
    """Agenten eines Projekts mit Laufzeitstatus."""
    cfg = projects.get(project_id)
    if not cfg:
        raise HTTPException(404, f"Projekt '{project_id}' nicht gefunden")
    running = runtime.status_all()
    result = {}
    for agent_id in cfg.all_agents:
        agent_cfg = discovery.get(agent_id)
        result[agent_id] = {
            "role":    "boss" if agent_id == cfg.agents.boss else "worker",
            "found":   agent_cfg is not None,
            "runtime": running.get(agent_id),
        }
    return result


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
        "runtime": runtime.status_all(),
    }
