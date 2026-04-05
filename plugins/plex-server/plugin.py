"""
Plex Media Server Plugin for HydraHive

Browse and query your Plex Media Server via the Plex API.
List libraries, search for media, view active sessions and recent additions.

Plex API: https://github.com/Arcanemagus/plex-api/wiki
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger("plex-server")


def _load_user_config(username: str) -> dict:
    path = Path(f"/etc/hydrahive/user_app_config/{username}/plex-server.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _get_client(config: dict):
    import httpx
    base_url = config.get("base_url", "").rstrip("/")
    plex_token = config.get("plex_token", "").strip()
    if not base_url or not plex_token:
        return None
    headers = {
        "X-Plex-Token": plex_token,
        "Accept": "application/json",
    }
    return httpx.AsyncClient(base_url=base_url, headers=headers, timeout=20)


def register(api):

    @api.tool(
        tool_id="plex_libraries",
        description="List all media libraries in the Plex server (Movies, TV Shows, Music, etc.).",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )
    async def plex_libraries(**ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Plex not configured. Please set base_url and plex_token in plugin settings."})
        try:
            async with client:
                r = await client.get("/library/sections")
                r.raise_for_status()
                data = r.json()
            sections = data.get("MediaContainer", {}).get("Directory", [])
            libraries = [{"key": s.get("key"), "title": s.get("title", ""), "type": s.get("type", ""), "agent": s.get("agent", ""), "count": s.get("count", 0)} for s in sections]
            return json.dumps({"libraries": libraries, "count": len(libraries)})
        except Exception as e:
            logger.warning("plex_libraries error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="plex_search",
        description="Search for media across all Plex libraries.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search term (title, actor, etc.)"},
                "limit": {"type": "integer", "description": "Max results to return (default 20)"},
            },
            "required": ["query"],
        },
    )
    async def plex_search(query: str = "", limit: int = 20, **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Plex not configured."})
        if not query:
            return json.dumps({"error": "query is required."})
        try:
            async with client:
                r = await client.get("/search", params={"query": query, "limit": limit})
                r.raise_for_status()
                data = r.json()
            items = data.get("MediaContainer", {}).get("Metadata", [])
            results = [{"title": i.get("title", ""), "type": i.get("type", ""), "year": i.get("year", ""), "rating_key": i.get("ratingKey", ""), "summary": (i.get("summary") or "")[:200], "thumb": i.get("thumb", "")} for i in items]
            return json.dumps({"results": results, "count": len(results)})
        except Exception as e:
            logger.warning("plex_search error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="plex_sessions",
        description="List currently active Plex streaming sessions.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )
    async def plex_sessions(**ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Plex not configured."})
        try:
            async with client:
                r = await client.get("/status/sessions")
                r.raise_for_status()
                data = r.json()
            container = data.get("MediaContainer", {})
            sessions_raw = container.get("Metadata", [])
            sessions = []
            for s in sessions_raw:
                player = s.get("Player", {})
                user = s.get("User", {})
                sessions.append({
                    "title": s.get("title", ""),
                    "type": s.get("type", ""),
                    "user": user.get("title", ""),
                    "player": player.get("title", ""),
                    "state": player.get("state", ""),
                    "progress_ms": s.get("viewOffset", 0),
                    "duration_ms": s.get("duration", 0),
                })
            return json.dumps({"sessions": sessions, "count": len(sessions)})
        except Exception as e:
            logger.warning("plex_sessions error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="plex_recently_added",
        description="List recently added media items in a Plex library.",
        parameters={
            "type": "object",
            "properties": {
                "library_key": {"type": "string", "description": "Library section key from plex_libraries (e.g. '1'). Empty = all libraries."},
                "limit": {"type": "integer", "description": "Max items to return (default 20)"},
            },
            "required": [],
        },
    )
    async def plex_recently_added(library_key: str = "", limit: int = 20, **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Plex not configured."})
        try:
            async with client:
                if library_key:
                    r = await client.get(f"/library/sections/{library_key}/recentlyAdded", params={"X-Plex-Container-Size": limit})
                else:
                    r = await client.get("/library/recentlyAdded", params={"X-Plex-Container-Size": limit})
                r.raise_for_status()
                data = r.json()
            items = data.get("MediaContainer", {}).get("Metadata", [])
            result = [{"title": i.get("title", ""), "type": i.get("type", ""), "year": i.get("year", ""), "added_at": i.get("addedAt", ""), "rating_key": i.get("ratingKey", "")} for i in items[:limit]]
            return json.dumps({"recently_added": result, "count": len(result)})
        except Exception as e:
            logger.warning("plex_recently_added error: %s", e)
            return json.dumps({"error": str(e)})

    logger.info("Plex Media Server Plugin registered (4 tools)")
