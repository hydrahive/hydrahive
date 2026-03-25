from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field


class CreateAgentRequest(BaseModel):
    id: str
    type: str
    identity: str
    model: str
    temperature: float = 0.7
    max_tokens: int = 4096
    soul: str = ""
    tools: list[str] = Field(default_factory=list)
    fallback_models: list[str] = Field(default_factory=list)
    mcp_servers: list[str] = Field(default_factory=list)
    allowed_agents: list[str] = Field(default_factory=list)
    max_tool_rounds: int | None = None
    heartbeat_interval: str = "30s"
    heartbeat_timeout: str = "90s"
    heartbeat_on_failure: str = "restart"


def build_agent_admin_llm_data(req: CreateAgentRequest) -> dict:
    return {
        "model": req.model,
        "temperature": req.temperature,
        "max_tokens": req.max_tokens,
        "fallback_models": list(req.fallback_models),
    }


def build_agent_admin_data(req: CreateAgentRequest, agent_id: str | None = None) -> dict:
    agent_data = {
        "id": req.id or agent_id,
        "type": req.type,
        "identity": req.identity,
        "llm": build_agent_admin_llm_data(req),
        "tools": list(req.tools),
        "allowed_agents": list(req.allowed_agents),
        "mcp_servers": list(req.mcp_servers),
        "heartbeat": {
            "interval": req.heartbeat_interval,
            "timeout": req.heartbeat_timeout,
            "on_failure": req.heartbeat_on_failure,
        },
    }
    if req.soul:
        agent_data["soul"] = "./soul.md"
    if req.max_tool_rounds is not None:
        agent_data["max_tool_rounds"] = req.max_tool_rounds
    return agent_data


class SpawnRequest(BaseModel):
    agent_id: str


class HeartbeatPatchRequest(BaseModel):
    enabled:    bool  = True
    interval:   str   = "30s"
    timeout:    str   = "90s"
    on_failure: str   = "restart"


def register_agent_admin_routes(
    auth_router: APIRouter,
    admin_router: APIRouter,
    *,
    require_auth,
    require_admin,
    require_admin_or_localhost,
    require_auth_or_localhost,
    discovery,
    runtime,
    agents_dir: str,
    audit_log,
    logger,
    load_agent_config_direct,
) -> None:
    @admin_router.post("/agents", status_code=201)
    async def create_agent(req: CreateAgentRequest, _a: tuple = Depends(require_admin)):
        import asyncio as _asyncio
        import re as _re
        import yaml as _yaml

        if not _re.match(r"^[a-z0-9_-]+$", req.id):
            raise HTTPException(400, "Agent-ID darf nur a-z, 0-9, _ und - enthalten")
        if req.type not in {"boss", "specialist", "worker"}:
            raise HTTPException(400, f"Ungueltiger Typ: {req.type}")
        if discovery.get(req.id):
            raise HTTPException(409, f"Agent '{req.id}' existiert bereits")

        agent_dir = Path(agents_dir) / req.id
        agent_dir.mkdir(parents=True, exist_ok=True)
        (agent_dir / "skills").mkdir(exist_ok=True)
        (agent_dir / "memory").mkdir(exist_ok=True)

        agent_data = build_agent_admin_data(req)

        yaml_path = agent_dir / "agent.yaml"
        yaml_path.write_text(_yaml.dump(agent_data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
        soul_path = agent_dir / "soul.md"
        soul_path.write_text(req.soul or f"# {req.identity}\n\nDu bist {req.identity}, ein KI-Agent.\n", encoding="utf-8")

        logger.info("Agent angelegt: %s (%s)", req.id, req.type)
        audit_log("agent.create", target=req.id, details={"type": req.type, "model": req.model})
        await _asyncio.sleep(0.3)

        cfg = discovery.get(req.id)
        if cfg is None:
            cfg = load_agent_config_direct(agent_dir)

        return {
            "created": True,
            "agent_id": req.id,
            "agent_dir": str(agent_dir),
            "yaml_path": str(yaml_path),
            "registered": cfg is not None,
        }

    @admin_router.put("/agents/{agent_id}")
    async def update_agent(agent_id: str, req: CreateAgentRequest, _a: tuple = Depends(require_admin)):
        import yaml as _yaml

        agent_dir = Path(agents_dir) / agent_id
        if not agent_dir.exists():
            raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")

        agent_data = build_agent_admin_data(req, agent_id=agent_id)
        if req.soul:
            (agent_dir / "soul.md").write_text(req.soul, encoding="utf-8")

        yaml_path = agent_dir / "agent.yaml"
        yaml_path.write_text(_yaml.dump(agent_data, allow_unicode=True, default_flow_style=False), encoding="utf-8")
        discovery._register(agent_dir)
        logger.info("Agent aktualisiert: %s", agent_id)
        return {"updated": True, "agent_id": agent_id}

    @admin_router.delete("/agents/{agent_id}")
    async def delete_agent(agent_id: str, _a: tuple = Depends(require_admin)):
        agent_dir = Path(agents_dir) / agent_id
        if not agent_dir.exists():
            raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")
        disabled_dir = Path(agents_dir) / f"_{agent_id}_disabled"
        agent_dir.rename(disabled_dir)
        logger.info("Agent deaktiviert: %s -> %s", agent_dir, disabled_dir)
        audit_log("agent.delete", target=agent_id)
        return {"disabled": True, "agent_id": agent_id, "moved_to": str(disabled_dir)}

    @auth_router.get("/agents/{agent_id}/soul")
    def get_agent_soul(agent_id: str, _a: tuple[str, str] = Depends(require_auth)):
        soul_path = Path(agents_dir) / agent_id / "soul.md"
        if not soul_path.exists():
            return {"soul": "", "exists": False}
        return {"soul": soul_path.read_text(encoding="utf-8"), "exists": True}

    @admin_router.post("/agents/spawn")
    async def spawn_task_agent(req: SpawnRequest, _a: tuple = Depends(require_admin_or_localhost)):
        cfg = discovery.get(req.agent_id)
        if not cfg:
            raise HTTPException(404, f"Agent '{req.agent_id}' nicht in Discovery")
        if cfg.type != "worker":
            raise HTTPException(400, f"Nur worker koennen gespawnt werden, nicht {cfg.type}")
        await runtime.spawn_task_agent(cfg)
        return {"spawned": req.agent_id}

    @admin_router.post("/agents/{agent_id}/stop")
    async def stop_agent(agent_id: str, _a: tuple = Depends(require_admin)):
        """Bricht den laufenden Task eines Agenten ab (Notfall-Stop)."""
        stopped = await runtime.stop_agent_task(agent_id)
        if not stopped:
            raise HTTPException(404, f"Agent '{agent_id}' hat keinen laufenden Task")
        return {"stopped": agent_id}

    @auth_router.post("/agents/{agent_id}/heartbeat")
    def agent_heartbeat(agent_id: str, _a: tuple = Depends(require_auth_or_localhost)):
        runtime.heartbeat(agent_id)
        return {"ok": True}

    @admin_router.patch("/agents/{agent_id}/heartbeat")
    def patch_agent_heartbeat(agent_id: str, req: HeartbeatPatchRequest, _a: tuple = Depends(require_admin)):
        import yaml as _yaml

        agent_dir = Path(agents_dir) / agent_id
        if not agent_dir.exists():
            raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")
        yaml_path = agent_dir / "agent.yaml"
        try:
            raw = _yaml.safe_load(yaml_path.read_text(encoding="utf-8")) or {}
            raw["heartbeat"] = {
                "enabled":    req.enabled,
                "interval":   req.interval,
                "timeout":    req.timeout,
                "on_failure": req.on_failure,
            }
            yaml_path.write_text(_yaml.dump(raw, allow_unicode=True, sort_keys=False), encoding="utf-8")
        except Exception as e:
            raise HTTPException(500, f"Fehler beim Speichern: {e}")
        load_agent_config_direct(agent_dir)
        return {"ok": True, "agent_id": agent_id}
