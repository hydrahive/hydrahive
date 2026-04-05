"""
Signal Messenger Plugin for HydraHive

Send and receive Signal messages via the signal-cli-rest-api.
Supports sending to individuals/groups, reading incoming messages,
listing groups and contacts.

signal-cli-rest-api: https://github.com/bbernhard/signal-cli-rest-api
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger("signal-messenger")


def _load_user_config(username: str) -> dict:
    path = Path(f"/etc/hydrahive/user_app_config/{username}/signal-messenger.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _get_client(config: dict):
    import httpx
    base_url = config.get("base_url", "").rstrip("/")
    phone = config.get("phone_number", "").strip()
    if not base_url or not phone:
        return None, None
    client = httpx.AsyncClient(base_url=base_url, timeout=20)
    return client, phone


def register(api):

    @api.tool(
        tool_id="signal_send",
        description="Send a Signal message to a phone number or group ID.",
        parameters={
            "type": "object",
            "properties": {
                "recipient": {"type": "string", "description": "Recipient phone number (E.164) or group ID"},
                "message": {"type": "string", "description": "Text message to send"},
                "is_group": {"type": "boolean", "description": "True if recipient is a group ID (default false)"},
            },
            "required": ["recipient", "message"],
        },
    )
    async def signal_send(recipient: str = "", message: str = "", is_group: bool = False, **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client, phone = _get_client(config)
        if not client:
            return json.dumps({"error": "Signal not configured. Please set base_url and phone_number in plugin settings."})
        if not recipient or not message:
            return json.dumps({"error": "recipient and message are required."})
        try:
            async with client:
                if is_group:
                    body = {"message": message, "number": phone, "recipients": [], "group_id": recipient}
                else:
                    body = {"message": message, "number": phone, "recipients": [recipient]}
                r = await client.post("/v2/send", json=body)
                r.raise_for_status()
                return json.dumps({"sent": True, "recipient": recipient, "message": message})
        except Exception as e:
            logger.warning("signal_send error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="signal_receive",
        description="Receive pending Signal messages for the configured phone number.",
        parameters={
            "type": "object",
            "properties": {
                "timeout": {"type": "integer", "description": "Receive timeout in seconds (default 1)"},
            },
            "required": [],
        },
    )
    async def signal_receive(timeout: int = 1, **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client, phone = _get_client(config)
        if not client:
            return json.dumps({"error": "Signal not configured."})
        try:
            async with client:
                r = await client.get(f"/v1/receive/{phone}", params={"timeout": timeout})
                r.raise_for_status()
                messages = r.json()
            result = []
            for m in (messages if isinstance(messages, list) else []):
                env = m.get("envelope", {})
                msg = env.get("dataMessage", {})
                result.append({
                    "from": env.get("source", ""),
                    "timestamp": env.get("timestamp", ""),
                    "message": msg.get("message", ""),
                    "group_id": (msg.get("groupInfo") or {}).get("groupId", ""),
                })
            return json.dumps({"messages": result, "count": len(result)})
        except Exception as e:
            logger.warning("signal_receive error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="signal_groups",
        description="List all Signal groups the configured number is a member of.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )
    async def signal_groups(**ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client, phone = _get_client(config)
        if not client:
            return json.dumps({"error": "Signal not configured."})
        try:
            async with client:
                r = await client.get(f"/v1/groups/{phone}")
                r.raise_for_status()
                groups = r.json()
            result = [{"id": g.get("id"), "name": g.get("name", ""), "members": g.get("members", [])} for g in (groups if isinstance(groups, list) else [])]
            return json.dumps({"groups": result, "count": len(result)})
        except Exception as e:
            logger.warning("signal_groups error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="signal_contacts",
        description="List Signal contacts known to the configured number.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )
    async def signal_contacts(**ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client, phone = _get_client(config)
        if not client:
            return json.dumps({"error": "Signal not configured."})
        try:
            async with client:
                r = await client.get(f"/v1/contacts/{phone}")
                r.raise_for_status()
                contacts = r.json()
            result = [{"number": c.get("number", ""), "name": c.get("name", ""), "profile_name": c.get("profile", {}).get("name", "")} for c in (contacts if isinstance(contacts, list) else [])]
            return json.dumps({"contacts": result, "count": len(result)})
        except Exception as e:
            logger.warning("signal_contacts error: %s", e)
            return json.dumps({"error": str(e)})

    logger.info("Signal Messenger Plugin registered (4 tools)")
