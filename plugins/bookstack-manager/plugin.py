"""
BookStack Manager Plugin für HydraHive

Wiki-Seiten durchsuchen, lesen, erstellen und aktualisieren über die BookStack REST API.
Authentifizierung via Token-ID + Token-Secret (Token {id}:{secret}).

BookStack API: https://www.bookstackapp.com/docs/admin/hacking-bookstack/#bookstack-api
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger("bookstack-manager")


def _load_user_config(username: str) -> dict:
    path = Path(f"/etc/hydrahive/user_app_config/{username}/bookstack-manager.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _get_client(config: dict):
    import httpx
    url = config.get("base_url", "").rstrip("/")
    token_id = config.get("token_id", "")
    token_secret = config.get("token_secret", "")
    if not url or not token_id or not token_secret:
        return None
    return httpx.AsyncClient(
        base_url=url,
        headers={
            "Authorization": f"Token {token_id}:{token_secret}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        timeout=20,
    )


def register(api):
    """BookStack-Plugin beim Core registrieren."""

    @api.tool(
        tool_id="bookstack_search",
        description="BookStack-Wiki durchsuchen. Findet Seiten, Kapitel und Bücher nach Suchbegriff.",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Suchbegriff.",
                },
                "type": {
                    "type": "string",
                    "description": "Ergebnistyp filtern: 'page', 'chapter', 'book', 'bookshelf'. Leer = alle.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max. Ergebnisse (default 20, max 100).",
                },
            },
            "required": ["query"],
        },
    )
    async def bookstack_search(query: str = "", type: str = "", limit: int = 20, **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "BookStack nicht konfiguriert. Bitte Base URL, Token ID und Token Secret eintragen."})
        if not query:
            return json.dumps({"error": "query ist Pflicht."})
        params: dict = {"query": query, "count": min(limit, 100)}
        if type:
            params["query"] = f"{query} [type:{type}]"
        try:
            async with client:
                r = await client.get("/api/search", params=params)
                r.raise_for_status()
                data = r.json()
            results = [
                {
                    "id": item.get("id"),
                    "type": item.get("type", ""),
                    "name": item.get("name", ""),
                    "url": item.get("url", ""),
                    "preview_html": (item.get("preview_html", {}).get("content", "") or "")[:300],
                    "book": item.get("book", {}).get("name", "") if item.get("book") else "",
                    "chapter": item.get("chapter", {}).get("name", "") if item.get("chapter") else "",
                }
                for item in data.get("data", [])
            ]
            return json.dumps({"query": query, "results": results, "total": data.get("total", len(results))})
        except Exception as e:
            logger.warning("bookstack_search error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="bookstack_pages",
        description=(
            "Seiten in BookStack auflisten oder eine bestimmte Seite abrufen. "
            "Ohne page_id: neueste Seiten. Mit page_id: Seiteninhalt (HTML + Markdown)."
        ),
        parameters={
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "integer",
                    "description": "ID einer Seite zum Lesen (optional).",
                },
                "book_id": {
                    "type": "integer",
                    "description": "Nur Seiten aus diesem Buch auflisten (optional).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max. Seiten beim Auflisten (default 20).",
                },
            },
            "required": [],
        },
    )
    async def bookstack_pages(page_id: int = 0, book_id: int = 0, limit: int = 20, **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "BookStack nicht konfiguriert."})
        try:
            async with client:
                if page_id:
                    r = await client.get(f"/api/pages/{page_id}")
                    r.raise_for_status()
                    p = r.json()
                    return json.dumps({
                        "id": p.get("id"),
                        "name": p.get("name", ""),
                        "book_id": p.get("book_id"),
                        "chapter_id": p.get("chapter_id"),
                        "html": p.get("html", "")[:5000],
                        "markdown": p.get("markdown", "")[:5000],
                        "created_at": p.get("created_at", ""),
                        "updated_at": p.get("updated_at", ""),
                        "created_by": (p.get("created_by") or {}).get("name", ""),
                        "updated_by": (p.get("updated_by") or {}).get("name", ""),
                        "tags": [t.get("name", "") for t in (p.get("tags") or [])],
                    })
                else:
                    params: dict = {"count": limit, "sort": "-updated_at"}
                    if book_id:
                        params["filter[book_id]"] = book_id
                    r = await client.get("/api/pages", params=params)
                    r.raise_for_status()
                    data = r.json()
                    pages = [
                        {
                            "id": p.get("id"),
                            "name": p.get("name", ""),
                            "book_id": p.get("book_id"),
                            "chapter_id": p.get("chapter_id"),
                            "updated_at": p.get("updated_at", ""),
                        }
                        for p in data.get("data", [])
                    ]
                    return json.dumps({"pages": pages, "total": data.get("total", len(pages))})
        except Exception as e:
            logger.warning("bookstack_pages error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="bookstack_create_page",
        description="Eine neue Seite in einem BookStack-Buch oder -Kapitel erstellen.",
        parameters={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "Seitentitel.",
                },
                "markdown": {
                    "type": "string",
                    "description": "Seiteninhalt als Markdown.",
                },
                "book_id": {
                    "type": "integer",
                    "description": "Ziel-Buch-ID (erforderlich wenn kein chapter_id).",
                },
                "chapter_id": {
                    "type": "integer",
                    "description": "Ziel-Kapitel-ID (optional, überschreibt book_id).",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Tags für die Seite (optional).",
                },
            },
            "required": ["name", "markdown"],
        },
    )
    async def bookstack_create_page(
        name: str = "",
        markdown: str = "",
        book_id: int = 0,
        chapter_id: int = 0,
        tags: list = None,
        **ctx,
    ) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "BookStack nicht konfiguriert."})
        if not name or not markdown:
            return json.dumps({"error": "name und markdown sind Pflicht."})
        if not book_id and not chapter_id:
            return json.dumps({"error": "book_id oder chapter_id ist Pflicht."})
        body: dict = {"name": name, "markdown": markdown}
        if chapter_id:
            body["chapter_id"] = chapter_id
        elif book_id:
            body["book_id"] = book_id
        if tags:
            body["tags"] = [{"name": t} for t in tags]
        try:
            async with client:
                r = await client.post("/api/pages", json=body)
                r.raise_for_status()
                p = r.json()
                return json.dumps({
                    "created": True,
                    "id": p.get("id"),
                    "name": p.get("name", ""),
                    "url": p.get("url", ""),
                    "book_id": p.get("book_id"),
                })
        except Exception as e:
            logger.warning("bookstack_create_page error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="bookstack_update_page",
        description="Eine bestehende BookStack-Seite aktualisieren (Titel und/oder Inhalt).",
        parameters={
            "type": "object",
            "properties": {
                "page_id": {
                    "type": "integer",
                    "description": "ID der zu aktualisierenden Seite.",
                },
                "name": {
                    "type": "string",
                    "description": "Neuer Seitentitel (optional, leer = unverändert).",
                },
                "markdown": {
                    "type": "string",
                    "description": "Neuer Seiteninhalt als Markdown (optional, leer = unverändert).",
                },
                "tags": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Neue Tags (optional, ersetzt vorhandene Tags).",
                },
            },
            "required": ["page_id"],
        },
    )
    async def bookstack_update_page(
        page_id: int = 0,
        name: str = "",
        markdown: str = "",
        tags: list = None,
        **ctx,
    ) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "BookStack nicht konfiguriert."})
        if not page_id:
            return json.dumps({"error": "page_id ist Pflicht."})
        try:
            async with client:
                # Fetch current page
                r_get = await client.get(f"/api/pages/{page_id}")
                r_get.raise_for_status()
                current = r_get.json()
                body: dict = {
                    "name": name or current.get("name", ""),
                    "markdown": markdown or current.get("markdown", ""),
                    "book_id": current.get("book_id"),
                }
                if current.get("chapter_id"):
                    body["chapter_id"] = current.get("chapter_id")
                if tags is not None:
                    body["tags"] = [{"name": t} for t in tags]
                r = await client.put(f"/api/pages/{page_id}", json=body)
                r.raise_for_status()
                p = r.json()
                return json.dumps({
                    "updated": True,
                    "id": p.get("id"),
                    "name": p.get("name", ""),
                    "url": p.get("url", ""),
                    "updated_at": p.get("updated_at", ""),
                })
        except Exception as e:
            logger.warning("bookstack_update_page error: %s", e)
            return json.dumps({"error": str(e)})

    logger.info("BookStack Manager Plugin registriert (4 Tools)")
