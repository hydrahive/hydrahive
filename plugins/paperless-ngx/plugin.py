"""
Paperless-ngx Plugin for HydraHive

Manage documents in Paperless-ngx via the REST API.
List, search, tag documents and upload new files.

Paperless-ngx API: https://docs.paperless-ngx.com/api/
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger("paperless-ngx")


def _load_user_config(username: str) -> dict:
    path = Path(f"/etc/hydrahive/user_app_config/{username}/paperless-ngx.json")
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
        "Authorization": f"Token {api_token}",
        "Accept": "application/json; version=5",
    }
    return httpx.AsyncClient(base_url=base_url, headers=headers, timeout=30)


def register(api):

    @api.tool(
        tool_id="paperless_documents",
        description="List documents in Paperless-ngx with optional filtering by correspondent, tag or document type.",
        parameters={
            "type": "object",
            "properties": {
                "page": {"type": "integer", "description": "Page number (default 1)"},
                "page_size": {"type": "integer", "description": "Documents per page (default 25)"},
                "correspondent": {"type": "string", "description": "Filter by correspondent name (partial match)"},
                "tag": {"type": "string", "description": "Filter by tag name (partial match)"},
                "ordering": {"type": "string", "description": "Sort field: created, modified, added, title (prefix - for descending, e.g. -created)"},
            },
            "required": [],
        },
    )
    async def paperless_documents(page: int = 1, page_size: int = 25, correspondent: str = "", tag: str = "", ordering: str = "-created", **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Paperless-ngx not configured. Please set base_url and api_token in plugin settings."})
        try:
            async with client:
                params = {"page": page, "page_size": page_size, "ordering": ordering}
                if correspondent:
                    params["correspondent__name__icontains"] = correspondent
                if tag:
                    params["tags__name__icontains"] = tag
                r = await client.get("/api/documents/", params=params)
                r.raise_for_status()
                data = r.json()
            docs = [{"id": d.get("id"), "title": d.get("title", ""), "created": d.get("created", ""), "added": d.get("added", ""), "correspondent": d.get("correspondent"), "tags": d.get("tags", []), "document_type": d.get("document_type")} for d in data.get("results", [])]
            return json.dumps({"documents": docs, "count": data.get("count", 0), "page": page, "total_pages": -(-data.get("count", 0) // page_size)})
        except Exception as e:
            logger.warning("paperless_documents error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="paperless_search",
        description="Full-text search across all Paperless-ngx documents.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Search query (supports Paperless-ngx query syntax)"},
                "limit": {"type": "integer", "description": "Max results to return (default 20)"},
            },
            "required": ["query"],
        },
    )
    async def paperless_search(query: str = "", limit: int = 20, **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Paperless-ngx not configured."})
        if not query:
            return json.dumps({"error": "query is required."})
        try:
            async with client:
                r = await client.get("/api/documents/", params={"query": query, "page_size": limit})
                r.raise_for_status()
                data = r.json()
            docs = [{"id": d.get("id"), "title": d.get("title", ""), "created": d.get("created", ""), "correspondent": d.get("correspondent"), "tags": d.get("tags", []), "score": d.get("__search_hit__", {}).get("score", None)} for d in data.get("results", [])]
            return json.dumps({"results": docs, "count": data.get("count", 0), "query": query})
        except Exception as e:
            logger.warning("paperless_search error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="paperless_tags",
        description="List all tags in Paperless-ngx with document counts.",
        parameters={
            "type": "object",
            "properties": {
                "search": {"type": "string", "description": "Filter tags by name (partial match, optional)"},
            },
            "required": [],
        },
    )
    async def paperless_tags(search: str = "", **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Paperless-ngx not configured."})
        try:
            async with client:
                params = {"page_size": 200}
                if search:
                    params["name__icontains"] = search
                r = await client.get("/api/tags/", params=params)
                r.raise_for_status()
                data = r.json()
            tags = [{"id": t.get("id"), "name": t.get("name", ""), "colour": t.get("colour", 0), "document_count": t.get("document_count", 0)} for t in data.get("results", [])]
            return json.dumps({"tags": tags, "count": len(tags)})
        except Exception as e:
            logger.warning("paperless_tags error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="paperless_upload",
        description="Upload a document to Paperless-ngx by providing a file path on the server or a publicly accessible URL.",
        parameters={
            "type": "object",
            "properties": {
                "file_path": {"type": "string", "description": "Absolute path to the file on the server (e.g. /tmp/invoice.pdf)"},
                "title": {"type": "string", "description": "Document title (optional, Paperless will auto-detect if omitted)"},
                "created": {"type": "string", "description": "Document date YYYY-MM-DD (optional)"},
                "correspondent": {"type": "string", "description": "Correspondent ID (integer, optional)"},
            },
            "required": ["file_path"],
        },
    )
    async def paperless_upload(file_path: str = "", title: str = "", created: str = "", correspondent: str = "", **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        base_url = config.get("base_url", "").rstrip("/")
        api_token = config.get("api_token", "").strip()
        if not base_url or not api_token:
            return json.dumps({"error": "Paperless-ngx not configured."})
        if not file_path:
            return json.dumps({"error": "file_path is required."})
        fpath = Path(file_path)
        if not fpath.exists():
            return json.dumps({"error": f"File not found: {file_path}"})
        try:
            import httpx
            headers = {"Authorization": f"Token {api_token}"}
            with open(fpath, "rb") as f:
                file_content = f.read()
            files = {"document": (fpath.name, file_content, "application/octet-stream")}
            data = {}
            if title:
                data["title"] = title
            if created:
                data["created"] = created
            if correspondent:
                data["correspondent"] = correspondent
            async with httpx.AsyncClient(headers=headers, timeout=60) as client:
                r = await client.post(f"{base_url}/api/documents/post_document/", files=files, data=data)
                r.raise_for_status()
                task_id = r.text.strip().strip('"')
                return json.dumps({"uploaded": True, "task_id": task_id, "file": fpath.name})
        except Exception as e:
            logger.warning("paperless_upload error: %s", e)
            return json.dumps({"error": str(e)})

    logger.info("Paperless-ngx Plugin registered (4 tools)")
