"""
Monica CRM Plugin für HydraHive

Persönliches Kontakt- und Beziehungsmanagement über die Monica API.
Ermöglicht Agenten Kontakte zu suchen, Aktivitäten zu loggen,
Erinnerungen zu setzen und Notizen zu verwalten.

Monica API: https://www.monicahq.com/api
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger("monica-crm")


def _load_user_config(username: str) -> dict:
    path = Path(f"/etc/hydrahive/user_app_config/{username}/monica-crm.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _get_client(config: dict):
    import httpx
    url = config.get("monica_url", "").rstrip("/")
    token = config.get("monica_token", "")
    if not url or not token:
        return None
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    return httpx.AsyncClient(base_url=url, headers=headers, timeout=15)


def register(api):
    """Plugin beim Core registrieren — @api.tool() Dekorator-Pattern."""

    @api.tool(
        tool_id="monica_contacts",
        description="Kontakte suchen oder auflisten. Ohne query: letzte Kontakte. Mit query: Suche nach Name/Firma.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Suchbegriff (Name, Firma). Leer = letzte Kontakte."},
                "limit": {"type": "integer", "description": "Max. Ergebnisse (default 10)"},
            },
            "required": [],
        },
    )
    async def monica_contacts(query: str = "", limit: int = 10, **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Monica nicht konfiguriert. Bitte URL und API-Token in den Plugin-Einstellungen eintragen."})
        try:
            async with client:
                params = {"query": query, "limit": limit} if query else {"limit": limit, "sort": "updated_at"}
                r = await client.get("/api/contacts", params=params)
                r.raise_for_status()
                data = r.json()
            contacts = [{
                "id": c.get("id"),
                "name": f"{c.get('first_name', '')} {c.get('last_name', '')}".strip(),
                "nickname": c.get("nickname", ""),
                "company": c.get("information", {}).get("career", {}).get("company", ""),
                "last_activity": c.get("last_activity_together"),
            } for c in data.get("data", [])]
            return json.dumps({"contacts": contacts, "total": data.get("meta", {}).get("total", len(contacts))})
        except Exception as e:
            logger.warning("Monica contacts error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="monica_contact_detail",
        description="Detailinfos zu einem Kontakt: Telefon, E-Mail, Geburtstag, Firma, Tags, Notizen.",
        parameters={
            "type": "object",
            "properties": {
                "contact_id": {"type": "integer", "description": "ID des Kontakts"},
            },
            "required": ["contact_id"],
        },
    )
    async def monica_contact_detail(contact_id: int = 0, **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Monica nicht konfiguriert."})
        if not contact_id:
            return json.dumps({"error": "contact_id fehlt."})
        try:
            async with client:
                r = await client.get(f"/api/contacts/{contact_id}")
                r.raise_for_status()
                c = r.json().get("data", {})
                info = c.get("information") or {}
                phone_data = (info.get("phone_numbers") or {}).get("data") or []
                phones = [{"label": p.get("name", ""), "number": p.get("content", "")} for p in phone_data]
                cf_data = (info.get("contact_fields") or {}).get("data") or []
                emails = [e.get("content", "") for e in cf_data
                          if "email" in (e.get("contact_field_type") or {}).get("type", "").lower()]
                career = info.get("career") or {}
                dates = info.get("dates") or {}
                birthdate = (dates.get("birthdate") or {}).get("date", "")
                tags_data = (c.get("tags") or {}).get("data") or []
                return json.dumps({
                    "id": c.get("id"),
                    "name": f"{c.get('first_name', '')} {c.get('last_name', '')}".strip(),
                    "nickname": c.get("nickname", ""),
                    "gender": c.get("gender", ""),
                    "birthday": birthdate,
                    "company": career.get("company", ""),
                    "job_title": career.get("job", ""),
                    "phones": phones, "emails": emails,
                    "notes": c.get("description", ""),
                    "tags": [t.get("name", "") for t in tags_data],
                    "last_activity": c.get("last_activity_together"),
                })
        except Exception as e:
            logger.warning("Monica contact detail error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="monica_add_activity",
        description="Eine Interaktion mit einem Kontakt dokumentieren (Telefonat, Treffen, E-Mail etc.).",
        parameters={
            "type": "object",
            "properties": {
                "contact_id": {"type": "integer", "description": "ID des Kontakts"},
                "summary": {"type": "string", "description": "Kurzbeschreibung der Aktivität"},
                "description": {"type": "string", "description": "Detailbeschreibung (optional)"},
                "date": {"type": "string", "description": "Datum YYYY-MM-DD (optional, default heute)"},
            },
            "required": ["contact_id", "summary"],
        },
    )
    async def monica_add_activity(contact_id: int = 0, summary: str = "", description: str = "", date: str = "", **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Monica nicht konfiguriert."})
        if not contact_id or not summary:
            return json.dumps({"error": "contact_id und summary sind Pflicht."})
        try:
            async with client:
                body = {"summary": summary, "description": description, "happened_at": date or "", "contacts": [contact_id]}
                r = await client.post("/api/activities", json=body)
                r.raise_for_status()
                act = r.json().get("data", {})
                return json.dumps({"created": True, "activity_id": act.get("id"), "summary": summary})
        except Exception as e:
            logger.warning("Monica add activity error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="monica_activities",
        description="Letzte Aktivitäten/Interaktionen mit einem Kontakt oder allen Kontakten auflisten.",
        parameters={
            "type": "object",
            "properties": {
                "contact_id": {"type": "integer", "description": "Kontakt-ID (optional, leer = alle)"},
                "limit": {"type": "integer", "description": "Max. Ergebnisse (default 10)"},
            },
            "required": [],
        },
    )
    async def monica_activities(contact_id: int = 0, limit: int = 10, **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Monica nicht konfiguriert."})
        try:
            async with client:
                url = f"/api/contacts/{contact_id}/activities" if contact_id else "/api/activities"
                r = await client.get(url, params={"limit": limit})
                r.raise_for_status()
                data = r.json()
            activities = [{
                "id": a.get("id"), "summary": a.get("summary", ""),
                "description": a.get("description", ""), "date": a.get("happened_at", ""),
            } for a in data.get("data", [])]
            return json.dumps({"activities": activities, "total": data.get("meta", {}).get("total", len(activities))})
        except Exception as e:
            logger.warning("Monica activities error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="monica_reminders",
        description="Anstehende Erinnerungen abrufen — Geburtstage, Follow-ups, Termine.",
        parameters={
            "type": "object",
            "properties": {
                "contact_id": {"type": "integer", "description": "Kontakt-ID (optional, leer = alle)"},
            },
            "required": [],
        },
    )
    async def monica_reminders(contact_id: int = 0, **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Monica nicht konfiguriert."})
        try:
            async with client:
                url = f"/api/contacts/{contact_id}/reminders" if contact_id else "/api/reminders"
                r = await client.get(url)
                r.raise_for_status()
                data = r.json()
            reminders = [{
                "id": rem.get("id"), "title": rem.get("title", ""),
                "date": rem.get("next_expected_date", ""), "frequency": rem.get("frequency_type", ""),
                "contact": f"{rem.get('contact', {}).get('first_name', '')} {rem.get('contact', {}).get('last_name', '')}".strip(),
            } for rem in data.get("data", [])]
            return json.dumps({"reminders": reminders, "total": len(reminders)})
        except Exception as e:
            logger.warning("Monica reminders error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="monica_add_note",
        description="Eine Notiz zu einem Kontakt speichern.",
        parameters={
            "type": "object",
            "properties": {
                "contact_id": {"type": "integer", "description": "ID des Kontakts"},
                "body": {"type": "string", "description": "Inhalt der Notiz (Markdown)"},
            },
            "required": ["contact_id", "body"],
        },
    )
    async def monica_add_note(contact_id: int = 0, body: str = "", **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Monica nicht konfiguriert."})
        if not contact_id or not body:
            return json.dumps({"error": "contact_id und body sind Pflicht."})
        try:
            async with client:
                r = await client.post(f"/api/contacts/{contact_id}/notes", json={"body": body, "is_favorited": False})
                r.raise_for_status()
                note = r.json().get("data", {})
                return json.dumps({"created": True, "note_id": note.get("id")})
        except Exception as e:
            logger.warning("Monica add note error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="monica_search",
        description="Globale Suche über alle Kontakte nach Name, Firma oder anderen Feldern.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Suchbegriff"},
            },
            "required": ["query"],
        },
    )
    async def monica_search(query: str = "", **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Monica nicht konfiguriert."})
        if not query:
            return json.dumps({"error": "query fehlt."})
        try:
            async with client:
                r = await client.get("/api/contacts", params={"query": query, "limit": 20})
                r.raise_for_status()
                data = r.json()
            results = [{
                "id": c.get("id"),
                "name": f"{c.get('first_name', '')} {c.get('last_name', '')}".strip(),
                "company": c.get("information", {}).get("career", {}).get("company", ""),
            } for c in data.get("data", [])]
            return json.dumps({"results": results, "total": data.get("meta", {}).get("total", len(results))})
        except Exception as e:
            logger.warning("Monica search error: %s", e)
            return json.dumps({"error": str(e)})

    logger.info("Monica CRM Plugin registriert (7 Tools)")
