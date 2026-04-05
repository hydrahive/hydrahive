"""
Radicale Manager Plugin für HydraHive

Kalender und Kontakte über einen Radicale CalDAV/CardDAV-Server verwalten.
Authentifizierung via Basic Auth. Kommunikation über urllib (stdlib).

Radicale: https://radicale.org/
"""
import json
import logging
import re
from pathlib import Path

logger = logging.getLogger("radicale-manager")


def _load_user_config(username: str) -> dict:
    path = Path(f"/etc/hydrahive/user_app_config/{username}/radicale-manager.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _get_client(config: dict):
    import httpx
    url = config.get("base_url", "").rstrip("/")
    user = config.get("username", "")
    pw = config.get("password", "")
    if not url or not user or not pw:
        return None
    return httpx.AsyncClient(
        base_url=url,
        auth=(user, pw),
        headers={"Content-Type": "application/xml"},
        timeout=20,
    )


def _parse_vevents(text: str, limit: int = 50) -> list:
    """Minimaler iCalendar-Parser ohne externe Abhängigkeiten."""
    blocks = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.DOTALL)
    events = []
    for block in blocks[:limit]:
        def _prop(name, b=block):
            m = re.search(rf"^{name}[^:;]*[;:](.*?)$", b, re.MULTILINE)
            return m.group(1).strip() if m else ""
        events.append({
            "summary": _prop("SUMMARY"),
            "dtstart": _prop("DTSTART"),
            "dtend": _prop("DTEND"),
            "location": _prop("LOCATION"),
            "description": _prop("DESCRIPTION")[:300],
            "uid": _prop("UID"),
            "status": _prop("STATUS"),
        })
    return events


def _parse_vcards(text: str, query: str = "", limit: int = 50) -> list:
    """Minimaler vCard-Parser."""
    blocks = re.findall(r"BEGIN:VCARD(.*?)END:VCARD", text, re.DOTALL)
    contacts = []
    for block in blocks:
        def _prop(name, b=block):
            m = re.search(rf"^{name}[^:;]*[;:](.*?)$", b, re.MULTILINE)
            return m.group(1).strip() if m else ""
        fn = _prop("FN")
        email = _prop("EMAIL")
        tel = _prop("TEL")
        org = _prop("ORG")
        if query and query.lower() not in fn.lower() and query.lower() not in email.lower():
            continue
        contacts.append({"name": fn, "email": email, "phone": tel, "org": org})
        if len(contacts) >= limit:
            break
    return contacts


def register(api):
    """Radicale-Plugin beim Core registrieren."""

    @api.tool(
        tool_id="radicale_calendars",
        description=(
            "Alle Kalender auf dem Radicale-Server auflisten. "
            "Gibt Kalender-IDs zurück, die für radicale_events benötigt werden."
        ),
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )
    async def radicale_calendars(**ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Radicale nicht konfiguriert. Bitte Base URL, Username und Passwort eintragen."})
        user = config.get("username", "")
        try:
            async with client:
                r = await client.request(
                    "PROPFIND",
                    f"/{user}/",
                    headers={"Depth": "1"},
                    content=(
                        b'<?xml version="1.0"?>'
                        b'<d:propfind xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
                        b'<d:prop><d:displayname/><d:resourcetype/>'
                        b'<c:calendar-description/></d:prop>'
                        b'</d:propfind>'
                    ),
                )
                text = r.text
                hrefs = re.findall(r"<d:href>(.*?)</d:href>", text)
                names = re.findall(r"<d:displayname>(.*?)</d:displayname>", text)
                is_cal = ["<cal:calendar" in seg or "urn:ietf:params:xml:ns:caldav" in seg
                          for seg in text.split("<d:response>")[1:]]
                calendars = []
                for i, href in enumerate(hrefs[1:], 0):  # skip root
                    name = names[i] if i < len(names) else href.rstrip("/").split("/")[-1]
                    if name:
                        calendars.append({"id": href.rstrip("/").split("/")[-1], "name": name, "href": href})
                return json.dumps({"calendars": calendars, "count": len(calendars)})
        except Exception as e:
            logger.warning("radicale_calendars error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="radicale_events",
        description="Events eines bestimmten Radicale-Kalenders abrufen.",
        parameters={
            "type": "object",
            "properties": {
                "calendar_id": {
                    "type": "string",
                    "description": "Kalender-ID (aus radicale_calendars).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max. Events (default 20).",
                },
            },
            "required": ["calendar_id"],
        },
    )
    async def radicale_events(calendar_id: str = "", limit: int = 20, **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Radicale nicht konfiguriert."})
        if not calendar_id:
            return json.dumps({"error": "calendar_id ist Pflicht."})
        user = config.get("username", "")
        try:
            async with client:
                r = await client.request(
                    "REPORT",
                    f"/{user}/{calendar_id}/",
                    headers={"Depth": "1"},
                    content=(
                        b'<?xml version="1.0"?>'
                        b'<c:calendar-query xmlns:d="DAV:" xmlns:c="urn:ietf:params:xml:ns:caldav">'
                        b'<d:prop><d:getetag/><c:calendar-data/></d:prop>'
                        b'<c:filter><c:comp-filter name="VCALENDAR">'
                        b'<c:comp-filter name="VEVENT"/>'
                        b'</c:comp-filter></c:filter>'
                        b'</c:calendar-query>'
                    ),
                )
                events = _parse_vevents(r.text, limit)
                return json.dumps({"calendar": calendar_id, "events": events, "count": len(events)})
        except Exception as e:
            logger.warning("radicale_events error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="radicale_contacts",
        description="Kontakte aus einem Radicale-Adressbuch abrufen oder suchen.",
        parameters={
            "type": "object",
            "properties": {
                "address_book": {
                    "type": "string",
                    "description": "Adressbuch-ID (aus radicale_calendars Listing).",
                },
                "query": {
                    "type": "string",
                    "description": "Suchbegriff (Name oder E-Mail). Leer = alle.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max. Ergebnisse (default 20).",
                },
            },
            "required": ["address_book"],
        },
    )
    async def radicale_contacts(address_book: str = "", query: str = "", limit: int = 20, **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Radicale nicht konfiguriert."})
        if not address_book:
            return json.dumps({"error": "address_book ist Pflicht."})
        user = config.get("username", "")
        try:
            async with client:
                r = await client.request(
                    "REPORT",
                    f"/{user}/{address_book}/",
                    headers={"Depth": "1"},
                    content=(
                        b'<?xml version="1.0"?>'
                        b'<card:addressbook-query xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">'
                        b'<d:prop><d:getetag/><card:address-data/></d:prop>'
                        b'</card:addressbook-query>'
                    ),
                )
                contacts = _parse_vcards(r.text, query, limit)
                return json.dumps({"address_book": address_book, "contacts": contacts, "count": len(contacts)})
        except Exception as e:
            logger.warning("radicale_contacts error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="radicale_create_event",
        description="Einen neuen Termin in einem Radicale-Kalender anlegen.",
        parameters={
            "type": "object",
            "properties": {
                "calendar_id": {
                    "type": "string",
                    "description": "Ziel-Kalender-ID.",
                },
                "summary": {
                    "type": "string",
                    "description": "Titel/Betreff des Termins.",
                },
                "dtstart": {
                    "type": "string",
                    "description": "Startzeit im Format YYYYMMDDTHHMMSS oder YYYYMMDD (ganztägig).",
                },
                "dtend": {
                    "type": "string",
                    "description": "Endzeit im gleichen Format wie dtstart.",
                },
                "location": {
                    "type": "string",
                    "description": "Ort (optional).",
                },
                "description": {
                    "type": "string",
                    "description": "Beschreibung (optional).",
                },
            },
            "required": ["calendar_id", "summary", "dtstart", "dtend"],
        },
    )
    async def radicale_create_event(
        calendar_id: str = "",
        summary: str = "",
        dtstart: str = "",
        dtend: str = "",
        location: str = "",
        description: str = "",
        **ctx,
    ) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Radicale nicht konfiguriert."})
        if not calendar_id or not summary or not dtstart or not dtend:
            return json.dumps({"error": "calendar_id, summary, dtstart und dtend sind Pflicht."})
        import uuid as _uuid
        uid = str(_uuid.uuid4())
        ical_lines = [
            "BEGIN:VCALENDAR",
            "VERSION:2.0",
            "PRODID:-//HydraHive//radicale-manager//EN",
            "BEGIN:VEVENT",
            f"UID:{uid}",
            f"SUMMARY:{summary}",
            f"DTSTART:{dtstart}",
            f"DTEND:{dtend}",
        ]
        if location:
            ical_lines.append(f"LOCATION:{location}")
        if description:
            ical_lines.append(f"DESCRIPTION:{description}")
        ical_lines += ["END:VEVENT", "END:VCALENDAR"]
        ical_data = "\r\n".join(ical_lines).encode("utf-8")
        user = config.get("username", "")
        try:
            async with client:
                r = await client.put(
                    f"/{user}/{calendar_id}/{uid}.ics",
                    headers={"Content-Type": "text/calendar; charset=utf-8"},
                    content=ical_data,
                )
                if r.status_code in (201, 204):
                    return json.dumps({"created": True, "uid": uid, "summary": summary})
                return json.dumps({"error": f"HTTP {r.status_code}: {r.text[:200]}"})
        except Exception as e:
            logger.warning("radicale_create_event error: %s", e)
            return json.dumps({"error": str(e)})

    logger.info("Radicale Manager Plugin registriert (4 Tools)")
