"""
Nextcloud Manager Plugin für HydraHive

Dateien, Kalender und Kontakte über die Nextcloud OCS- und WebDAV-API verwalten.
Authentifizierung via Basic Auth (Username + App-Passwort).

Nextcloud OCS API: https://docs.nextcloud.com/server/latest/developer_manual/client_apis/OCS/
"""
import json
import logging
from pathlib import Path

logger = logging.getLogger("nextcloud-manager")


def _load_user_config(username: str) -> dict:
    path = Path(f"/etc/hydrahive/user_app_config/{username}/nextcloud-manager.json")
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _get_client(config: dict):
    import httpx
    url = config.get("base_url", "").rstrip("/")
    user = config.get("username", "")
    pw = config.get("app_password", "")
    if not url or not user or not pw:
        return None
    return httpx.AsyncClient(
        base_url=url,
        auth=(user, pw),
        headers={
            "OCS-APIRequest": "true",
            "Accept": "application/json",
        },
        timeout=20,
    )


def register(api):
    """Nextcloud-Plugin beim Core registrieren."""

    @api.tool(
        tool_id="nextcloud_files",
        description=(
            "Dateien und Ordner in Nextcloud auflisten oder nach Dateien suchen. "
            "Ohne path und query: Root-Verzeichnis. Mit path: Ordnerinhalt. Mit query: Dateisuche."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Pfad im Nextcloud-Dateisystem, z.B. 'Documents/'. Leer = Root.",
                },
                "query": {
                    "type": "string",
                    "description": "Suchbegriff für Dateinamen (Volltextsuche via OCS).",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max. Ergebnisse bei Suche (default 20).",
                },
            },
            "required": [],
        },
    )
    async def nextcloud_files(path: str = "", query: str = "", limit: int = 20, **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Nextcloud nicht konfiguriert. Bitte Base URL, Username und App-Passwort eintragen."})
        try:
            async with client:
                if query:
                    # Unified Search
                    r = await client.get(
                        "/ocs/v2.php/search/providers/files/search",
                        params={"term": query, "limit": limit},
                    )
                    r.raise_for_status()
                    data = r.json()
                    entries = data.get("ocs", {}).get("data", {}).get("entries", [])
                    files = [
                        {
                            "name": e.get("title", ""),
                            "path": e.get("resourceUrl", ""),
                            "type": e.get("icon", ""),
                        }
                        for e in entries
                    ]
                    return json.dumps({"query": query, "files": files, "count": len(files)})
                else:
                    # WebDAV PROPFIND
                    user = config.get("username", "")
                    dav_path = f"/remote.php/dav/files/{user}/{path.lstrip('/')}"
                    r = await client.request(
                        "PROPFIND",
                        dav_path,
                        headers={"Depth": "1", "Content-Type": "application/xml"},
                        content=(
                            b'<?xml version="1.0"?>'
                            b'<d:propfind xmlns:d="DAV:">'
                            b'<d:prop><d:displayname/><d:getcontenttype/><d:getlastmodified/>'
                            b'<d:getcontentlength/><d:resourcetype/></d:prop>'
                            b'</d:propfind>'
                        ),
                    )
                    # Parse minimal XML without external deps
                    text = r.text
                    import re
                    names = re.findall(r"<d:displayname>(.*?)</d:displayname>", text)
                    hrefs = re.findall(r"<d:href>(.*?)</d:href>", text)
                    sizes = re.findall(r"<d:getcontentlength>(.*?)</d:getcontentlength>", text)
                    is_dir = [("<d:collection" in seg) for seg in text.split("<d:href>")[1:]]
                    files = []
                    for i, name in enumerate(names):
                        files.append({
                            "name": name,
                            "href": hrefs[i] if i < len(hrefs) else "",
                            "size": sizes[i] if i < len(sizes) else "",
                            "is_directory": is_dir[i] if i < len(is_dir) else False,
                        })
                    return json.dumps({"path": path or "/", "files": files, "count": len(files)})
        except Exception as e:
            logger.warning("nextcloud_files error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="nextcloud_calendar",
        description=(
            "Kalender-Events aus Nextcloud abrufen. Listet Kalender auf oder gibt Events "
            "eines bestimmten Kalenders zurück."
        ),
        parameters={
            "type": "object",
            "properties": {
                "calendar_id": {
                    "type": "string",
                    "description": "ID/Name des Kalenders. Leer = alle Kalender auflisten.",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max. Events (default 20).",
                },
            },
            "required": [],
        },
    )
    async def nextcloud_calendar(calendar_id: str = "", limit: int = 20, **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Nextcloud nicht konfiguriert."})
        user = config.get("username", "")
        try:
            async with client:
                if not calendar_id:
                    # List calendars via CalDAV PROPFIND
                    r = await client.request(
                        "PROPFIND",
                        f"/remote.php/dav/calendars/{user}/",
                        headers={"Depth": "1", "Content-Type": "application/xml"},
                        content=(
                            b'<?xml version="1.0"?>'
                            b'<d:propfind xmlns:d="DAV:" xmlns:cs="http://calendarserver.org/ns/"'
                            b' xmlns:c="urn:ietf:params:xml:ns:caldav">'
                            b'<d:prop><d:displayname/><cs:getctag/></d:prop>'
                            b'</d:propfind>'
                        ),
                    )
                    text = r.text
                    import re
                    names = re.findall(r"<d:displayname>(.*?)</d:displayname>", text)
                    hrefs = re.findall(r"<d:href>(.*?)</d:href>", text)
                    calendars = [
                        {"name": n, "href": h}
                        for n, h in zip(names, hrefs[1:])  # skip root
                        if n
                    ]
                    return json.dumps({"calendars": calendars, "count": len(calendars)})
                else:
                    # Fetch events via CalDAV REPORT
                    cal_path = f"/remote.php/dav/calendars/{user}/{calendar_id}/"
                    r = await client.request(
                        "REPORT",
                        cal_path,
                        headers={
                            "Depth": "1",
                            "Content-Type": "application/xml",
                        },
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
                    text = r.text
                    import re
                    # Extract VEVENT blocks
                    vevent_blocks = re.findall(r"BEGIN:VEVENT(.*?)END:VEVENT", text, re.DOTALL)
                    events = []
                    for block in vevent_blocks[:limit]:
                        def _prop(name, b=block):
                            m = re.search(rf"{name}[^:]*:(.*?)(?:\r?\n(?!\s))", b + "\n", re.DOTALL)
                            return m.group(1).strip().replace("\\n", "\n") if m else ""
                        events.append({
                            "summary": _prop("SUMMARY"),
                            "dtstart": _prop("DTSTART"),
                            "dtend": _prop("DTEND"),
                            "location": _prop("LOCATION"),
                            "description": _prop("DESCRIPTION")[:200],
                            "uid": _prop("UID"),
                        })
                    return json.dumps({"calendar": calendar_id, "events": events, "count": len(events)})
        except Exception as e:
            logger.warning("nextcloud_calendar error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="nextcloud_contacts",
        description="Kontakte in Nextcloud suchen oder auflisten (CardDAV).",
        parameters={
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Suchbegriff (Name, E-Mail). Leer = alle Kontakte.",
                },
                "address_book": {
                    "type": "string",
                    "description": "Adressbuch-ID (default 'contacts').",
                },
                "limit": {
                    "type": "integer",
                    "description": "Max. Ergebnisse (default 20).",
                },
            },
            "required": [],
        },
    )
    async def nextcloud_contacts(query: str = "", address_book: str = "contacts", limit: int = 20, **ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Nextcloud nicht konfiguriert."})
        user = config.get("username", "")
        try:
            async with client:
                book_path = f"/remote.php/dav/addressbooks/users/{user}/{address_book}/"
                r = await client.request(
                    "REPORT",
                    book_path,
                    headers={"Depth": "1", "Content-Type": "application/xml"},
                    content=(
                        b'<?xml version="1.0"?>'
                        b'<card:addressbook-query xmlns:d="DAV:" xmlns:card="urn:ietf:params:xml:ns:carddav">'
                        b'<d:prop><d:getetag/><card:address-data/></d:prop>'
                        b'</card:addressbook-query>'
                    ),
                )
                text = r.text
                import re
                # Extract vCard blocks
                vcard_blocks = re.findall(r"BEGIN:VCARD(.*?)END:VCARD", text, re.DOTALL)
                contacts = []
                for block in vcard_blocks:
                    def _prop(name, b=block):
                        m = re.search(rf"^{name}[^:]*:(.*?)$", b, re.MULTILINE)
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
                return json.dumps({"address_book": address_book, "contacts": contacts, "count": len(contacts)})
        except Exception as e:
            logger.warning("nextcloud_contacts error: %s", e)
            return json.dumps({"error": str(e)})

    @api.tool(
        tool_id="nextcloud_status",
        description="Nextcloud-Serverstatus und eigene Benutzerinfos abrufen.",
        parameters={
            "type": "object",
            "properties": {},
            "required": [],
        },
    )
    async def nextcloud_status(**ctx) -> str:
        config = _load_user_config(ctx.get("_username", "admin"))
        client = _get_client(config)
        if not client:
            return json.dumps({"error": "Nextcloud nicht konfiguriert."})
        try:
            async with client:
                r_status = await client.get("/status.php")
                r_user = await client.get("/ocs/v2.php/cloud/user")
                status = r_status.json() if r_status.status_code == 200 else {}
                user_data = {}
                if r_user.status_code == 200:
                    ocs = r_user.json().get("ocs", {}).get("data", {})
                    user_data = {
                        "id": ocs.get("id", ""),
                        "display_name": ocs.get("display-name", ""),
                        "email": ocs.get("email", ""),
                        "quota_used": ocs.get("quota", {}).get("used", 0),
                        "quota_total": ocs.get("quota", {}).get("total", 0),
                        "groups": ocs.get("groups", []),
                    }
                return json.dumps({
                    "server_version": status.get("version", ""),
                    "server_version_string": status.get("versionstring", ""),
                    "edition": status.get("edition", ""),
                    "product_name": status.get("productname", "Nextcloud"),
                    "maintenance": status.get("maintenance", False),
                    "user": user_data,
                })
        except Exception as e:
            logger.warning("nextcloud_status error: %s", e)
            return json.dumps({"error": str(e)})

    logger.info("Nextcloud Manager Plugin registriert (4 Tools)")
