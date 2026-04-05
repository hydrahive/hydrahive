"""
Slack Integration Plugin for HydraHive

Interact with Slack workspaces via the Slack Web API.
Supports sending messages, listing channels, reading history and users.

Slack API: https://api.slack.com/web
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger("slack-integration")


def _load_user_config(username: str) -> dict:
    path = Path(f"/etc/hydrahive/user_app_config/{username}/slack-integration.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _get_client(config: dict):
    import httpx
    token = config.get("bot_token", "").strip()
    if not token:
        return None
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json; charset=utf-8",
    }
    return httpx.AsyncClient(base_url="https://slack.com/api", headers=headers, timeout=20)


def register(api):

    @api.tool(
        tool_id="slack_send",
        description="Send a message to a Slack channel or user.",
        parameters={
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "Channel ID or name (e.g. #general or C12345)"},
                "text": {"type": "string", "description": "Message text (supports Slack mrkdwn)"},
                "thread_ts": {"type": "string", "description": "Thread timestamp to reply in a thread (optional)"},
            },
            "required": ["channel", "text"],
        },
    )
    async def slack_send(channel: str = "", text: str = "", thread_ts: str = "", **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Slack not configured. Please set bot_token in plugin settings."})
        if not channel or not text:
            return json.dumps({"error": "channel and text are required."})
        try:
            async with client:
                body = {"channel": channel, "text": text}
                if thread_ts:
                    body["thread_ts"] = thread_ts
                r = await client.post("/chat.postMessage", json=body)
                r.raise_for_status()
                data = r.json()
                if not data.get("ok"):
                    return json.dumps({"error": data.get("error", "Slack API error")})
                return json.dumps({"sent": True, "ts": data.get("ts"), "channel": data.get("channel")})
        except Exception as e:
            logger.warning("slack_send error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="slack_channels",
        description="List public channels in the Slack workspace.",
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max channels to return (default 50)"},
                "exclude_archived": {"type": "boolean", "description": "Exclude archived channels (default true)"},
            },
            "required": [],
        },
    )
    async def slack_channels(limit: int = 50, exclude_archived: bool = True, **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Slack not configured."})
        try:
            async with client:
                r = await client.get("/conversations.list", params={"limit": limit, "exclude_archived": str(exclude_archived).lower()})
                r.raise_for_status()
                data = r.json()
                if not data.get("ok"):
                    return json.dumps({"error": data.get("error", "Slack API error")})
            channels = [{"id": c.get("id"), "name": c.get("name"), "is_private": c.get("is_private", False), "num_members": c.get("num_members", 0)} for c in data.get("channels", [])]
            return json.dumps({"channels": channels, "count": len(channels)})
        except Exception as e:
            logger.warning("slack_channels error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="slack_history",
        description="Read message history from a Slack channel.",
        parameters={
            "type": "object",
            "properties": {
                "channel": {"type": "string", "description": "Channel ID (e.g. C12345)"},
                "limit": {"type": "integer", "description": "Number of messages to retrieve (default 20)"},
            },
            "required": ["channel"],
        },
    )
    async def slack_history(channel: str = "", limit: int = 20, **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Slack not configured."})
        if not channel:
            return json.dumps({"error": "channel is required."})
        try:
            async with client:
                r = await client.get("/conversations.history", params={"channel": channel, "limit": limit})
                r.raise_for_status()
                data = r.json()
                if not data.get("ok"):
                    return json.dumps({"error": data.get("error", "Slack API error")})
            messages = [{"ts": m.get("ts"), "user": m.get("user", ""), "text": m.get("text", ""), "subtype": m.get("subtype", "")} for m in data.get("messages", [])]
            return json.dumps({"messages": messages, "count": len(messages)})
        except Exception as e:
            logger.warning("slack_history error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="slack_users",
        description="List users in the Slack workspace.",
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Max users to return (default 50)"},
            },
            "required": [],
        },
    )
    async def slack_users(limit: int = 50, **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Slack not configured."})
        try:
            async with client:
                r = await client.get("/users.list", params={"limit": limit})
                r.raise_for_status()
                data = r.json()
                if not data.get("ok"):
                    return json.dumps({"error": data.get("error", "Slack API error")})
            users = [{"id": u.get("id"), "name": u.get("name", ""), "real_name": u.get("real_name", ""), "is_bot": u.get("is_bot", False), "deleted": u.get("deleted", False)} for u in data.get("members", []) if not u.get("deleted")]
            return json.dumps({"users": users, "count": len(users)})
        except Exception as e:
            logger.warning("slack_users error: %s", e)
            return json.dumps({"error": str(e)})

    logger.info("Slack Integration Plugin registered (4 tools)")
