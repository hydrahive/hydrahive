"""
discord-extended Plugin — Discord Bot API (erweiterte Tools).

Tools:
  - discord_send:    Nachricht in einen Channel senden
  - discord_embed:   Embed-Nachricht senden
  - discord_react:   Reaktion auf eine Nachricht hinzufügen
  - discord_threads: Threads in einem Channel auflisten oder erstellen
  - discord_roles:   Server-Rollen anzeigen und Mitglieder-Rollen verwalten
"""
import json
import urllib.request
import urllib.error
import urllib.parse


def _load_config(username: str, plugin_id: str = "discord-extended") -> dict:
    path = f"/etc/hydrahive/user_app_config/{username}/{plugin_id}.json"
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _discord_api(token: str, path: str, method: str = "GET", body: dict = None) -> dict | list:
    url = "https://discord.com/api/v10" + path
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bot {token}")
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as resp:
        raw = resp.read()
        if not raw:
            return {}
        return json.loads(raw.decode())


def register(api):

    @api.tool(
        tool_id="discord_send",
        description="Sendet eine Textnachricht in einen Discord-Channel.",
        parameters={
            "type": "object",
            "properties": {
                "channel_id": {"type": "string", "description": "Discord Channel-ID"},
                "content": {"type": "string", "description": "Nachrichtentext (max. 2000 Zeichen)"},
                "reply_to": {"type": "string", "description": "Message-ID zum Antworten (optional)"},
                "suppress_mentions": {"type": "boolean", "description": "Erwähnungs-Benachrichtigungen unterdrücken (default: false)"},
                "bot_token": {"type": "string", "description": "Discord Bot Token (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": ["channel_id", "content"],
        },
    )
    def discord_send(channel_id: str, content: str, reply_to: str = "", suppress_mentions: bool = False, bot_token: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        token = bot_token or cfg.get("bot_token", "")
        if not token:
            return "Fehler: bot_token benötigt"
        if len(content) > 2000:
            return "Fehler: Nachricht zu lang (max. 2000 Zeichen)"
        payload = {"content": content}
        if suppress_mentions:
            payload["allowed_mentions"] = {"parse": []}
        if reply_to:
            payload["message_reference"] = {"message_id": reply_to}
        try:
            result = _discord_api(token, f"/channels/{channel_id}/messages", method="POST", body=payload)
            if "id" in result:
                return f"Nachricht gesendet in Channel {channel_id}. Message-ID: {result['id']}"
            return f"Antwort: {json.dumps(result)[:300]}"
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            return f"HTTP Fehler {e.code}: {body[:300]}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="discord_embed",
        description="Sendet eine formatierte Embed-Nachricht in einen Discord-Channel.",
        parameters={
            "type": "object",
            "properties": {
                "channel_id": {"type": "string", "description": "Discord Channel-ID"},
                "title": {"type": "string", "description": "Embed-Titel"},
                "description": {"type": "string", "description": "Embed-Beschreibung (Markdown unterstützt)"},
                "color": {"type": "integer", "description": "Embed-Farbe als Dezimalzahl (z.B. 5814783 = blau, 0 = default)"},
                "url": {"type": "string", "description": "URL für den Titel-Link (optional)"},
                "footer": {"type": "string", "description": "Footer-Text (optional)"},
                "image_url": {"type": "string", "description": "Bild-URL (optional)"},
                "fields": {"type": "array", "items": {"type": "object"}, "description": "Felder als [{name, value, inline}] (optional)"},
                "content": {"type": "string", "description": "Text vor dem Embed (optional)"},
                "bot_token": {"type": "string", "description": "Discord Bot Token (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": ["channel_id", "title"],
        },
    )
    def discord_embed(channel_id: str, title: str, description: str = "", color: int = 5814783, url: str = "", footer: str = "", image_url: str = "", fields: list = None, content: str = "", bot_token: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        token = bot_token or cfg.get("bot_token", "")
        if not token:
            return "Fehler: bot_token benötigt"
        embed = {"title": title, "color": color}
        if description:
            embed["description"] = description
        if url:
            embed["url"] = url
        if footer:
            embed["footer"] = {"text": footer}
        if image_url:
            embed["image"] = {"url": image_url}
        if fields:
            embed["fields"] = fields
        payload = {"embeds": [embed]}
        if content:
            payload["content"] = content
        try:
            result = _discord_api(token, f"/channels/{channel_id}/messages", method="POST", body=payload)
            if "id" in result:
                return f"Embed gesendet in Channel {channel_id}. Message-ID: {result['id']}"
            return f"Antwort: {json.dumps(result)[:300]}"
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            return f"HTTP Fehler {e.code}: {body[:300]}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="discord_react",
        description="Fügt eine Reaktion (Emoji) zu einer Discord-Nachricht hinzu oder entfernt sie.",
        parameters={
            "type": "object",
            "properties": {
                "channel_id": {"type": "string", "description": "Discord Channel-ID"},
                "message_id": {"type": "string", "description": "Message-ID"},
                "emoji": {"type": "string", "description": "Emoji (Unicode z.B. '👍' oder Custom-Emoji 'name:id')"},
                "remove": {"type": "boolean", "description": "Reaktion entfernen statt hinzufügen (default: false)"},
                "bot_token": {"type": "string", "description": "Discord Bot Token (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": ["channel_id", "message_id", "emoji"],
        },
    )
    def discord_react(channel_id: str, message_id: str, emoji: str, remove: bool = False, bot_token: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        token = bot_token or cfg.get("bot_token", "")
        if not token:
            return "Fehler: bot_token benötigt"
        # URL-encode emoji
        emoji_encoded = urllib.parse.quote(emoji)
        try:
            if remove:
                _discord_api(token, f"/channels/{channel_id}/messages/{message_id}/reactions/{emoji_encoded}/@me", method="DELETE")
                return f"Reaktion {emoji} von Nachricht {message_id} entfernt."
            else:
                _discord_api(token, f"/channels/{channel_id}/messages/{message_id}/reactions/{emoji_encoded}/@me", method="PUT")
                return f"Reaktion {emoji} zu Nachricht {message_id} hinzugefügt."
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            return f"HTTP Fehler {e.code}: {body[:300]}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="discord_threads",
        description="Listet Threads in einem Discord-Channel auf oder erstellt einen neuen Thread.",
        parameters={
            "type": "object",
            "properties": {
                "channel_id": {"type": "string", "description": "Discord Channel-ID"},
                "action": {"type": "string", "description": "Aktion: list (default) oder create"},
                "thread_name": {"type": "string", "description": "Name des neuen Threads (nur bei action=create)"},
                "message_id": {"type": "string", "description": "Message-ID aus der ein Thread erstellt wird (optional)"},
                "auto_archive_minutes": {"type": "integer", "description": "Auto-Archivierung nach Minuten: 60, 1440, 4320, 10080 (default: 1440)"},
                "bot_token": {"type": "string", "description": "Discord Bot Token (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": ["channel_id"],
        },
    )
    def discord_threads(channel_id: str, action: str = "list", thread_name: str = "", message_id: str = "", auto_archive_minutes: int = 1440, bot_token: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        token = bot_token or cfg.get("bot_token", "")
        if not token:
            return "Fehler: bot_token benötigt"
        try:
            if action == "create":
                if not thread_name:
                    return "Fehler: thread_name benötigt für action=create"
                if message_id:
                    # Thread from message
                    result = _discord_api(token, f"/channels/{channel_id}/messages/{message_id}/threads", method="POST", body={
                        "name": thread_name,
                        "auto_archive_duration": auto_archive_minutes,
                    })
                else:
                    # Standalone thread (forum or news channel)
                    result = _discord_api(token, f"/channels/{channel_id}/threads", method="POST", body={
                        "name": thread_name,
                        "auto_archive_duration": auto_archive_minutes,
                        "type": 11,  # PUBLIC_THREAD
                    })
                if "id" in result:
                    return f"Thread erstellt: **{result.get('name', thread_name)}** (ID: {result['id']})"
                return f"Antwort: {json.dumps(result)[:300]}"
            else:
                # List active threads
                result = _discord_api(token, f"/channels/{channel_id}/threads/active" if False else f"/guilds/0/threads/active")
                # Fallback: use channel threads endpoint
                threads_data = _discord_api(token, f"/channels/{channel_id}/threads/active" if True else "")
                threads = threads_data if isinstance(threads_data, list) else threads_data.get("threads", [])
                # Filter by channel
                threads = [t for t in threads if str(t.get("parent_id", "")) == str(channel_id)]
                if not threads:
                    return f"Keine aktiven Threads in Channel {channel_id}"
                lines = [f"**Aktive Threads in Channel {channel_id} ({len(threads)}):**", ""]
                for t in threads[:20]:
                    name = t.get("name", "?")
                    tid = t.get("id", "?")
                    msg_count = t.get("message_count", 0)
                    archived = t.get("thread_metadata", {}).get("archived", False)
                    status = "archiviert" if archived else "aktiv"
                    lines.append(f"  **{name}** (ID: {tid}) — {msg_count} Nachrichten [{status}]")
                return "\n".join(lines)
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            return f"HTTP Fehler {e.code}: {body[:300]}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="discord_roles",
        description="Zeigt Server-Rollen an oder verwaltet Rollen eines Mitglieds (hinzufügen/entfernen).",
        parameters={
            "type": "object",
            "properties": {
                "guild_id": {"type": "string", "description": "Discord Server (Guild) ID"},
                "action": {"type": "string", "description": "Aktion: list (Rollen auflisten), add (Rolle hinzufügen), remove (Rolle entfernen)"},
                "user_id": {"type": "string", "description": "User-ID für add/remove Aktionen"},
                "role_id": {"type": "string", "description": "Rollen-ID für add/remove Aktionen"},
                "bot_token": {"type": "string", "description": "Discord Bot Token (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": ["guild_id"],
        },
    )
    def discord_roles(guild_id: str, action: str = "list", user_id: str = "", role_id: str = "", bot_token: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        token = bot_token or cfg.get("bot_token", "")
        if not token:
            return "Fehler: bot_token benötigt"
        try:
            if action == "add":
                if not user_id or not role_id:
                    return "Fehler: user_id und role_id benötigt"
                _discord_api(token, f"/guilds/{guild_id}/members/{user_id}/roles/{role_id}", method="PUT")
                return f"Rolle {role_id} zu User {user_id} hinzugefügt."
            elif action == "remove":
                if not user_id or not role_id:
                    return "Fehler: user_id und role_id benötigt"
                _discord_api(token, f"/guilds/{guild_id}/members/{user_id}/roles/{role_id}", method="DELETE")
                return f"Rolle {role_id} von User {user_id} entfernt."
            else:
                # List roles
                roles = _discord_api(token, f"/guilds/{guild_id}/roles")
                if not isinstance(roles, list):
                    return f"Fehler beim Abrufen der Rollen: {roles}"
                # Sort by position descending
                roles.sort(key=lambda r: r.get("position", 0), reverse=True)
                lines = [f"**Discord Server Rollen ({len(roles)}):**", ""]
                for role in roles:
                    name = role.get("name", "?")
                    rid = role.get("id", "?")
                    color = role.get("color", 0)
                    managed = " [Bot]" if role.get("managed") else ""
                    mentionable = " [@]" if role.get("mentionable") else ""
                    hoisted = " [angezeigt]" if role.get("hoist") else ""
                    color_str = f" #{color:06X}" if color else ""
                    lines.append(f"  {name:30} ID: {rid}{color_str}{managed}{mentionable}{hoisted}")
                # If user_id given, show their roles too
                if user_id:
                    member = _discord_api(token, f"/guilds/{guild_id}/members/{user_id}")
                    member_role_ids = set(str(r) for r in member.get("roles", []))
                    member_roles = [r["name"] for r in roles if str(r["id"]) in member_role_ids]
                    lines.append("")
                    lines.append(f"**Rollen von User {user_id}:**")
                    lines.append(", ".join(member_roles) if member_roles else "Keine Rollen")
                return "\n".join(lines)
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            return f"HTTP Fehler {e.code}: {body[:300]}"
        except Exception as e:
            return f"Fehler: {e}"
