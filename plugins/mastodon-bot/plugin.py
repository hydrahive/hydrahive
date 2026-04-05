"""
Mastodon Bot Plugin for HydraHive

Interact with Mastodon instances via the Mastodon REST API.
Supports posting toots, reading timelines, notifications and search.

Mastodon API: https://docs.joinmastodon.org/api/
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger("mastodon-bot")


def _load_user_config(username: str) -> dict:
    path = Path(f"/etc/hydrahive/user_app_config/{username}/mastodon-bot.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _get_client(config: dict):
    import httpx
    instance_url = config.get("instance_url", "").rstrip("/")
    access_token = config.get("access_token", "").strip()
    if not instance_url or not access_token:
        return None
    headers = {"Authorization": f"Bearer {access_token}", "Content-Type": "application/json"}
    return httpx.AsyncClient(base_url=instance_url, headers=headers, timeout=20)


def register(api):

    @api.tool(
        tool_id="mastodon_toot",
        description="Post a toot (status) to Mastodon. Supports public, unlisted, private and direct visibility.",
        parameters={
            "type": "object",
            "properties": {
                "status": {"type": "string", "description": "The toot text content"},
                "visibility": {"type": "string", "description": "Visibility: public, unlisted, private, direct (default: public)"},
                "in_reply_to_id": {"type": "string", "description": "ID of a status to reply to (optional)"},
                "spoiler_text": {"type": "string", "description": "Content warning text (optional)"},
            },
            "required": ["status"],
        },
    )
    async def mastodon_toot(status: str = "", visibility: str = "public", in_reply_to_id: str = "", spoiler_text: str = "", **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Mastodon not configured. Please set instance_url and access_token in plugin settings."})
        if not status:
            return json.dumps({"error": "status text is required."})
        try:
            async with client:
                body = {"status": status, "visibility": visibility}
                if in_reply_to_id:
                    body["in_reply_to_id"] = in_reply_to_id
                if spoiler_text:
                    body["spoiler_text"] = spoiler_text
                r = await client.post("/api/v1/statuses", json=body)
                r.raise_for_status()
                toot = r.json()
                return json.dumps({"posted": True, "id": toot.get("id"), "url": toot.get("url"), "created_at": toot.get("created_at")})
        except Exception as e:
            logger.warning("mastodon_toot error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="mastodon_timeline",
        description="Read the home timeline or public timeline from Mastodon.",
        parameters={
            "type": "object",
            "properties": {
                "timeline": {"type": "string", "description": "Timeline type: home, public, local (default: home)"},
                "limit": {"type": "integer", "description": "Number of toots to retrieve (default 20, max 40)"},
            },
            "required": [],
        },
    )
    async def mastodon_timeline(timeline: str = "home", limit: int = 20, **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Mastodon not configured."})
        endpoints = {"home": "/api/v1/timelines/home", "public": "/api/v1/timelines/public", "local": "/api/v1/timelines/public"}
        endpoint = endpoints.get(timeline, "/api/v1/timelines/home")
        params = {"limit": min(limit, 40)}
        if timeline == "local":
            params["local"] = "true"
        try:
            async with client:
                r = await client.get(endpoint, params=params)
                r.raise_for_status()
                statuses = r.json()
            result = [{"id": s.get("id"), "account": s.get("account", {}).get("acct", ""), "content": s.get("content", ""), "created_at": s.get("created_at", ""), "url": s.get("url", "")} for s in (statuses if isinstance(statuses, list) else [])]
            return json.dumps({"timeline": timeline, "toots": result, "count": len(result)})
        except Exception as e:
            logger.warning("mastodon_timeline error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="mastodon_notifications",
        description="Retrieve Mastodon notifications (mentions, follows, boosts, favourites).",
        parameters={
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "Number of notifications to retrieve (default 20)"},
                "types": {"type": "string", "description": "Comma-separated notification types to include: mention, follow, reblog, favourite (empty = all)"},
            },
            "required": [],
        },
    )
    async def mastodon_notifications(limit: int = 20, types: str = "", **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Mastodon not configured."})
        try:
            async with client:
                params = {"limit": min(limit, 40)}
                if types:
                    for t in types.split(","):
                        params.setdefault("types[]", []).append(t.strip())
                r = await client.get("/api/v1/notifications", params=params)
                r.raise_for_status()
                notifs = r.json()
            result = [{"id": n.get("id"), "type": n.get("type", ""), "created_at": n.get("created_at", ""), "account": n.get("account", {}).get("acct", ""), "status_content": (n.get("status") or {}).get("content", "")} for n in (notifs if isinstance(notifs, list) else [])]
            return json.dumps({"notifications": result, "count": len(result)})
        except Exception as e:
            logger.warning("mastodon_notifications error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="mastodon_search",
        description="Search Mastodon for accounts, statuses or hashtags.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query"},
                "search_type": {"type": "string", "description": "Filter type: accounts, statuses, hashtags (empty = all)"},
                "limit": {"type": "integer", "description": "Max results per category (default 10)"},
            },
            "required": ["query"],
        },
    )
    async def mastodon_search(query: str = "", search_type: str = "", limit: int = 10, **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Mastodon not configured."})
        if not query:
            return json.dumps({"error": "query is required."})
        try:
            async with client:
                params = {"q": query, "limit": limit, "resolve": "true"}
                if search_type:
                    params["type"] = search_type
                r = await client.get("/api/v2/search", params=params)
                r.raise_for_status()
                data = r.json()
            accounts = [{"acct": a.get("acct"), "display_name": a.get("display_name", "")} for a in data.get("accounts", [])]
            statuses = [{"id": s.get("id"), "account": s.get("account", {}).get("acct", ""), "content": s.get("content", "")} for s in data.get("statuses", [])]
            hashtags = [{"name": h.get("name", ""), "url": h.get("url", "")} for h in data.get("hashtags", [])]
            return json.dumps({"accounts": accounts, "statuses": statuses, "hashtags": hashtags})
        except Exception as e:
            logger.warning("mastodon_search error: %s", e)
            return json.dumps({"error": str(e)})

    logger.info("Mastodon Bot Plugin registered (4 tools)")
