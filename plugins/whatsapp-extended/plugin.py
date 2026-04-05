"""
whatsapp-extended Plugin — WhatsApp Bridge (mautrix/whatsmeow kompatibel).

Tools:
  - whatsapp_send:     Nachricht senden
  - whatsapp_groups:   Gruppen auflisten
  - whatsapp_contacts: Kontakte auflisten
  - whatsapp_status:   Bridge-Status anzeigen
"""
import json
import urllib.request
import urllib.error
import urllib.parse


def _load_config(username: str, plugin_id: str = "whatsapp-extended") -> dict:
    path = f"/etc/hydrahive/user_app_config/{username}/{plugin_id}.json"
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _api(bridge_url: str, secret: str, path: str, method: str = "GET", body: dict = None) -> dict | list:
    url = bridge_url.rstrip("/") + path
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    if secret:
        req.add_header("Authorization", f"Bearer {secret}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        if not raw:
            return {}
        return json.loads(raw.decode())


def register(api):

    @api.tool(
        tool_id="whatsapp_send",
        description="Sendet eine WhatsApp-Nachricht über die Bridge an eine Nummer oder Gruppe.",
        parameters={
            "type": "object",
            "properties": {
                "recipient": {"type": "string", "description": "Empfänger: Telefonnummer (49151...) oder Gruppen-JID (xxx@g.us)"},
                "message": {"type": "string", "description": "Nachrichtentext"},
                "bridge_url": {"type": "string", "description": "Bridge URL (optional, sonst aus Config)"},
                "bridge_secret": {"type": "string", "description": "Bridge Secret/API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": ["recipient", "message"],
        },
    )
    def whatsapp_send(recipient: str, message: str, bridge_url: str = "", bridge_secret: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        bridge_url = bridge_url or cfg.get("bridge_url", "")
        secret = bridge_secret or cfg.get("bridge_secret", "")
        if not bridge_url:
            return "Fehler: bridge_url benötigt"
        # Normalize recipient to JID format
        jid = recipient if "@" in recipient else f"{recipient}@s.whatsapp.net"
        try:
            result = _api(bridge_url, secret, "/send", method="POST", body={
                "to": jid,
                "text": message,
            })
            if result.get("success") or result.get("status") == "sent":
                msg_id = result.get("message_id", result.get("id", "?"))
                return f"WhatsApp-Nachricht gesendet an {jid}. Message-ID: {msg_id}"
            return f"Antwort: {json.dumps(result)[:300]}"
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            return f"HTTP Fehler {e.code}: {body[:200]}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="whatsapp_groups",
        description="Listet alle WhatsApp-Gruppen auf, denen der verknüpfte Account angehört.",
        parameters={
            "type": "object",
            "properties": {
                "filter": {"type": "string", "description": "Optionaler Suchbegriff für Gruppenname"},
                "bridge_url": {"type": "string", "description": "Bridge URL (optional, sonst aus Config)"},
                "bridge_secret": {"type": "string", "description": "Bridge Secret/API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def whatsapp_groups(filter: str = "", bridge_url: str = "", bridge_secret: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        bridge_url = bridge_url or cfg.get("bridge_url", "")
        secret = bridge_secret or cfg.get("bridge_secret", "")
        if not bridge_url:
            return "Fehler: bridge_url benötigt"
        try:
            result = _api(bridge_url, secret, "/groups")
            groups = result if isinstance(result, list) else result.get("groups", result.get("data", []))
            if filter:
                fl = filter.lower()
                groups = [g for g in groups if fl in str(g.get("name", g.get("subject", ""))).lower()]
            if not groups:
                return "Keine Gruppen gefunden"
            lines = [f"**WhatsApp Gruppen ({len(groups)}):**", ""]
            for g in groups[:50]:
                name = g.get("name", g.get("subject", g.get("id", "?")))
                gid = g.get("id", g.get("jid", "?"))
                members = g.get("participant_count", g.get("members", "?"))
                lines.append(f"  **{name}**")
                lines.append(f"    JID: {gid} | Mitglieder: {members}")
            if len(groups) > 50:
                lines.append(f"  ... und {len(groups) - 50} weitere")
            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            return f"HTTP Fehler {e.code}: {body[:200]}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="whatsapp_contacts",
        description="Listet WhatsApp-Kontakte des verknüpften Accounts auf.",
        parameters={
            "type": "object",
            "properties": {
                "filter": {"type": "string", "description": "Optionaler Suchbegriff für Name oder Nummer"},
                "limit": {"type": "integer", "description": "Maximale Anzahl (default: 50)"},
                "bridge_url": {"type": "string", "description": "Bridge URL (optional, sonst aus Config)"},
                "bridge_secret": {"type": "string", "description": "Bridge Secret/API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def whatsapp_contacts(filter: str = "", limit: int = 50, bridge_url: str = "", bridge_secret: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        bridge_url = bridge_url or cfg.get("bridge_url", "")
        secret = bridge_secret or cfg.get("bridge_secret", "")
        if not bridge_url:
            return "Fehler: bridge_url benötigt"
        try:
            result = _api(bridge_url, secret, "/contacts")
            contacts = result if isinstance(result, list) else result.get("contacts", result.get("data", []))
            if filter:
                fl = filter.lower()
                contacts = [c for c in contacts if fl in str(c.get("name", "")).lower() or fl in str(c.get("jid", c.get("phone", ""))).lower()]
            limit = min(limit, 200)
            lines = [f"**WhatsApp Kontakte ({len(contacts)}, zeige {min(len(contacts), limit)}):**", ""]
            for c in contacts[:limit]:
                name = c.get("name", c.get("push_name", "?"))
                jid = c.get("jid", c.get("phone", "?"))
                # Strip @s.whatsapp.net for display
                phone = jid.replace("@s.whatsapp.net", "") if "@s.whatsapp.net" in jid else jid
                lines.append(f"  {name:30}  {phone}")
            if len(contacts) > limit:
                lines.append(f"  ... und {len(contacts) - limit} weitere (Filter verfeinern)")
            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            return f"HTTP Fehler {e.code}: {body[:200]}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="whatsapp_status",
        description="Zeigt den Status der WhatsApp Bridge: Verbindung, eingeloggter Account und Bridge-Version.",
        parameters={
            "type": "object",
            "properties": {
                "bridge_url": {"type": "string", "description": "Bridge URL (optional, sonst aus Config)"},
                "bridge_secret": {"type": "string", "description": "Bridge Secret/API Key (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": [],
        },
    )
    def whatsapp_status(bridge_url: str = "", bridge_secret: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        bridge_url = bridge_url or cfg.get("bridge_url", "")
        secret = bridge_secret or cfg.get("bridge_secret", "")
        if not bridge_url:
            return "Fehler: bridge_url benötigt"
        try:
            result = _api(bridge_url, secret, "/status")
            state = result.get("state", result.get("status", "?"))
            connected = result.get("connected", result.get("is_connected", False))
            phone = result.get("phone", result.get("jid", result.get("user", "?")))
            version = result.get("version", result.get("bridge_version", "?"))
            battery = result.get("battery_level", None)
            platform = result.get("platform", "")
            lines = [
                "**WhatsApp Bridge Status:**",
                "",
                f"Verbindung:  {'Verbunden' if connected else 'Getrennt'}",
                f"Status:      {state}",
                f"Account:     {phone}",
                f"Version:     {version}",
            ]
            if platform:
                lines.append(f"Platform:    {platform}")
            if battery is not None:
                lines.append(f"Akku:        {battery}%")
            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            return f"HTTP Fehler {e.code}: {body[:200]}"
        except Exception as e:
            return f"Fehler: {e}"
