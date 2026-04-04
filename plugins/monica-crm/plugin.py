"""
Monica CRM Plugin für HydraHive

Persönliches Kontakt- und Beziehungsmanagement über die Monica API.
Ermöglicht Agenten Kontakte zu suchen, Aktivitäten zu loggen,
Erinnerungen zu setzen und Notizen zu verwalten.

Monica API v3: https://www.monicahq.com/api
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger("monica-crm")


def _load_user_config(username: str) -> dict:
    """Lädt die User-spezifische Plugin-Config."""
    path = Path(f"/etc/hydrahive/user_app_config/{username}/monica-crm.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _get_client(config: dict):
    """Erstellt einen httpx AsyncClient mit Monica-Auth."""
    import httpx
    url = config.get("monica_url", "").rstrip("/")
    token = config.get("monica_token", "")
    if not url or not token:
        return None, None
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    return httpx.AsyncClient(base_url=url, headers=headers, timeout=15), url


# ── Tools ─────────────────────────────────────────────────────────────────────

async def monica_contacts(agent_id: str, project_id: str, query: str = "", limit: int = 10, **kwargs) -> dict:
    """Kontakte suchen oder auflisten."""
    username = kwargs.get("_username", "admin")
    config = _load_user_config(username)
    client, url = _get_client(config)
    if not client:
        return {"error": "Monica nicht konfiguriert. Bitte URL und API-Token in den Plugin-Einstellungen eintragen."}

    try:
        async with client:
            if query:
                r = await client.get(f"/api/contacts", params={"query": query, "limit": limit})
            else:
                r = await client.get(f"/api/contacts", params={"limit": limit, "sort": "updated_at"})
            r.raise_for_status()
            data = r.json()

        contacts = []
        for c in data.get("data", []):
            contacts.append({
                "id": c.get("id"),
                "name": f"{c.get('first_name', '')} {c.get('last_name', '')}".strip(),
                "nickname": c.get("nickname", ""),
                "company": c.get("information", {}).get("career", {}).get("company", ""),
                "last_activity": c.get("last_activity_together"),
                "stay_in_touch_frequency": c.get("stay_in_touch_frequency_number"),
            })
        return {"contacts": contacts, "total": data.get("meta", {}).get("total", len(contacts))}
    except Exception as e:
        logger.warning("Monica contacts error: %s", e)
        return {"error": f"Monica API Fehler: {e}"}


async def monica_contact_detail(agent_id: str, project_id: str, contact_id: int = 0, **kwargs) -> dict:
    """Detailinfos zu einem Kontakt abrufen."""
    username = kwargs.get("_username", "admin")
    config = _load_user_config(username)
    client, url = _get_client(config)
    if not client:
        return {"error": "Monica nicht konfiguriert."}
    if not contact_id:
        return {"error": "contact_id fehlt."}

    try:
        async with client:
            r = await client.get(f"/api/contacts/{contact_id}")
            r.raise_for_status()
            c = r.json().get("data", {})

            # Telefonnummern
            phones = []
            for p in c.get("information", {}).get("phone_numbers", {}).get("data", []):
                phones.append({"label": p.get("name", ""), "number": p.get("content", "")})

            # E-Mails
            emails = []
            for e in c.get("information", {}).get("contact_fields", {}).get("data", []):
                if "email" in e.get("contact_field_type", {}).get("type", "").lower():
                    emails.append(e.get("content", ""))

            return {
                "id": c.get("id"),
                "name": f"{c.get('first_name', '')} {c.get('last_name', '')}".strip(),
                "nickname": c.get("nickname", ""),
                "birthday": c.get("information", {}).get("dates", {}).get("birthdate", {}).get("date", ""),
                "company": c.get("information", {}).get("career", {}).get("company", ""),
                "job_title": c.get("information", {}).get("career", {}).get("title", ""),
                "phones": phones,
                "emails": emails,
                "notes": c.get("description", ""),
                "tags": [t.get("name", "") for t in c.get("tags", {}).get("data", [])],
                "last_activity": c.get("last_activity_together"),
                "created_at": c.get("created_at"),
            }
    except Exception as e:
        logger.warning("Monica contact detail error: %s", e)
        return {"error": f"Monica API Fehler: {e}"}


async def monica_add_activity(agent_id: str, project_id: str, contact_id: int = 0, summary: str = "", description: str = "", date: str = "", **kwargs) -> dict:
    """Eine Aktivität/Interaktion mit einem Kontakt loggen."""
    username = kwargs.get("_username", "admin")
    config = _load_user_config(username)
    client, url = _get_client(config)
    if not client:
        return {"error": "Monica nicht konfiguriert."}
    if not contact_id or not summary:
        return {"error": "contact_id und summary sind Pflicht."}

    try:
        async with client:
            body = {
                "summary": summary,
                "description": description or "",
                "happened_at": date or "",
                "contacts": [contact_id],
            }
            r = await client.post("/api/activities", json=body)
            r.raise_for_status()
            act = r.json().get("data", {})
            return {"created": True, "activity_id": act.get("id"), "summary": summary}
    except Exception as e:
        logger.warning("Monica add activity error: %s", e)
        return {"error": f"Monica API Fehler: {e}"}


async def monica_activities(agent_id: str, project_id: str, contact_id: int = 0, limit: int = 10, **kwargs) -> dict:
    """Aktivitäten eines Kontakts oder aller Kontakte auflisten."""
    username = kwargs.get("_username", "admin")
    config = _load_user_config(username)
    client, url = _get_client(config)
    if not client:
        return {"error": "Monica nicht konfiguriert."}

    try:
        async with client:
            if contact_id:
                r = await client.get(f"/api/contacts/{contact_id}/activities", params={"limit": limit})
            else:
                r = await client.get("/api/activities", params={"limit": limit})
            r.raise_for_status()
            data = r.json()

        activities = []
        for a in data.get("data", []):
            activities.append({
                "id": a.get("id"),
                "summary": a.get("summary", ""),
                "description": a.get("description", ""),
                "date": a.get("happened_at", ""),
                "contacts": [f"{c.get('first_name', '')} {c.get('last_name', '')}".strip() for c in a.get("attendees", {}).get("contacts", [])],
            })
        return {"activities": activities, "total": data.get("meta", {}).get("total", len(activities))}
    except Exception as e:
        logger.warning("Monica activities error: %s", e)
        return {"error": f"Monica API Fehler: {e}"}


async def monica_reminders(agent_id: str, project_id: str, contact_id: int = 0, **kwargs) -> dict:
    """Erinnerungen abrufen (Geburtstage, Termine, Follow-ups)."""
    username = kwargs.get("_username", "admin")
    config = _load_user_config(username)
    client, url = _get_client(config)
    if not client:
        return {"error": "Monica nicht konfiguriert."}

    try:
        async with client:
            if contact_id:
                r = await client.get(f"/api/contacts/{contact_id}/reminders")
            else:
                r = await client.get("/api/reminders")
            r.raise_for_status()
            data = r.json()

        reminders = []
        for rem in data.get("data", []):
            reminders.append({
                "id": rem.get("id"),
                "title": rem.get("title", ""),
                "date": rem.get("next_expected_date", ""),
                "frequency": rem.get("frequency_type", ""),
                "contact": f"{rem.get('contact', {}).get('first_name', '')} {rem.get('contact', {}).get('last_name', '')}".strip(),
            })
        return {"reminders": reminders, "total": len(reminders)}
    except Exception as e:
        logger.warning("Monica reminders error: %s", e)
        return {"error": f"Monica API Fehler: {e}"}


async def monica_add_note(agent_id: str, project_id: str, contact_id: int = 0, body: str = "", **kwargs) -> dict:
    """Eine Notiz zu einem Kontakt hinzufügen."""
    username = kwargs.get("_username", "admin")
    config = _load_user_config(username)
    client, url = _get_client(config)
    if not client:
        return {"error": "Monica nicht konfiguriert."}
    if not contact_id or not body:
        return {"error": "contact_id und body sind Pflicht."}

    try:
        async with client:
            r = await client.post(f"/api/contacts/{contact_id}/notes", json={
                "body": body,
                "is_favorited": False,
            })
            r.raise_for_status()
            note = r.json().get("data", {})
            return {"created": True, "note_id": note.get("id")}
    except Exception as e:
        logger.warning("Monica add note error: %s", e)
        return {"error": f"Monica API Fehler: {e}"}


async def monica_search(agent_id: str, project_id: str, query: str = "", **kwargs) -> dict:
    """Globale Suche über Kontakte, Notizen, Aktivitäten."""
    username = kwargs.get("_username", "admin")
    config = _load_user_config(username)
    client, url = _get_client(config)
    if not client:
        return {"error": "Monica nicht konfiguriert."}
    if not query:
        return {"error": "query fehlt."}

    try:
        async with client:
            r = await client.get("/api/contacts", params={"query": query, "limit": 20})
            r.raise_for_status()
            data = r.json()

        results = []
        for c in data.get("data", []):
            results.append({
                "id": c.get("id"),
                "name": f"{c.get('first_name', '')} {c.get('last_name', '')}".strip(),
                "company": c.get("information", {}).get("career", {}).get("company", ""),
            })
        return {"results": results, "total": data.get("meta", {}).get("total", len(results))}
    except Exception as e:
        logger.warning("Monica search error: %s", e)
        return {"error": f"Monica API Fehler: {e}"}


# ── Plugin Registration ───────────────────────────────────────────────────────

def register(api):
    """Plugin beim Core registrieren."""
    api.register_tool(
        tool_id="monica_contacts",
        name="Monica: Kontakte",
        description="Kontakte suchen oder auflisten. Ohne query: letzte Kontakte. Mit query: Suche nach Name/Firma.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Suchbegriff (Name, Firma). Leer = letzte Kontakte."},
                "limit": {"type": "integer", "description": "Max. Ergebnisse (default 10)"},
            },
            "required": [],
        },
        handler=monica_contacts,
    )
    api.register_tool(
        tool_id="monica_contact_detail",
        name="Monica: Kontakt-Details",
        description="Detailinfos zu einem Kontakt: Telefon, E-Mail, Geburtstag, Firma, Tags, Notizen.",
        parameters={
            "type": "object",
            "properties": {
                "contact_id": {"type": "integer", "description": "ID des Kontakts"},
            },
            "required": ["contact_id"],
        },
        handler=monica_contact_detail,
    )
    api.register_tool(
        tool_id="monica_add_activity",
        name="Monica: Aktivität loggen",
        description="Eine Interaktion mit einem Kontakt dokumentieren (Telefonat, Treffen, E-Mail etc.).",
        parameters={
            "type": "object",
            "properties": {
                "contact_id": {"type": "integer", "description": "ID des Kontakts"},
                "summary": {"type": "string", "description": "Kurzbeschreibung der Aktivität"},
                "description": {"type": "string", "description": "Detailbeschreibung (optional)"},
                "date": {"type": "string", "description": "Datum im Format YYYY-MM-DD (optional, default heute)"},
            },
            "required": ["contact_id", "summary"],
        },
        handler=monica_add_activity,
    )
    api.register_tool(
        tool_id="monica_activities",
        name="Monica: Aktivitäten",
        description="Letzte Aktivitäten/Interaktionen mit einem Kontakt oder allen Kontakten auflisten.",
        parameters={
            "type": "object",
            "properties": {
                "contact_id": {"type": "integer", "description": "Kontakt-ID (optional, leer = alle)"},
                "limit": {"type": "integer", "description": "Max. Ergebnisse (default 10)"},
            },
            "required": [],
        },
        handler=monica_activities,
    )
    api.register_tool(
        tool_id="monica_reminders",
        name="Monica: Erinnerungen",
        description="Anstehende Erinnerungen abrufen — Geburtstage, Follow-ups, Termine.",
        parameters={
            "type": "object",
            "properties": {
                "contact_id": {"type": "integer", "description": "Kontakt-ID (optional, leer = alle)"},
            },
            "required": [],
        },
        handler=monica_reminders,
    )
    api.register_tool(
        tool_id="monica_add_note",
        name="Monica: Notiz hinzufügen",
        description="Eine Notiz zu einem Kontakt speichern.",
        parameters={
            "type": "object",
            "properties": {
                "contact_id": {"type": "integer", "description": "ID des Kontakts"},
                "body": {"type": "string", "description": "Inhalt der Notiz (Markdown)"},
            },
            "required": ["contact_id", "body"],
        },
        handler=monica_add_note,
    )
    api.register_tool(
        tool_id="monica_search",
        name="Monica: Suche",
        description="Globale Suche über alle Kontakte nach Name, Firma oder anderen Feldern.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Suchbegriff"},
            },
            "required": ["query"],
        },
        handler=monica_search,
    )

    logger.info("Monica CRM Plugin registriert (7 Tools)")
