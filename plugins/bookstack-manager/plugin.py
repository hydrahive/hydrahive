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
    # Per-User Config hat Priorität
    path = Path(f"/etc/hydrahive/user_app_config/{username}/bookstack-manager.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        pass
    # Fallback: System-weite Config (alle Agenten haben Zugriff)
    system_path = Path("/etc/hydrahive/bookstack.json")
    try:
        return json.loads(system_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


# ── Auto-Setup: Grundstruktur beim ersten Start anlegen ──────────────────────

_WIKI_SHELVES = [
    {"name": "Systeme", "description": "Dokumentation aller Systeme, Server und Dienste"},
    {"name": "Lessons Learned", "description": "Fehler, Lösungen und was wir daraus gelernt haben"},
    {"name": "Workflows", "description": "Erprobte Arbeitsweisen und Best Practices"},
    {"name": "Projekte", "description": "Projekt-Dokumentation und Fortschritt"},
]


async def _auto_setup(client) -> dict:
    """Erstellt die Wiki-Grundstruktur wenn noch nicht vorhanden."""
    created = []
    try:
        async with client:
            # Prüfe ob Shelves schon existieren
            r = await client.get("/api/shelves", params={"count": 100})
            r.raise_for_status()
            existing = {s["name"] for s in r.json().get("data", [])}

            for shelf in _WIKI_SHELVES:
                if shelf["name"] not in existing:
                    r = await client.post("/api/shelves", json=shelf)
                    if r.status_code < 300:
                        created.append(shelf["name"])
                        shelf_id = r.json().get("id")
                        # Erstes Buch im Shelf anlegen
                        if shelf_id:
                            await client.post("/api/books", json={
                                "name": f"{shelf['name']} — Allgemein",
                                "description": shelf["description"],
                            })
    except Exception as e:
        logger.warning("Wiki auto-setup error: %s", e)
    return {"created_shelves": created}


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

    @api.tool(
        tool_id="bookstack_setup",
        description=(
            "BookStack-Wiki Grundstruktur anlegen: Shelves für Systeme, Lessons Learned, "
            "Workflows und Projekte. Idempotent — erstellt nur was noch nicht existiert."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )
    async def bookstack_setup(**ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "BookStack nicht konfiguriert."})
        result = await _auto_setup(client)
        return json.dumps(result)

    @api.tool(
        tool_id="bookstack_log_lesson",
        description=(
            "Schnell eine Lesson Learned dokumentieren: Was passiert ist, was gelernt wurde, "
            "welche Lösung funktioniert hat. Wird automatisch im 'Lessons Learned' Buch gespeichert."
        ),
        parameters={
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Kurzer Titel (z.B. 'Scroll-Bug durch sessionStorage')",
                },
                "problem": {
                    "type": "string",
                    "description": "Was war das Problem?",
                },
                "solution": {
                    "type": "string",
                    "description": "Was war die Lösung?",
                },
                "lesson": {
                    "type": "string",
                    "description": "Was haben wir daraus gelernt?",
                },
            },
            "required": ["title", "problem", "solution"],
        },
    )
    async def bookstack_log_lesson(
        title: str = "", problem: str = "", solution: str = "", lesson: str = "", **ctx,
    ) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "BookStack nicht konfiguriert."})
        from datetime import datetime
        now = datetime.now().strftime("%Y-%m-%d %H:%M")
        markdown = f"# {title}\n\n**Datum:** {now}\n\n"
        markdown += f"## Problem\n{problem}\n\n"
        markdown += f"## Lösung\n{solution}\n\n"
        if lesson:
            markdown += f"## Lesson Learned\n{lesson}\n\n"
        try:
            async with client:
                # "Lessons Learned" Buch finden
                r = await client.get("/api/books", params={"count": 50})
                r.raise_for_status()
                books = r.json().get("data", [])
                book_id = None
                for b in books:
                    if "lesson" in b.get("name", "").lower():
                        book_id = b["id"]
                        break
                if not book_id:
                    # Buch erstellen
                    r_b = await client.post("/api/books", json={
                        "name": "Lessons Learned",
                        "description": "Fehler, Lösungen und was wir daraus gelernt haben",
                    })
                    if r_b.status_code < 300:
                        book_id = r_b.json().get("id")
                if not book_id:
                    return json.dumps({"error": "Konnte 'Lessons Learned' Buch nicht finden/erstellen"})
                r_p = await client.post("/api/pages", json={
                    "name": title,
                    "markdown": markdown,
                    "book_id": book_id,
                    "tags": [{"name": "lesson-learned"}, {"name": "auto-documented"}],
                })
                r_p.raise_for_status()
                p = r_p.json()
                return json.dumps({"created": True, "id": p.get("id"), "name": title, "url": p.get("url", "")})
        except Exception as e:
            logger.warning("bookstack_log_lesson error: %s", e)
            return json.dumps({"error": str(e)})

    logger.info("BookStack Manager Plugin registriert (6 Tools)")
