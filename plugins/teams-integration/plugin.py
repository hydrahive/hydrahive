"""
MS Teams Integration Plugin for HydraHive

Interact with Microsoft Teams via the Microsoft Graph API.
Uses OAuth2 client credentials flow to authenticate.
Supports sending messages, listing channels, chats and members.

Graph API: https://learn.microsoft.com/en-us/graph/api/overview
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger("teams-integration")


def _load_user_config(username: str) -> dict:
    path = Path(f"/etc/hydrahive/user_app_config/{username}/teams-integration.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


async def _get_token(config: dict) -> str | None:
    import httpx
    tenant_id = config.get("tenant_id", "").strip()
    client_id = config.get("client_id", "").strip()
    client_secret = config.get("client_secret", "").strip()
    if not all([tenant_id, client_id, client_secret]):
        return None
    url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"
    data = {
        "grant_type": "client_credentials",
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": "https://graph.microsoft.com/.default",
    }
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, data=data)
        r.raise_for_status()
        return r.json().get("access_token")


def _graph_client(token: str):
    import httpx
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    return httpx.AsyncClient(base_url="https://graph.microsoft.com/v1.0", headers=headers, timeout=20)


def register(api):

    @api.tool(
        tool_id="teams_send",
        description="Send a message to a Microsoft Teams channel.",
        parameters={
            "type": "object",
            "properties": {
                "team_id": {"type": "string", "description": "Teams group/team ID"},
                "channel_id": {"type": "string", "description": "Channel ID within the team"},
                "message": {"type": "string", "description": "Message text (HTML supported)"},
            },
            "required": ["team_id", "channel_id", "message"],
        },
    )
    async def teams_send(team_id: str = "", channel_id: str = "", message: str = "", **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        if not config.get("tenant_id"):
            return json.dumps({"error": "Teams not configured. Please set tenant_id, client_id and client_secret."})
        if not all([team_id, channel_id, message]):
            return json.dumps({"error": "team_id, channel_id and message are required."})
        try:
            token = await _get_token(config)
            if not token:
                return json.dumps({"error": "Failed to obtain access token. Check credentials."})
            async with _graph_client(token) as client:
                body = {"body": {"contentType": "html", "content": message}}
                r = await client.post(f"/teams/{team_id}/channels/{channel_id}/messages", json=body)
                r.raise_for_status()
                msg = r.json()
                return json.dumps({"sent": True, "id": msg.get("id"), "created": msg.get("createdDateTime")})
        except Exception as e:
            logger.warning("teams_send error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="teams_channels",
        description="List channels in a Microsoft Teams team.",
        parameters={
            "type": "object",
            "properties": {
                "team_id": {"type": "string", "description": "Teams group/team ID"},
            },
            "required": ["team_id"],
        },
    )
    async def teams_channels(team_id: str = "", **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        if not config.get("tenant_id"):
            return json.dumps({"error": "Teams not configured."})
        if not team_id:
            return json.dumps({"error": "team_id is required."})
        try:
            token = await _get_token(config)
            if not token:
                return json.dumps({"error": "Failed to obtain access token."})
            async with _graph_client(token) as client:
                r = await client.get(f"/teams/{team_id}/channels")
                r.raise_for_status()
                data = r.json()
            channels = [{"id": c.get("id"), "name": c.get("displayName", ""), "description": c.get("description", ""), "membership_type": c.get("membershipType", "")} for c in data.get("value", [])]
            return json.dumps({"channels": channels, "count": len(channels)})
        except Exception as e:
            logger.warning("teams_channels error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="teams_chats",
        description="List recent Teams chats (direct messages and group chats).",
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max chats to return (default 20)"},
            },
            "required": [],
        },
    )
    async def teams_chats(limit: int = 20, **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        if not config.get("tenant_id"):
            return json.dumps({"error": "Teams not configured."})
        try:
            token = await _get_token(config)
            if not token:
                return json.dumps({"error": "Failed to obtain access token."})
            async with _graph_client(token) as client:
                r = await client.get("/chats", params={"$top": limit})
                r.raise_for_status()
                data = r.json()
            chats = [{"id": c.get("id"), "topic": c.get("topic", ""), "chat_type": c.get("chatType", ""), "last_updated": c.get("lastUpdatedDateTime", "")} for c in data.get("value", [])]
            return json.dumps({"chats": chats, "count": len(chats)})
        except Exception as e:
            logger.warning("teams_chats error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="teams_members",
        description="List members of a Microsoft Teams team.",
        parameters={
            "type": "object",
            "properties": {
                "team_id": {"type": "string", "description": "Teams group/team ID"},
            },
            "required": ["team_id"],
        },
    )
    async def teams_members(team_id: str = "", **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        if not config.get("tenant_id"):
            return json.dumps({"error": "Teams not configured."})
        if not team_id:
            return json.dumps({"error": "team_id is required."})
        try:
            token = await _get_token(config)
            if not token:
                return json.dumps({"error": "Failed to obtain access token."})
            async with _graph_client(token) as client:
                r = await client.get(f"/teams/{team_id}/members")
                r.raise_for_status()
                data = r.json()
            members = [{"id": m.get("id"), "display_name": m.get("displayName", ""), "email": m.get("email", ""), "roles": m.get("roles", [])} for m in data.get("value", [])]
            return json.dumps({"members": members, "count": len(members)})
        except Exception as e:
            logger.warning("teams_members error: %s", e)
            return json.dumps({"error": str(e)})

    logger.info("MS Teams Integration Plugin registered (4 tools)")
