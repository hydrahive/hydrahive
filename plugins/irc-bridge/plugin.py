"""
IRC Bridge Plugin for HydraHive

Connect to IRC networks via a REST bridge (e.g. Ergo/Soju HTTP API or
a custom bridge like https://github.com/ekmartin/slack-irc).

The bridge exposes a simple REST API for sending/receiving IRC messages.

Bridge API: configured via base_url in plugin settings.
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger("irc-bridge")


def _load_user_config(username: str) -> dict:
    path = Path(f"/etc/hydrahive/user_app_config/{username}/irc-bridge.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _get_client(config: dict):
    import httpx
    base_url = config.get("bridge_url", "").rstrip("/")
    if not base_url:
        return None
    return httpx.AsyncClient(base_url=base_url, timeout=15)


def register(api):

    @api.tool(
        tool_id="irc_send",
        description="Send a message to an IRC channel or user via the REST bridge.",
        parameters={
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "IRC channel (e.g. #general) or nickname"},
                "message": {"type": "string", "description": "Message text to send"},
                "server": {"type": "string", "description": "IRC server hostname (overrides config default)"},
            },
            "required": ["target", "message"],
        },
    )
    async def irc_send(target: str = "", message: str = "", server: str = "", **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "IRC not configured. Please set bridge_url in plugin settings."})
        if not target or not message:
            return json.dumps({"error": "target and message are required."})
        irc_server = server or config.get("server", "")
        nickname = config.get("nickname", "HydraHive")
        try:
            async with client:
                body = {"server": irc_server, "nick": nickname, "target": target, "message": message}
                r = await client.post("/send", json=body)
                r.raise_for_status()
                return json.dumps({"sent": True, "target": target, "message": message})
        except Exception as e:
            logger.warning("irc_send error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="irc_channels",
        description="List IRC channels the bridge is currently connected to.",
        parameters={
            "type": "object",
            "properties": {
                "server": {"type": "string", "description": "IRC server hostname (overrides config default)"},
            },
            "required": [],
        },
    )
    async def irc_channels(server: str = "", **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "IRC not configured."})
        irc_server = server or config.get("server", "")
        try:
            async with client:
                r = await client.get("/channels", params={"server": irc_server})
                r.raise_for_status()
                data = r.json()
            channels = data if isinstance(data, list) else data.get("channels", [])
            return json.dumps({"channels": channels, "count": len(channels)})
        except Exception as e:
            logger.warning("irc_channels error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="irc_users",
        description="List users currently in an IRC channel.",
        parameters={
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "IRC channel name (e.g. #general)"},
                "server": {"type": "string", "description": "IRC server hostname (overrides config default)"},
            },
            "required": ["channel"],
        },
    )
    async def irc_users(channel: str = "", server: str = "", **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "IRC not configured."})
        if not channel:
            return json.dumps({"error": "channel is required."})
        irc_server = server or config.get("server", "")
        try:
            async with client:
                r = await client.get("/users", params={"server": irc_server, "channel": channel})
                r.raise_for_status()
                data = r.json()
            users = data if isinstance(data, list) else data.get("users", [])
            return json.dumps({"channel": channel, "users": users, "count": len(users)})
        except Exception as e:
            logger.warning("irc_users error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="irc_join",
        description="Join an IRC channel via the REST bridge.",
        parameters={
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "IRC channel to join (e.g. #general)"},
                "server": {"type": "string", "description": "IRC server hostname (overrides config default)"},
            },
            "required": ["channel"],
        },
    )
    async def irc_join(channel: str = "", server: str = "", **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "IRC not configured."})
        if not channel:
            return json.dumps({"error": "channel is required."})
        irc_server = server or config.get("server", "")
        nickname = config.get("nickname", "HydraHive")
        try:
            async with client:
                body = {"server": irc_server, "nick": nickname, "channel": channel}
                r = await client.post("/join", json=body)
                r.raise_for_status()
                return json.dumps({"joined": True, "channel": channel, "server": irc_server})
        except Exception as e:
            logger.warning("irc_join error: %s", e)
            return json.dumps({"error": str(e)})

    logger.info("IRC Bridge Plugin registered (4 tools)")
