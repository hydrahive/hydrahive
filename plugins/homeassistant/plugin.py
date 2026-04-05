"""
Home Assistant Plugin for HydraHive

Interact with Home Assistant via the REST API.
Query entity states, call services, view history and manage automations.

Home Assistant REST API: https://developers.home-assistant.io/docs/api/rest/
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger("homeassistant")


def _load_user_config(username: str) -> dict:
    path = Path(f"/etc/hydrahive/user_app_config/{username}/homeassistant.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _get_client(config: dict):
    import httpx
    base_url = config.get("base_url", "").rstrip("/")
    api_token = config.get("api_token", "").strip()
    if not base_url or not api_token:
        return None
    headers = {
        "Authorization": f"Bearer {api_token}",
        "Content-Type": "application/json",
    }
    return httpx.AsyncClient(base_url=base_url, headers=headers, timeout=20)


def register(api):

    @api.tool(
        tool_id="ha_states",
        description="Get Home Assistant entity states. Returns all entities or filter by domain/entity_id.",
        parameters={
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "Specific entity ID (e.g. light.living_room). Empty = all entities."},
                "domain": {"type": "string", "description": "Filter by domain (e.g. light, switch, sensor). Only used when entity_id is empty."},
            },
            "required": [],
        },
    )
    async def ha_states(entity_id: str = "", domain: str = "", **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Home Assistant not configured. Please set base_url and api_token in plugin settings."})
        try:
            async with client:
                if entity_id:
                    r = await client.get(f"/api/states/{entity_id}")
                    r.raise_for_status()
                    state = r.json()
                    return json.dumps({"entity_id": state.get("entity_id"), "state": state.get("state"), "attributes": state.get("attributes", {}), "last_changed": state.get("last_changed")})
                else:
                    r = await client.get("/api/states")
                    r.raise_for_status()
                    states = r.json()
            if domain:
                states = [s for s in states if s.get("entity_id", "").startswith(domain + ".")]
            result = [{"entity_id": s.get("entity_id"), "state": s.get("state"), "friendly_name": s.get("attributes", {}).get("friendly_name", "")} for s in states]
            return json.dumps({"states": result, "count": len(result)})
        except Exception as e:
            logger.warning("ha_states error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="ha_services",
        description="List available Home Assistant services, optionally filtered by domain.",
        parameters={
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Filter by domain (e.g. light, switch, automation). Empty = all domains."},
            },
            "required": [],
        },
    )
    async def ha_services(domain: str = "", **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Home Assistant not configured."})
        try:
            async with client:
                r = await client.get("/api/services")
                r.raise_for_status()
                services = r.json()
            if domain:
                services = [s for s in services if s.get("domain") == domain]
            result = [{"domain": s.get("domain"), "services": list(s.get("services", {}).keys())} for s in services]
            return json.dumps({"services": result, "domain_count": len(result)})
        except Exception as e:
            logger.warning("ha_services error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="ha_call_service",
        description="Call a Home Assistant service (e.g. turn on a light, trigger an automation).",
        parameters={
            "type": "object",
            "properties": {
                "domain": {"type": "string", "description": "Service domain (e.g. light, switch, automation, script)"},
                "service": {"type": "string", "description": "Service name (e.g. turn_on, turn_off, toggle, trigger)"},
                "entity_id": {"type": "string", "description": "Target entity ID (e.g. light.living_room) — optional for some services"},
                "service_data": {"type": "object", "description": "Additional service data as JSON object (e.g. brightness, color)"},
            },
            "required": ["domain", "service"],
        },
    )
    async def ha_call_service(domain: str = "", service: str = "", entity_id: str = "", service_data: dict = None, **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Home Assistant not configured."})
        if not domain or not service:
            return json.dumps({"error": "domain and service are required."})
        try:
            async with client:
                body = service_data or {}
                if entity_id:
                    body["entity_id"] = entity_id
                r = await client.post(f"/api/services/{domain}/{service}", json=body)
                r.raise_for_status()
                result = r.json()
                changed = [{"entity_id": s.get("entity_id"), "state": s.get("state")} for s in (result if isinstance(result, list) else [])]
                return json.dumps({"called": True, "domain": domain, "service": service, "affected_entities": changed})
        except Exception as e:
            logger.warning("ha_call_service error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="ha_history",
        description="Get the state history for a Home Assistant entity over a time period.",
        parameters={
            "type": "object",
            "properties": {
                "entity_id": {"type": "string", "description": "Entity ID to get history for (e.g. sensor.temperature)"},
                "hours": {"type": "integer", "description": "Number of hours of history to retrieve (default 24)"},
            },
            "required": ["entity_id"],
        },
    )
    async def ha_history(entity_id: str = "", hours: int = 24, **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Home Assistant not configured."})
        if not entity_id:
            return json.dumps({"error": "entity_id is required."})
        from datetime import datetime, timedelta, timezone
        start = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
        try:
            async with client:
                r = await client.get(f"/api/history/period/{start}", params={"filter_entity_id": entity_id, "minimal_response": "true"})
                r.raise_for_status()
                history = r.json()
            entity_history = history[0] if history else []
            result = [{"state": h.get("state"), "last_changed": h.get("last_changed")} for h in entity_history]
            return json.dumps({"entity_id": entity_id, "history": result, "count": len(result), "period_hours": hours})
        except Exception as e:
            logger.warning("ha_history error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="ha_automations",
        description="List Home Assistant automations with their current state (on/off).",
        parameters={
            "type": "object",
            "properties": {
                "enabled_only": {"type": "boolean", "description": "Only return enabled automations (default false)"},
            },
            "required": [],
        },
    )
    async def ha_automations(enabled_only: bool = False, **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Home Assistant not configured."})
        try:
            async with client:
                r = await client.get("/api/states")
                r.raise_for_status()
                states = r.json()
            automations = [s for s in states if s.get("entity_id", "").startswith("automation.")]
            if enabled_only:
                automations = [a for a in automations if a.get("state") == "on"]
            result = [{"entity_id": a.get("entity_id"), "state": a.get("state"), "friendly_name": a.get("attributes", {}).get("friendly_name", ""), "last_triggered": a.get("attributes", {}).get("last_triggered")} for a in automations]
            return json.dumps({"automations": result, "count": len(result)})
        except Exception as e:
            logger.warning("ha_automations error: %s", e)
            return json.dumps({"error": str(e)})

    logger.info("Home Assistant Plugin registered (5 tools)")
