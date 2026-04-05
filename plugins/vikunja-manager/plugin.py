"""
Vikunja Manager Plugin für HydraHive

Aufgaben und Projekte über die Vikunja REST API verwalten.
Authentifizierung via API-Token (Bearer).

Vikunja API: https://vikunja.io/docs/api/
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger("vikunja-manager")


def _load_user_config(username: str) -> dict:
    path = Path(f"/etc/hydrahive/user_app_config/{username}/vikunja-manager.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _get_client(config: dict):
    import httpx
    url = config.get("base_url", "").rstrip("/")
    token = config.get("api_token", "")
    if not url or not token:
        return None
    return httpx.AsyncClient(
        base_url=url,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        timeout=20,
    )


def register(api):
    """Vikunja-Plugin beim Core registrieren."""

    @api.tool(
        tool_id="vikunja_projects",
        description="Alle Vikunja-Projekte (Listen) auflisten.",
        parameters={
            "type": "object",
            "properties": {
                "page": {
                    "type": "integer",
                    "description": "Seite (default 1).",
                },
            },
            "required": [],
        },
    )
    async def vikunja_projects(page: int = 1, **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Vikunja nicht konfiguriert. Bitte Base URL und API-Token eintragen."})
        try:
            async with client:
                r = await client.get("/api/v1/projects", params={"page": page, "per_page": 50})
                r.raise_for_status()
                data = r.json()
            projects = [
                {
                    "id": p.get("id"),
                    "title": p.get("title", ""),
                    "description": (p.get("description") or "")[:150],
                    "is_archived": p.get("is_archived", False),
                    "created": p.get("created", ""),
                    "updated": p.get("updated", ""),
                }
                for p in (data if isinstance(data, list) else [])
            ]
            return json.dumps({"projects": projects, "count": len(projects), "page": page})
        except Exception as e:
            logger.warning("vikunja_projects error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="vikunja_tasks",
        description=(
            "Aufgaben aus einem Vikunja-Projekt abrufen oder alle offenen Tasks suchen. "
            "Ohne project_id: globale Aufgabensuche."
        ),
        parameters={
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "integer",
                    "description": "Projekt-ID (aus vikunja_projects). Leer = alle Tasks.",
                },
                "query": {
                    "type": "string",
                    "description": "Suchbegriff im Titel (optional).",
                },
                "done": {
                    "type": "boolean",
                    "description": "Erledigte Tasks einschließen (default false).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max. Ergebnisse (default 20).",
                },
            },
            "required": [],
        },
    )
    async def vikunja_tasks(
        project_id: int = 0,
        query: str = "",
        done: bool = False,
        limit: int = 20,
        **ctx,
    ) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Vikunja nicht konfiguriert."})
        try:
            async with client:
                params: dict = {"per_page": limit}
                if query:
                    params["s"] = query
                if not done:
                    params["filter_by"] = "done"
                    params["filter_value"] = "false"
                    params["filter_comparator"] = "equals"
                if project_id:
                    url = f"/api/v1/projects/{project_id}/tasks"
                else:
                    url = "/api/v1/tasks/all"
                r = await client.get(url, params=params)
                r.raise_for_status()
                data = r.json()
            tasks = [
                {
                    "id": t.get("id"),
                    "title": t.get("title", ""),
                    "description": (t.get("description") or "")[:200],
                    "done": t.get("done", False),
                    "priority": t.get("priority", 0),
                    "due_date": t.get("due_date", ""),
                    "project_id": t.get("project_id"),
                    "assignees": [a.get("name", a.get("username", "")) for a in (t.get("assignees") or [])],
                    "labels": [lbl.get("title", "") for lbl in (t.get("labels") or [])],
                }
                for t in (data if isinstance(data, list) else [])
            ]
            return json.dumps({"tasks": tasks, "count": len(tasks)})
        except Exception as e:
            logger.warning("vikunja_tasks error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="vikunja_create_task",
        description="Eine neue Aufgabe in einem Vikunja-Projekt erstellen.",
        parameters={
            "type": "object",
            "properties": {
                "project_id": {
                    "type": "integer",
                    "description": "Ziel-Projekt-ID.",
                },
                "title": {
                    "type": "string",
                    "description": "Titel der Aufgabe.",
                },
                "description": {
                    "type": "string",
                    "description": "Beschreibung (Markdown, optional).",
                },
                "due_date": {
                    "type": "string",
                    "description": "Fälligkeitsdatum ISO8601, z.B. 2024-12-31T00:00:00Z (optional).",
                },
                "priority": {
                    "type": "integer",
                    "description": "Priorität 0-5 (0=keine, 5=kritisch, optional).",
                },
            },
            "required": ["project_id", "title"],
        },
    )
    async def vikunja_create_task(
        project_id: int = 0,
        title: str = "",
        description: str = "",
        due_date: str = "",
        priority: int = 0,
        **ctx,
    ) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Vikunja nicht konfiguriert."})
        if not project_id or not title:
            return json.dumps({"error": "project_id und title sind Pflicht."})
        body: dict = {"title": title}
        if description:
            body["description"] = description
        if due_date:
            body["due_date"] = due_date
        if priority:
            body["priority"] = priority
        try:
            async with client:
                r = await client.post(f"/api/v1/projects/{project_id}/tasks", json=body)
                r.raise_for_status()
                task = r.json()
                return json.dumps({
                    "created": True,
                    "id": task.get("id"),
                    "title": task.get("title", ""),
                    "project_id": project_id,
                })
        except Exception as e:
            logger.warning("vikunja_create_task error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="vikunja_update_task",
        description="Eine bestehende Vikunja-Aufgabe aktualisieren (Titel, Status, Priorität, Fälligkeitsdatum).",
        parameters={
            "type": "object",
            "properties": {
                "task_id": {
                    "type": "integer",
                    "description": "ID der Aufgabe.",
                },
                "title": {
                    "type": "string",
                    "description": "Neuer Titel (optional).",
                },
                "description": {
                    "type": "string",
                    "description": "Neue Beschreibung (optional).",
                },
                "done": {
                    "type": "boolean",
                    "description": "Als erledigt markieren.",
                },
                "due_date": {
                    "type": "string",
                    "description": "Neues Fälligkeitsdatum ISO8601 (optional).",
                },
                "priority": {
                    "type": "integer",
                    "description": "Neue Priorität 0-5 (optional).",
                },
            },
            "required": ["task_id"],
        },
    )
    async def vikunja_update_task(
        task_id: int = 0,
        title: str = "",
        description: str = "",
        done: bool = None,
        due_date: str = "",
        priority: int = None,
        **ctx,
    ) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Vikunja nicht konfiguriert."})
        if not task_id:
            return json.dumps({"error": "task_id ist Pflicht."})
        try:
            async with client:
                # Fetch current task first
                r_get = await client.get(f"/api/v1/tasks/{task_id}")
                r_get.raise_for_status()
                current = r_get.json()
                # Merge changes
                body = {
                    "title": title or current.get("title", ""),
                    "description": description if description else current.get("description", ""),
                    "done": done if done is not None else current.get("done", False),
                    "due_date": due_date or current.get("due_date", ""),
                    "priority": priority if priority is not None else current.get("priority", 0),
                }
                r = await client.post(f"/api/v1/tasks/{task_id}", json=body)
                r.raise_for_status()
                task = r.json()
                return json.dumps({
                    "updated": True,
                    "id": task.get("id"),
                    "title": task.get("title", ""),
                    "done": task.get("done", False),
                })
        except Exception as e:
            logger.warning("vikunja_update_task error: %s", e)
            return json.dumps({"error": str(e)})

    logger.info("Vikunja Manager Plugin registriert (4 Tools)")
