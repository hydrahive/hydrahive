"""
Threema Gateway Plugin for HydraHive

Send messages via the Threema Gateway API (msgapi.threema.ch).
Supports text messages, file sending and Threema ID lookups.

Threema Gateway API: https://gateway.threema.ch/en/developer/api
"""
import json
import logging
import urllib.request
import urllib.parse
from pathlib import Path

logger = logging.getLogger("threema-gateway")

THREEMA_API_BASE = "https://msgapi.threema.ch"


def _load_user_config(username: str) -> dict:
    path = Path(f"/etc/hydrahive/user_app_config/{username}/threema-gateway.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _api_request(method: str, path: str, data: dict | None = None, config: dict = {}) -> dict:
    """Simple synchronous urllib request for Threema Gateway API (form-encoded)."""
    gateway_id = config.get("gateway_id", "").strip()
    api_secret = config.get("api_secret", "").strip()
    url = f"{THREEMA_API_BASE}{path}"
    params = {"from": gateway_id, "secret": api_secret}
    if data:
        params.update(data)
    encoded = urllib.parse.urlencode(params).encode("utf-8")
    req = urllib.request.Request(url, data=encoded if method == "POST" else None)
    if method == "GET":
        url = url + "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode("utf-8")
            try:
                return {"ok": True, "data": json.loads(body)}
            except json.JSONDecodeError:
                return {"ok": True, "data": body}
    except urllib.error.HTTPError as e:
        return {"ok": False, "error": f"HTTP {e.code}: {e.reason}"}
    except Exception as ex:
        return {"ok": False, "error": str(ex)}


def register(api):

    @api.tool(
        tool_id="threema_send_text",
        description="Send a text message to a Threema ID via the Threema Gateway.",
        parameters={
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient Threema ID (8 characters, e.g. ECHOECHO)"},
                "text": {"type": "string", "description": "Text message to send"},
            },
            "required": ["to", "text"],
        },
    )
    async def threema_send_text(to: str = "", text: str = "", **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        if not config.get("gateway_id") or not config.get("api_secret"):
            return json.dumps({"error": "Threema not configured. Please set gateway_id and api_secret in plugin settings."})
        if not to or not text:
            return json.dumps({"error": "to and text are required."})
        result = _api_request("POST", "/send_simple", {"to": to, "text": text}, config)
        if result["ok"]:
            return json.dumps({"sent": True, "to": to, "message_id": result.get("data", "")})
        return json.dumps({"error": result.get("error", "Unknown error")})

    @api.tool(
        tool_id="threema_send_file",
        description="Send a file (by URL) to a Threema ID via the Threema Gateway.",
        parameters={
            "type": "object",
            "properties": {
                "to": {"type": "string", "description": "Recipient Threema ID (8 characters)"},
                "file_url": {"type": "string", "description": "Publicly accessible URL of the file to send"},
                "caption": {"type": "string", "description": "Optional caption/description for the file"},
            },
            "required": ["to", "file_url"],
        },
    )
    async def threema_send_file(to: str = "", file_url: str = "", caption: str = "", **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        if not config.get("gateway_id") or not config.get("api_secret"):
            return json.dumps({"error": "Threema not configured."})
        if not to or not file_url:
            return json.dumps({"error": "to and file_url are required."})
        # Threema Gateway simple send supports url-based file messages via JSON
        data = {"to": to, "url": file_url}
        if caption:
            data["text"] = caption
        result = _api_request("POST", "/send_simple", data, config)
        if result["ok"]:
            return json.dumps({"sent": True, "to": to, "file_url": file_url})
        return json.dumps({"error": result.get("error", "Unknown error")})

    @api.tool(
        tool_id="threema_lookup",
        description="Look up a Threema ID by phone number or email address.",
        parameters={
            "type": "object",
            "properties": {
                "phone": {"type": "string", "description": "Phone number in E.164 format (e.g. +49123456789) — provide phone OR email"},
                "email": {"type": "string", "description": "Email address to look up — provide phone OR email"},
            },
            "required": [],
        },
    )
    async def threema_lookup(phone: str = "", email: str = "", **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        if not config.get("gateway_id") or not config.get("api_secret"):
            return json.dumps({"error": "Threema not configured."})
        if not phone and not email:
            return json.dumps({"error": "Provide either phone or email."})
        import hashlib
        if phone:
            h = hashlib.sha256(phone.strip().encode()).hexdigest()
            result = _api_request("GET", f"/lookup/phone_hash/{h}", config=config)
        else:
            h = hashlib.sha256(email.strip().lower().encode()).hexdigest()
            result = _api_request("GET", f"/lookup/email_hash/{h}", config=config)
        if result["ok"]:
            return json.dumps({"found": True, "threema_id": result.get("data", ""), "lookup": phone or email})
        return json.dumps({"found": False, "error": result.get("error", "Not found")})

    logger.info("Threema Gateway Plugin registered (3 tools)")
