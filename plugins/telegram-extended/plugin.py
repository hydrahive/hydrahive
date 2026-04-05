"""
telegram-extended Plugin — Telegram Bot API (erweiterte Tools).

Tools:
  - telegram_send:    Nachricht senden (mit Markdown-Support)
  - telegram_forward: Nachricht weiterleiten
  - telegram_pin:     Nachricht anpinnen oder loslösen
  - telegram_members: Gruppenmitglieder anzeigen
  - telegram_poll:    Umfrage erstellen
"""
import json
import urllib.request
import urllib.error
import urllib.parse


def _load_config(username: str, plugin_id: str = "telegram-extended") -> dict:
    path = f"/etc/hydrahive/user_app_config/{username}/{plugin_id}.json"
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def _bot_api(token: str, method: str, payload: dict = None) -> dict:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = json.dumps(payload).encode() if payload else None
    req = urllib.request.Request(url, data=data)
    req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def register(api):

    @api.tool(
        tool_id="telegram_send",
        description="Sendet eine Nachricht über den Telegram Bot. Unterstützt Markdown und HTML-Formatierung.",
        parameters={
            "type": "object",
            "properties": {
                "chat_id": {"type": "string", "description": "Chat-ID oder @username des Empfängers"},
                "text": {"type": "string", "description": "Nachrichtentext"},
                "parse_mode": {"type": "string", "description": "Formatierung: Markdown, MarkdownV2, HTML oder leer (default: Markdown)"},
                "disable_preview": {"type": "boolean", "description": "Link-Previews deaktivieren (default: false)"},
                "silent": {"type": "boolean", "description": "Stille Benachrichtigung ohne Sound (default: false)"},
                "reply_to": {"type": "integer", "description": "Message-ID auf die geantwortet wird (optional)"},
                "bot_token": {"type": "string", "description": "Bot Token (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": ["chat_id", "text"],
        },
    )
    def telegram_send(chat_id: str, text: str, parse_mode: str = "Markdown", disable_preview: bool = False, silent: bool = False, reply_to: int = 0, bot_token: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        token = bot_token or cfg.get("bot_token", "")
        if not token:
            return "Fehler: bot_token benötigt"
        payload = {
            "chat_id": chat_id,
            "text": text,
            "disable_web_page_preview": disable_preview,
            "disable_notification": silent,
        }
        if parse_mode:
            payload["parse_mode"] = parse_mode
        if reply_to:
            payload["reply_to_message_id"] = reply_to
        try:
            result = _bot_api(token, "sendMessage", payload)
            if result.get("ok"):
                msg = result.get("result", {})
                msg_id = msg.get("message_id", "?")
                return f"Nachricht gesendet. Message-ID: {msg_id}"
            return f"Fehler: {result.get('description', 'Unbekannt')}"
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            return f"HTTP Fehler {e.code}: {body[:200]}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="telegram_forward",
        description="Leitet eine Telegram-Nachricht von einem Chat in einen anderen weiter.",
        parameters={
            "type": "object",
            "properties": {
                "to_chat_id": {"type": "string", "description": "Ziel-Chat-ID"},
                "from_chat_id": {"type": "string", "description": "Quell-Chat-ID"},
                "message_id": {"type": "integer", "description": "Message-ID der weiterzuleitenden Nachricht"},
                "silent": {"type": "boolean", "description": "Stille Weiterleitung ohne Sound (default: false)"},
                "bot_token": {"type": "string", "description": "Bot Token (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": ["to_chat_id", "from_chat_id", "message_id"],
        },
    )
    def telegram_forward(to_chat_id: str, from_chat_id: str, message_id: int, silent: bool = False, bot_token: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        token = bot_token or cfg.get("bot_token", "")
        if not token:
            return "Fehler: bot_token benötigt"
        try:
            result = _bot_api(token, "forwardMessage", {
                "chat_id": to_chat_id,
                "from_chat_id": from_chat_id,
                "message_id": message_id,
                "disable_notification": silent,
            })
            if result.get("ok"):
                new_id = result.get("result", {}).get("message_id", "?")
                return f"Nachricht weitergeleitet. Neue Message-ID: {new_id}"
            return f"Fehler: {result.get('description', 'Unbekannt')}"
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            return f"HTTP Fehler {e.code}: {body[:200]}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="telegram_pin",
        description="Pinnt eine Nachricht in einem Telegram-Gruppen-Chat an oder löst sie.",
        parameters={
            "type": "object",
            "properties": {
                "chat_id": {"type": "string", "description": "Chat-ID der Gruppe"},
                "message_id": {"type": "integer", "description": "Message-ID der anzupinnenden Nachricht"},
                "unpin": {"type": "boolean", "description": "true = loslösen, false = anpinnen (default: false)"},
                "unpin_all": {"type": "boolean", "description": "Alle gepinnten Nachrichten loslösen (default: false)"},
                "silent": {"type": "boolean", "description": "Kein Benachrichtigungs-Alert (default: true)"},
                "bot_token": {"type": "string", "description": "Bot Token (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": ["chat_id"],
        },
    )
    def telegram_pin(chat_id: str, message_id: int = 0, unpin: bool = False, unpin_all: bool = False, silent: bool = True, bot_token: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        token = bot_token or cfg.get("bot_token", "")
        if not token:
            return "Fehler: bot_token benötigt"
        try:
            if unpin_all:
                result = _bot_api(token, "unpinAllChatMessages", {"chat_id": chat_id})
                action = "Alle Nachrichten losgelöst"
            elif unpin:
                if not message_id:
                    return "Fehler: message_id benötigt zum Loslösen"
                result = _bot_api(token, "unpinChatMessage", {"chat_id": chat_id, "message_id": message_id})
                action = f"Nachricht {message_id} losgelöst"
            else:
                if not message_id:
                    return "Fehler: message_id benötigt zum Anpinnen"
                result = _bot_api(token, "pinChatMessage", {
                    "chat_id": chat_id,
                    "message_id": message_id,
                    "disable_notification": silent,
                })
                action = f"Nachricht {message_id} angepinnt"
            if result.get("ok") or result.get("result") is True:
                return f"{action} in Chat {chat_id}."
            return f"Fehler: {result.get('description', 'Unbekannt')}"
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            return f"HTTP Fehler {e.code}: {body[:200]}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="telegram_members",
        description="Zeigt Mitglieder einer Telegram-Gruppe oder Channel-Administratoren.",
        parameters={
            "type": "object",
            "properties": {
                "chat_id": {"type": "string", "description": "Gruppen- oder Channel-ID"},
                "admins_only": {"type": "boolean", "description": "Nur Administratoren anzeigen (default: false)"},
                "bot_token": {"type": "string", "description": "Bot Token (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": ["chat_id"],
        },
    )
    def telegram_members(chat_id: str, admins_only: bool = False, bot_token: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        token = bot_token or cfg.get("bot_token", "")
        if not token:
            return "Fehler: bot_token benötigt"
        try:
            # Get chat info
            chat_info = _bot_api(token, "getChat", {"chat_id": chat_id})
            chat = chat_info.get("result", {})
            chat_title = chat.get("title", chat_id)
            member_count_resp = _bot_api(token, "getChatMemberCount", {"chat_id": chat_id})
            member_count = member_count_resp.get("result", 0)
            # Get admins
            admins_resp = _bot_api(token, "getChatAdministrators", {"chat_id": chat_id})
            admins = admins_resp.get("result", [])
            lines = [f"**Telegram Gruppe: {chat_title}**", f"Mitglieder gesamt: {member_count}", ""]
            lines.append(f"**Administratoren ({len(admins)}):**")
            for admin in admins:
                user = admin.get("user", {})
                name = user.get("first_name", "?")
                if user.get("last_name"):
                    name += " " + user["last_name"]
                uname = f" @{user['username']}" if user.get("username") else ""
                status = admin.get("status", "")
                custom_title = admin.get("custom_title", "")
                title_str = f' "{custom_title}"' if custom_title else ""
                is_bot = " [Bot]" if user.get("is_bot") else ""
                lines.append(f"  {name}{uname}{title_str} [{status}]{is_bot}")
            return "\n".join(lines)
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            return f"HTTP Fehler {e.code}: {body[:200]}"
        except Exception as e:
            return f"Fehler: {e}"

    @api.tool(
        tool_id="telegram_poll",
        description="Erstellt eine Umfrage (Poll) in einem Telegram-Chat.",
        parameters={
            "type": "object",
            "properties": {
                "chat_id": {"type": "string", "description": "Chat-ID für die Umfrage"},
                "question": {"type": "string", "description": "Frage der Umfrage"},
                "options": {"type": "array", "items": {"type": "string"}, "description": "Antwortoptionen (2-10 Stück)"},
                "anonymous": {"type": "boolean", "description": "Anonyme Abstimmung (default: true)"},
                "multiple_answers": {"type": "boolean", "description": "Mehrfachantworten erlauben (default: false)"},
                "correct_option": {"type": "integer", "description": "Index der richtigen Antwort für Quiz-Modus (0-basiert, optional)"},
                "open_period": {"type": "integer", "description": "Umfrage-Dauer in Sekunden (5-600, optional)"},
                "bot_token": {"type": "string", "description": "Bot Token (optional, sonst aus Config)"},
                "username": {"type": "string", "description": "HydraHive-Username für Config-Lookup (optional)"},
            },
            "required": ["chat_id", "question", "options"],
        },
    )
    def telegram_poll(chat_id: str, question: str, options: list, anonymous: bool = True, multiple_answers: bool = False, correct_option: int = -1, open_period: int = 0, bot_token: str = "", username: str = "", **_) -> str:
        cfg = _load_config(username) if username else {}
        token = bot_token or cfg.get("bot_token", "")
        if not token:
            return "Fehler: bot_token benötigt"
        if len(options) < 2 or len(options) > 10:
            return "Fehler: 2 bis 10 Antwortoptionen erforderlich"
        try:
            payload = {
                "chat_id": chat_id,
                "question": question,
                "options": options,
                "is_anonymous": anonymous,
                "allows_multiple_answers": multiple_answers,
            }
            if correct_option >= 0:
                payload["type"] = "quiz"
                payload["correct_option_id"] = correct_option
            else:
                payload["type"] = "regular"
            if open_period:
                payload["open_period"] = max(5, min(open_period, 600))
            result = _bot_api(token, "sendPoll", payload)
            if result.get("ok"):
                poll = result.get("result", {}).get("poll", {})
                poll_id = poll.get("id", "?")
                return f"Umfrage erstellt in Chat {chat_id}.\nFrage: {question}\nOptionen: {', '.join(options)}\nPoll-ID: {poll_id}"
            return f"Fehler: {result.get('description', 'Unbekannt')}"
        except urllib.error.HTTPError as e:
            body = e.read().decode()
            return f"HTTP Fehler {e.code}: {body[:200]}"
        except Exception as e:
            return f"Fehler: {e}"
