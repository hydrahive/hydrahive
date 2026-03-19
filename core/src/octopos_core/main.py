"""
main.py — OctopOS Core Runtime Einstiegspunkt (#4)

FastAPI-App mit Lifespan-Management:
- Beim Start: AgentDiscovery + AgentRuntime hochfahren
- REST-Endpoints für Status, Spawn, Heartbeat
- Graceful Shutdown
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from .agent_config import AgentConfig
from .agent_discovery import AgentDiscovery
from .agent_runtime import AgentRuntime

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

AGENTS_DIR = "/agents"

discovery = AgentDiscovery(AGENTS_DIR)
runtime   = AgentRuntime()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # --- Startup ---
    logger.info("OctopOS Core startet...")
    discovery.start()
    await runtime.start(list(discovery.agents.values()))
    logger.info("OctopOS Core bereit")

    yield

    # --- Shutdown ---
    logger.info("OctopOS Core fährt herunter...")
    await runtime.stop()
    discovery.stop()
    logger.info("OctopOS Core gestoppt")


app = FastAPI(
    title="OctopOS Core",
    version="0.1.0",
    lifespan=lifespan,
)


# ------------------------------------------------------------------ Endpoints

@app.get("/health")
def health():
    return {"status": "ok", "service": "octopos-core"}


@app.get("/agents")
def list_agents():
    """Alle registrierten Agenten (Discovery + Laufzeitstatus)."""
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
    """Task-Agenten on-demand starten (vom Boss aufgerufen)."""
    cfg = discovery.get(req.agent_id)
    if not cfg:
        raise HTTPException(404, f"Agent '{req.agent_id}' nicht in Discovery")
    if cfg.type != "worker":
        raise HTTPException(400, f"Nur worker können gespawnt werden, nicht {cfg.type}")
    await runtime.spawn_task_agent(cfg)
    return {"spawned": req.agent_id}


@app.post("/agents/{agent_id}/heartbeat")
def agent_heartbeat(agent_id: str):
    """Vom Agent aufgerufen um Liveness zu melden."""
    runtime.heartbeat(agent_id)
    return {"ok": True}


@app.get("/status")
def system_status():
    return {
        "discovery": {
            "agents_dir": AGENTS_DIR,
            "count": len(discovery.agents),
        },
        "runtime": runtime.status_all(),
    }
