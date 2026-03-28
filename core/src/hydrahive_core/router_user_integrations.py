from __future__ import annotations

import logging
import re
import subprocess as _sp
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from pydantic import BaseModel, validator


logger = logging.getLogger(__name__)

# Agent-IDs dürfen nur sicher für Dateipfade verwendbare Zeichen enthalten
_AGENT_ID_RE = re.compile(r"^[a-z0-9_-]+$")


class _AgentIdError(ValueError):
    """Ungültige Agent-ID — wird in Route-Handlern zu HTTPException(400)."""


def _sanitize_agent_id(agent_id: str) -> str:
    """Wirft _AgentIdError wenn agent_id Path-Traversal oder ungültige Zeichen enthält."""
    if not agent_id or not _AGENT_ID_RE.fullmatch(agent_id):
        raise _AgentIdError(f"Ungültige Agent-ID: '{agent_id}'")
    return agent_id


def _username_from_auth(auth: tuple) -> str:
    username, _ = auth
    return _sanitize_agent_id(username)


class WksConfigRequest(BaseModel):
    ip: str
    ssh_user: str = ""
    ollama_port: int = 11434
    ssh_key: str = ""


class MailConfigRequest(BaseModel):
    mail_address: str = ""   # leer = auto-generieren aus username@domain
    domain: str = ""         # leer = erste Domain aus KAS
    create_account: bool = False  # True = Account via KAS anlegen
    # Manuelle SMTP-Konfiguration (wenn create_account=False und kein KAS)
    smtp_host: str = ""
    smtp_port: int = 587
    smtp_user: str = ""
    smtp_password: str = ""
    imap_host: str = ""


def _kas_config() -> dict:
    """Liest /etc/hydrahive/kas.json falls vorhanden."""
    import json
    p = Path("/etc/hydrahive/kas.json")
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return {}


def _mail_config_path(username: str) -> Path:
    from .main import AGENTS_DIR
    return Path(AGENTS_DIR) / f"personal_{username}" / "mail.json"


def load_mail_config(username: str) -> dict | None:
    import json
    p = _mail_config_path(username)
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            pass
    return None


def save_mail_config(username: str, cfg: dict) -> None:
    import json
    p = _mail_config_path(username)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(cfg, indent=2))
    p.chmod(0o600)


async def kas_create_mailaccount(local_part: str, domain_part: str, password: str, kas_cfg: dict) -> dict:
    """Legt ein Postfach via All-Inkl KAS SOAP API an."""
    import httpx
    login    = kas_cfg.get("login", "")
    auth_data = kas_cfg.get("password", "")
    if not login:
        return {"ok": False, "error": "KAS nicht konfiguriert (/etc/hydrahive/kas.json)"}
    soap = f"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns1="urn:xmethodsKasApi">
<SOAP-ENV:Body>
  <ns1:KasApi>
    <Params><![CDATA[{{"kas_login":"{login}","kas_auth_data":"{auth_data}","kas_auth_type":"plain","kas_action":"add_mailaccount","KasRequestParams":{{"local_part":"{local_part}","domain_part":"{domain_part}","mail_password":"{password}"}}}}]]></Params>
  </ns1:KasApi>
</SOAP-ENV:Body>
</SOAP-ENV:Envelope>"""
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post(
                "https://kasapi.kasserver.com/soap/KasApi.php",
                content=soap,
                headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": '"urn:xmethodsKasApi#KasApi"'},
            )
        if "kas_error" in r.text.lower():
            import re
            err = re.search(r'<string>(kas_\w+)</string>', r.text)
            return {"ok": False, "error": err.group(1) if err else "KAS Fehler"}
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class DiscordConfigRequest(BaseModel):
    bot_token: str = ""           # leer = bestehenden Token behalten
    guild_id: str = ""
    channel_ids: list[str] = []
    ignore_bots: bool = True      # Nachrichten von anderen Bots ignorieren
    require_mention: bool = False  # Nur bei @Mention antworten
    loop_detection: bool = True   # Loop-Detektion aktiv
    loop_bot_threshold: int = 3   # Aufeinanderfolgende Bot-Nachrichten bis Circuit Breaker
    loop_pingpong_seconds: int = 30  # Zeitfenster für PingPong-Erkennung (s)
    loop_cooldown_seconds: int = 300  # Circuit-Breaker-Cooldown (s)
    user_whitelist:  list[str] = []
    user_blacklist:  list[str] = []
    role_whitelist:  list[str] = []
    role_blacklist:  list[str] = []
    channel_modes:   dict[str, str] = {}   # channel_id → "rw"|"ro"
    channel_names:   dict[str, str] = {}   # channel_id → name (cache)

    @validator("guild_id")
    def _v_guild_id(cls, v: str) -> str:
        v = v.strip()
        if v and (not v.isdigit() or len(v) > 20):
            raise ValueError("Ungültige Guild-ID — muss numerische Snowflake sein")
        return v

    @validator("channel_ids", each_item=True)
    def _v_channel_ids(cls, v: str) -> str:
        v = v.strip()
        if v and (not v.isdigit() or len(v) > 20):
            raise ValueError(f"Ungültige Channel-ID '{v}' — muss numerische Snowflake sein")
        return v

    @validator("user_whitelist", "user_blacklist", "role_whitelist", "role_blacklist", each_item=True)
    def _v_snowflake_ids(cls, v: str) -> str:
        v = v.strip()
        if v and (not v.isdigit() or len(v) > 20):
            raise ValueError(f"Ungültige Discord-ID '{v}' — muss numerische Snowflake sein")
        return v


SUPPORTED_PLATFORMS = {
    "matrix":   {"supported": True,  "label": "Matrix"},
    "discord":  {"supported": True,  "label": "Discord"},
    "wks":      {"supported": True,  "label": "WKS"},
    "whatsapp": {"supported": True,  "label": "WhatsApp"},
    "telegram": {"supported": False, "label": "Telegram"},
    "signal":   {"supported": False, "label": "Signal"},
}


def discord_client_connected(username: str) -> bool:
    from .tool_registry import _discord_clients

    client = _discord_clients.get(username)
    if not client:
        return False

    connected = getattr(client, "is_connected", False)
    if callable(connected):
        try:
            return bool(connected())
        except Exception as e:
            logger.debug("Discord is_connected() für %s fehlgeschlagen: %s", username, e)
            return False
    return bool(connected)


def _wks_connected(username: str, wks: dict, wks_keys_dir: Path) -> bool:
    ip = str(wks.get("ip", "")).strip()
    if not ip:
        return False

    key_file = wks_keys_dir / username
    if not key_file.exists():
        return False

    ssh_user = str(wks.get("ssh_user", username)).strip() or username
    try:
        result = _sp.run(
            [
                "ssh",
                "-i",
                str(key_file),
                "-o",
                "BatchMode=yes",
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "ConnectTimeout=3",
                f"{ssh_user}@{ip}",
                "true",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return result.returncode == 0
    except FileNotFoundError:
        logger.debug("ssh nicht gefunden für WKS-Connectivity-Check (%s)", username)
        return False
    except Exception as e:
        logger.debug("WKS-Connectivity-Check für %s fehlgeschlagen: %s", username, e)
        return False


def _build_platform_overview(username: str, users: dict, wks_keys_dir: Path) -> list[dict]:
    from .discord_agent import load_discord_config
    from .whatsapp_agent import load_whatsapp_config

    user = users.get(username, {})
    wks = user.get("wks", {})
    discord_cfg  = load_discord_config(username)
    whatsapp_cfg = load_whatsapp_config(username)
    matrix_id = user.get("matrix_id", "")

    overview = [
        {
            "platform": "matrix",
            "label": SUPPORTED_PLATFORMS["matrix"]["label"],
            "supported": True,
            "configured": bool(matrix_id),
            "connected": bool(matrix_id),
            "details": {"matrix_id": matrix_id} if matrix_id else {},
        },
        {
            "platform": "discord",
            "label": SUPPORTED_PLATFORMS["discord"]["label"],
            "supported": True,
            "configured": bool(discord_cfg),
            "connected": discord_client_connected(username),
            "details": {
                "guild_id": discord_cfg.get("guild_id", "") if discord_cfg else "",
                "channel_ids": discord_cfg.get("channel_ids", []) if discord_cfg else [],
            },
        },
        {
            "platform": "wks",
            "label": SUPPORTED_PLATFORMS["wks"]["label"],
            "supported": True,
            "configured": bool(wks.get("ip")),
            "connected": _wks_connected(username, wks, wks_keys_dir),
            "details": {
                "ip": wks.get("ip", ""),
                "ssh_user": wks.get("ssh_user", username),
                "ollama_port": wks.get("ollama_port", 11434),
                "has_ssh_key": (wks_keys_dir / username).exists(),
            },
        },
        {
            "platform": "whatsapp",
            "label": SUPPORTED_PLATFORMS["whatsapp"]["label"],
            "supported": True,
            "configured": bool(whatsapp_cfg and whatsapp_cfg.get("enabled")),
            "connected": whatsapp_cfg.get("status") == "connected" if whatsapp_cfg else False,
            "details": {"phone": whatsapp_cfg.get("phone", "") if whatsapp_cfg else ""},
        },
        {
            "platform": "telegram",
            "label": SUPPORTED_PLATFORMS["telegram"]["label"],
            "supported": False,
            "configured": False,
            "connected": False,
            "details": {"status": "planned"},
        },
        {
            "platform": "signal",
            "label": SUPPORTED_PLATFORMS["signal"]["label"],
            "supported": False,
            "configured": False,
            "connected": False,
            "details": {"status": "planned"},
        },
    ]
    return overview


async def setup_discord_clients(*, load_users, runtime, orchestrator, logger) -> None:
    """Discord-Bots fuer alle konfigurierten User beim Start laden."""
    from .discord_agent import AgentDiscordClient, load_discord_config
    from .tool_registry import _discord_clients

    users = load_users()
    for username in users:
        cfg = load_discord_config(username)
        if not cfg:
            continue
        personal_agent_id = f"personal_{username}"
        client = AgentDiscordClient(
            agent_id=personal_agent_id,
            bot_token=cfg["bot_token"],
            guild_id=cfg.get("guild_id", ""),
            channel_ids=cfg.get("channel_ids", []),
            ignore_bots=cfg.get("ignore_bots", True),
            require_mention=cfg.get("require_mention", False),
            loop_detection=cfg.get("loop_detection", True),
            loop_bot_threshold=cfg.get("loop_bot_threshold", 3),
            loop_pingpong_seconds=cfg.get("loop_pingpong_seconds", 30),
            loop_cooldown_seconds=cfg.get("loop_cooldown_seconds", 300),
            user_whitelist=cfg.get("user_whitelist", []),
            user_blacklist=cfg.get("user_blacklist", []),
            role_whitelist=cfg.get("role_whitelist", []),
            role_blacklist=cfg.get("role_blacklist", []),
            channel_modes=cfg.get("channel_modes", {}),
            orchestrator=orchestrator,
        )
        _discord_clients[personal_agent_id] = client
        await runtime.attach_discord_client(username, client)
        logger.info("Discord-Bot fuer User '%s' (Agent: %s) gestartet", username, personal_agent_id)


def register_user_integration_routes(
    auth_router: APIRouter,
    *,
    require_auth,
    load_users,
    save_users,
    wks_keys_dir: Path,
    runtime,
    orchestrator,
    audit_log,
    logger,
    internal_router: APIRouter | None = None,
) -> None:
    @auth_router.get("/me/wks")
    def get_my_wks(auth: tuple = Depends(require_auth)):
        username = _username_from_auth(auth)
        users = load_users()
        wks = users.get(username, {}).get("wks", {})
        return {
            "configured": bool(wks.get("ip")),
            "ip": wks.get("ip", ""),
            "ssh_user": wks.get("ssh_user", username),
            "ollama_port": wks.get("ollama_port", 11434),
            "has_ssh_key": (wks_keys_dir / username).exists(),
        }

    @auth_router.get("/me/platforms")
    def get_my_platforms(auth: tuple = Depends(require_auth)):
        username = _username_from_auth(auth)
        users = load_users()
        return {
            "username": username,
            "platforms": _build_platform_overview(username, users, wks_keys_dir),
        }

    @auth_router.put("/me/wks")
    async def update_my_wks(req: WksConfigRequest, auth: tuple = Depends(require_auth)):
        username = _username_from_auth(auth)
        users = load_users()
        if username not in users:
            raise HTTPException(404, "User nicht gefunden")

        if req.ssh_key.strip():
            import stat as _stat

            wks_keys_dir.mkdir(parents=True, exist_ok=True)
            key_file = wks_keys_dir / username
            key_file.write_text(req.ssh_key.strip() + "\n", encoding="utf-8")
            key_file.chmod(_stat.S_IRUSR | _stat.S_IWUSR)

        users[username]["wks"] = {
            "ip": req.ip.strip(),
            "ssh_user": req.ssh_user.strip() or username,
            "ollama_port": req.ollama_port,
            "ssh_key_path": str(wks_keys_dir / username),
        }
        save_users(users)
        logger.info("WKS konfiguriert: %s -> %s@%s", username, req.ssh_user or username, req.ip)
        return {"updated": True}

    @auth_router.get("/me/wks/pubkey")
    def get_wks_pubkey(auth: tuple = Depends(require_auth)):
        import subprocess as _sp

        username = _username_from_auth(auth)
        key_file = wks_keys_dir / username
        if not key_file.exists():
            raise HTTPException(404, "Kein SSH-Key vorhanden - bitte erst generieren oder einfuegen")
        try:
            result = _sp.run(
                ["ssh-keygen", "-y", "-f", str(key_file)],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                raise HTTPException(500, f"ssh-keygen Fehler: {result.stderr}")
            return {"public_key": result.stdout.strip()}
        except FileNotFoundError:
            raise HTTPException(500, "ssh-keygen nicht gefunden")

    @auth_router.post("/me/wks/generate-key")
    def generate_wks_key(auth: tuple = Depends(require_auth)):
        import os as _os
        import shutil as _shutil
        import subprocess as _sp
        import tempfile

        username = _username_from_auth(auth)
        wks_keys_dir.mkdir(parents=True, exist_ok=True)
        key_file = wks_keys_dir / username
        with tempfile.NamedTemporaryFile(delete=False, suffix="_wks") as tf:
            tmp_path = tf.name
        try:
            _sp.run(
                ["ssh-keygen", "-t", "ed25519", "-f", tmp_path, "-N", "", "-C", f"hydrahive-wks@{username}"],
                capture_output=True,
                check=True,
                timeout=10,
            )
            _shutil.move(tmp_path, str(key_file))
            key_file.chmod(0o600)
            _os.remove(tmp_path + ".pub")
            result = _sp.run(["ssh-keygen", "-y", "-f", str(key_file)], capture_output=True, text=True, timeout=5)
            pub_key = result.stdout.strip()
            logger.info("WKS SSH-Key generiert fuer %s", username)
            audit_log("wks.key_generated", details={"user": username})
            return {"generated": True, "public_key": pub_key}
        except Exception as e:
            try:
                _os.unlink(tmp_path)
            except Exception:
                pass
            raise HTTPException(500, f"Key-Generierung fehlgeschlagen: {e}")

    @auth_router.post("/me/wks/test-ssh")
    async def test_wks_ssh(auth: tuple = Depends(require_auth)):
        import asyncio as _asyncio

        username = _username_from_auth(auth)
        users = load_users()
        wks = users.get(username, {}).get("wks", {})
        ip = wks.get("ip", "")
        ssh_user = wks.get("ssh_user", username)
        key_file = wks_keys_dir / username

        if not ip:
            raise HTTPException(400, "WKS nicht konfiguriert")
        if not key_file.exists():
            raise HTTPException(400, "Kein SSH-Key vorhanden")

        try:
            proc = await _asyncio.create_subprocess_exec(
                "ssh",
                "-i",
                str(key_file),
                "-o",
                "StrictHostKeyChecking=no",
                "-o",
                "ConnectTimeout=5",
                "-o",
                "BatchMode=yes",
                f"{ssh_user}@{ip}",
                "hostname && whoami",
                stdout=_asyncio.subprocess.PIPE,
                stderr=_asyncio.subprocess.PIPE,
            )
            stdout, stderr = await _asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode == 0:
                output = stdout.decode().strip()
                lines = output.splitlines()
                return {"ok": True, "hostname": lines[0] if lines else "", "user": lines[1] if len(lines) > 1 else ""}
            return {"ok": False, "error": stderr.decode().strip()[:300]}
        except _asyncio.TimeoutError:
            return {"ok": False, "error": "Timeout - Host nicht erreichbar"}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    @auth_router.get("/me/wks/ollama-models")
    async def get_wks_ollama_models(auth: tuple = Depends(require_auth)):
        import httpx as _httpx

        username = _username_from_auth(auth)
        users = load_users()
        wks = users.get(username, {}).get("wks", {})
        if not wks.get("ip"):
            return {"models": [], "wks_url": None}

        wks_url = f"http://{wks['ip']}:{wks.get('ollama_port', 11434)}"
        try:
            async with _httpx.AsyncClient(timeout=3) as client:
                resp = await client.get(f"{wks_url}/api/tags")
                if resp.status_code == 200:
                    tags = resp.json().get("models", [])
                    models = [
                        {"id": f"ollama/{t['name']}", "label": f"WKS: {t['name']}", "provider": "wks_ollama"}
                        for t in tags
                        if t.get("name")
                    ]
                    return {"models": models, "wks_url": wks_url}
        except Exception as e:
            return {"models": [], "wks_url": wks_url, "error": str(e)}
        return {"models": [], "wks_url": wks_url}

    @auth_router.get("/me/discord")
    def get_my_discord(auth: tuple = Depends(require_auth)):
        username = _username_from_auth(auth)
        from .discord_agent import load_discord_config

        cfg = load_discord_config(username)
        if not cfg:
            return {"configured": False}
        return {
            "configured": True,
            "guild_id": cfg.get("guild_id", ""),
            "channel_ids": cfg.get("channel_ids", []),
            "ignore_bots": cfg.get("ignore_bots", True),
            "require_mention": cfg.get("require_mention", False),
            "loop_detection": cfg.get("loop_detection", True),
            "loop_bot_threshold": cfg.get("loop_bot_threshold", 6),
            "loop_pingpong_seconds": cfg.get("loop_pingpong_seconds", 30),
            "loop_cooldown_seconds": cfg.get("loop_cooldown_seconds", 300),
            "connected": discord_client_connected(username),
            "user_whitelist": cfg.get("user_whitelist", []),
            "user_blacklist": cfg.get("user_blacklist", []),
            "role_whitelist": cfg.get("role_whitelist", []),
            "role_blacklist": cfg.get("role_blacklist", []),
            "channel_modes": cfg.get("channel_modes", {}),
            "channel_names": cfg.get("channel_names", {}),
        }

    @auth_router.get("/me/discord/channels")
    async def get_my_discord_channels(auth: tuple = Depends(require_auth)):
        """Channels der konfigurierten Guild per Discord REST API abrufen."""
        import httpx
        username = _username_from_auth(auth)
        from .discord_agent import load_discord_config

        cfg = load_discord_config(username)
        if not cfg:
            raise HTTPException(400, "Discord nicht konfiguriert")
        token = cfg.get("bot_token", "")
        guild_id = cfg.get("guild_id", "")
        if not guild_id:
            raise HTTPException(400, "Keine Guild-ID konfiguriert")
        try:
            async with httpx.AsyncClient(timeout=8) as client:
                r = await client.get(
                    f"https://discord.com/api/v10/guilds/{guild_id}/channels",
                    headers={"Authorization": f"Bot {token}"},
                )
            if r.status_code != 200:
                raise HTTPException(400, f"Discord API Fehler: {r.status_code}")
            channels = [
                {"id": ch["id"], "name": ch["name"]}
                for ch in r.json()
                if ch.get("type") == 0   # 0 = GUILD_TEXT
            ]
            channels.sort(key=lambda c: c["name"])
            return {"channels": channels}
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(502, f"Discord-Fehler: {e}")

    @auth_router.get("/me/discord/roles")
    async def get_my_discord_roles(auth: tuple = Depends(require_auth)):
        """Rollen der konfigurierten Guild auflisten."""
        username = _username_from_auth(auth)
        from .discord_agent import load_discord_config
        from .tool_registry import _discord_clients

        cfg = load_discord_config(username)
        if not cfg:
            raise HTTPException(400, "Discord nicht konfiguriert")
        personal_agent_id = f"personal_{username}"
        client = _discord_clients.get(personal_agent_id)
        if not client:
            raise HTTPException(400, "Discord-Bot nicht verbunden")
        try:
            roles = await client.list_roles()
            return {"roles": roles}
        except Exception as e:
            raise HTTPException(500, str(e))

    @auth_router.put("/me/discord", status_code=200)
    async def update_my_discord(req: DiscordConfigRequest, auth: tuple = Depends(require_auth)):
        username = _username_from_auth(auth)
        from .discord_agent import AgentDiscordClient, delete_discord_config, load_discord_config, save_discord_config
        from .tool_registry import _discord_clients

        # Token: neu wenn angegeben, sonst bestehenden behalten
        existing = load_discord_config(username) or {}
        token = req.bot_token.strip() or existing.get("bot_token", "")
        if not token:
            raise HTTPException(400, "Kein Bot-Token angegeben und kein bestehender Token vorhanden")

        cfg = {
            "bot_token": token,
            "guild_id": req.guild_id.strip(),
            "channel_ids": [c.strip() for c in req.channel_ids if c.strip()],
            "ignore_bots": req.ignore_bots,
            "require_mention": req.require_mention,
            "loop_detection": req.loop_detection,
            "loop_bot_threshold": max(2, req.loop_bot_threshold),
            "loop_pingpong_seconds": max(5, req.loop_pingpong_seconds),
            "loop_cooldown_seconds": max(10, req.loop_cooldown_seconds),
            "user_whitelist":  req.user_whitelist,
            "user_blacklist":  req.user_blacklist,
            "role_whitelist":  req.role_whitelist,
            "role_blacklist":  req.role_blacklist,
            "channel_modes":   req.channel_modes,
            "channel_names":   req.channel_names,
        }
        save_discord_config(username, cfg)
        personal_agent_id = f"personal_{username}"

        test_client = AgentDiscordClient(
            agent_id=personal_agent_id,
            bot_token=cfg["bot_token"],
            guild_id=cfg["guild_id"],
            channel_ids=cfg["channel_ids"],
            ignore_bots=cfg.get("ignore_bots", True),
            require_mention=cfg.get("require_mention", False),
            loop_detection=cfg.get("loop_detection", True),
            loop_bot_threshold=cfg.get("loop_bot_threshold", 6),
            loop_pingpong_seconds=cfg.get("loop_pingpong_seconds", 30),
            loop_cooldown_seconds=cfg.get("loop_cooldown_seconds", 300),
            user_whitelist=cfg.get("user_whitelist", []),
            user_blacklist=cfg.get("user_blacklist", []),
            role_whitelist=cfg.get("role_whitelist", []),
            role_blacklist=cfg.get("role_blacklist", []),
            channel_modes=cfg.get("channel_modes", {}),
            orchestrator=orchestrator,
        )
        test_result = await test_client.test_connection()
        if not test_result.get("ok"):
            delete_discord_config(username)
            raise HTTPException(400, f"Discord-Token ungueltig: {test_result.get('error', '')}")

        await runtime.detach_discord_client(username)
        client = AgentDiscordClient(
            agent_id=personal_agent_id,
            bot_token=cfg["bot_token"],
            guild_id=cfg["guild_id"],
            channel_ids=cfg["channel_ids"],
            ignore_bots=cfg.get("ignore_bots", True),
            require_mention=cfg.get("require_mention", False),
            loop_detection=cfg.get("loop_detection", True),
            loop_bot_threshold=cfg.get("loop_bot_threshold", 6),
            loop_pingpong_seconds=cfg.get("loop_pingpong_seconds", 30),
            loop_cooldown_seconds=cfg.get("loop_cooldown_seconds", 300),
            user_whitelist=cfg.get("user_whitelist", []),
            user_blacklist=cfg.get("user_blacklist", []),
            role_whitelist=cfg.get("role_whitelist", []),
            role_blacklist=cfg.get("role_blacklist", []),
            channel_modes=cfg.get("channel_modes", {}),
            orchestrator=orchestrator,
        )
        _discord_clients[personal_agent_id] = client
        await runtime.attach_discord_client(username, client)

        audit_log("discord.configured", details={"user": username, "bot": test_result.get("bot_name", "")})
        logger.info("Discord konfiguriert fuer %s: Bot '%s'", username, test_result.get("bot_name", ""))
        return {"updated": True, "bot_name": test_result.get("bot_name", ""), "bot_id": test_result.get("bot_id", "")}

    @auth_router.delete("/me/discord", status_code=200)
    async def delete_my_discord(auth: tuple = Depends(require_auth)):
        username = _username_from_auth(auth)
        from .discord_agent import delete_discord_config
        from .tool_registry import _discord_clients

        await runtime.detach_discord_client(username)
        _discord_clients.pop(username, None)
        delete_discord_config(username)
        audit_log("discord.removed", details={"user": username})
        return {"deleted": True}

    @auth_router.post("/me/discord/test")
    async def test_my_discord(auth: tuple = Depends(require_auth)):
        username = _username_from_auth(auth)
        from .discord_agent import AgentDiscordClient, load_discord_config

        cfg = load_discord_config(username)
        if not cfg:
            raise HTTPException(400, "Discord nicht konfiguriert")
        test_client = AgentDiscordClient(
            agent_id=f"personal_{username}",
            bot_token=cfg["bot_token"],
            guild_id=cfg["guild_id"],
            channel_ids=cfg.get("channel_ids", []),
            user_whitelist=cfg.get("user_whitelist", []),
            user_blacklist=cfg.get("user_blacklist", []),
            role_whitelist=cfg.get("role_whitelist", []),
            role_blacklist=cfg.get("role_blacklist", []),
            channel_modes=cfg.get("channel_modes", {}),
            orchestrator=orchestrator,
        )
        return await test_client.test_connection()

    # ── WhatsApp ─────────────────────────────────────────────────────────────

    @auth_router.get("/me/whatsapp")
    async def get_my_whatsapp(auth: tuple = Depends(require_auth)):
        username = _username_from_auth(auth)
        from .whatsapp_agent import bridge_get_status, load_whatsapp_config
        cfg = load_whatsapp_config(username)
        if not cfg or not cfg.get("enabled"):
            return {"configured": False, "status": "disconnected", "qr": None, "phone": None,
                    "private_chats_enabled": True, "group_chats_enabled": False,
                    "require_keyword": "", "allowed_numbers": [], "blocked_numbers": []}
        agent_id = f"personal_{username}"
        bridge = await bridge_get_status(agent_id)
        # Telefonnummer in Config speichern (für Loop-Schutz)
        bridge_phone = bridge.get("phone") or ""
        if bridge_phone and cfg.get("phone") != bridge_phone:
            from .whatsapp_agent import save_whatsapp_config as _save_wa
            cfg["phone"] = bridge_phone
            _save_wa(username, cfg)

        return {
            "configured": True,
            "status": bridge.get("status", "disconnected"),
            "qr": bridge.get("qr"),
            "phone": bridge_phone or cfg.get("phone", ""),
            "private_chats_enabled": cfg.get("private_chats_enabled", True),
            "group_chats_enabled":   cfg.get("group_chats_enabled", False),
            "require_keyword":       cfg.get("require_keyword", ""),
            "allowed_numbers":       cfg.get("allowed_numbers", []),
            "blocked_numbers":       cfg.get("blocked_numbers", []),
            "owner_numbers":         cfg.get("owner_numbers", []),
        }

    @auth_router.put("/me/whatsapp/config")
    async def update_my_whatsapp_config(body: dict = Body(...), auth: tuple = Depends(require_auth)):
        username = _username_from_auth(auth)
        from .whatsapp_agent import load_whatsapp_config, save_whatsapp_config
        cfg = load_whatsapp_config(username) or {"enabled": True}
        cfg.update({
            "private_chats_enabled": bool(body.get("private_chats_enabled", cfg.get("private_chats_enabled", True))),
            "group_chats_enabled":   bool(body.get("group_chats_enabled",   cfg.get("group_chats_enabled", False))),
            "require_keyword":       str(body.get("require_keyword", cfg.get("require_keyword", ""))).strip(),
            "allowed_numbers":       list(body.get("allowed_numbers", cfg.get("allowed_numbers", []))),
            "blocked_numbers":       list(body.get("blocked_numbers", cfg.get("blocked_numbers", []))),
            "owner_numbers":         list(body.get("owner_numbers",   cfg.get("owner_numbers", []))),
        })
        save_whatsapp_config(username, cfg)
        return {"updated": True}

    @auth_router.post("/me/whatsapp/connect")
    async def connect_my_whatsapp(auth: tuple = Depends(require_auth)):
        username = _username_from_auth(auth)
        from .whatsapp_agent import bridge_start_session, load_whatsapp_config, save_whatsapp_config
        existing = load_whatsapp_config(username) or {}
        existing["enabled"] = True
        save_whatsapp_config(username, existing)
        agent_id = f"personal_{username}"
        result = await bridge_start_session(agent_id)
        audit_log("whatsapp.connect", details={"user": username})
        return {"configured": True, "status": result.get("status"), "qr": result.get("qr"), "phone": result.get("phone")}

    @auth_router.delete("/me/whatsapp")
    async def delete_my_whatsapp(auth: tuple = Depends(require_auth)):
        username = _username_from_auth(auth)
        from .whatsapp_agent import bridge_disconnect, delete_whatsapp_config
        agent_id = f"personal_{username}"
        await bridge_disconnect(agent_id)
        delete_whatsapp_config(username)
        audit_log("whatsapp.removed", details={"user": username})
        return {"disconnected": True}

    # ── WhatsApp Interner Webhook (Bridge → Core) ─────────────────────────────

    _internal = internal_router or auth_router  # Fallback; wird von main.py mit echtem Router befüllt

    @_internal.post("/whatsapp/bridge-ready")
    async def whatsapp_bridge_ready(request: Request):
        """Bridge meldet sich nach (Neu-)Start — alle konfigurierten Sessions wiederherstellen."""
        import os as _os
        secret = _os.environ.get("WHATSAPP_BRIDGE_SECRET", "")
        if not secret:
            raise HTTPException(503, "WhatsApp-Bridge nicht konfiguriert (WHATSAPP_BRIDGE_SECRET fehlt)")
        if request.headers.get("X-Bridge-Secret", "") != secret:
            raise HTTPException(403, "Ungültiges Bridge-Secret")

        from .whatsapp_agent import bridge_start_session, load_whatsapp_config as _load_wa_cfg

        users = load_users()
        restarted = []
        for username in users:
            cfg = _load_wa_cfg(username)
            if not cfg or not cfg.get("enabled"):
                continue
            agent_id = f"personal_{username}"
            try:
                result = await bridge_start_session(agent_id)
                logger.info("WhatsApp-Session nach Bridge-Neustart: %s → %s", agent_id, result.get("status"))
                restarted.append(agent_id)
            except Exception as e:
                logger.warning("WhatsApp-Session Reconnect fehlgeschlagen für %s: %s", agent_id, e)

        return {"ok": True, "restarted": restarted}

    @_internal.post("/whatsapp/incoming")
    async def whatsapp_incoming(request: Request):
        """Eingehende WhatsApp-Nachrichten vom Bridge-Dienst verarbeiten."""
        import os as _os
        secret = _os.environ.get("WHATSAPP_BRIDGE_SECRET", "")
        if not secret:
            # Kein Secret konfiguriert → Endpoint vollständig sperren
            logger.error(
                "WHATSAPP_BRIDGE_SECRET nicht gesetzt — /internal/whatsapp/incoming verweigert Anfragen. "
                "Bitte WHATSAPP_BRIDGE_SECRET in der Umgebung setzen."
            )
            raise HTTPException(503, "WhatsApp-Bridge nicht konfiguriert (WHATSAPP_BRIDGE_SECRET fehlt)")
        req_secret = request.headers.get("X-Bridge-Secret", "")
        if req_secret != secret:
            raise HTTPException(403, "Ungültiges Bridge-Secret")

        body = await request.json()
        agent_id   = body.get("agent_id", "")
        from_jid   = body.get("from", "")
        from_name  = body.get("from_name", "").strip()
        message    = body.get("message", "").strip()
        media_type = body.get("media_type", "")
        media_data = body.get("media_data", "")

        if not agent_id:
            return {"ok": False, "error": "Fehlende Felder"}

        # Audio/PTT transkribieren
        is_audio = False
        if media_data and media_type.startswith("audio"):
            is_audio = True
            try:
                from .whatsapp_transcribe import transcribe_audio_b64
                transcript = transcribe_audio_b64(media_data, media_type)
                if transcript:
                    message = transcript
                    logger.info("WhatsApp Audio transkribiert: %d Zeichen", len(transcript))
                else:
                    message = "[Sprachnachricht — Transkription fehlgeschlagen]"
            except Exception as e:
                logger.error("Transkriptions-Fehler: %s", e)
                message = "[Sprachnachricht]"

        if not message:
            return {"ok": False, "error": "Fehlende Felder"}

        # Rufnummer aus JID extrahieren (123456789@s.whatsapp.net → 123456789)
        sender = from_jid.split("@")[0] if "@" in from_jid else from_jid
        is_group = from_jid.endswith("@g.us")

        # Filterkonfiguration laden
        username = agent_id.removeprefix("personal_")
        from .whatsapp_agent import load_whatsapp_config as _load_wa
        wa_cfg = _load_wa(username) or {}

        # Agent-Loop-Schutz: Nachrichten von anderen HydraHive-Agenten ignorieren
        all_users = load_users()
        for _uname, _udata in all_users.items():
            if _uname == username:
                continue
            _other_cfg = _load_wa(_uname)
            if not _other_cfg:
                continue
            _other_phone = _other_cfg.get("phone", "").lstrip("+")
            if _other_phone and sender.endswith(_other_phone):
                return {"ok": True, "filtered": "agent_loop"}
        private_ok = wa_cfg.get("private_chats_enabled", True)
        group_ok   = wa_cfg.get("group_chats_enabled", False)
        keyword    = wa_cfg.get("require_keyword", "").strip()
        allowed    = [n.strip() for n in wa_cfg.get("allowed_numbers", []) if n.strip()]
        blocked    = [n.strip() for n in wa_cfg.get("blocked_numbers", []) if n.strip()]
        owners     = [n.strip().lstrip("+") for n in wa_cfg.get("owner_numbers", []) if n.strip()]

        # Typ-Filter
        if is_group and not group_ok:
            return {"ok": True, "filtered": "group_chats_disabled"}
        if not is_group and not private_ok:
            return {"ok": True, "filtered": "private_chats_disabled"}

        # Nummer-Filter
        if blocked and any(sender.endswith(b.lstrip("+")) for b in blocked):
            return {"ok": True, "filtered": "blocked"}
        if allowed and not any(sender.endswith(a.lstrip("+")) for a in allowed):
            return {"ok": True, "filtered": "not_in_allowlist"}

        # Keyword-Filter
        if keyword and keyword.lower() not in message.lower():
            return {"ok": True, "filtered": "keyword_missing"}

        # Butler-Check
        try:
            from .butler_executor import ButlerEvent as _BE, check_flows as _butler
            _event = _BE(
                channel="whatsapp",
                contact_id=sender,
                contact_name=from_name,
                is_known=is_owner,
                message_text=message,
            )
            _butler_actions = await _butler(_event)
            for _act in _butler_actions:
                _sub = _act.get("subtype")
                _p   = _act.get("params", {})
                if _sub == "ignore":
                    return {"ok": True, "filtered": "butler_ignore"}
                elif _sub == "reply_fixed":
                    from .whatsapp_agent import bridge_send as _bsend
                    await _bsend(agent_id, from_jid, _p.get("text", ""))
                    return {"ok": True, "filtered": "butler_reply_fixed"}
                elif _sub == "agent_reply_guided":
                    _instr = str(_p.get("instruction", "")).strip()
                    if _instr:
                        message = _instr + "\n\n" + message
                if _sub in ("agent_reply", "agent_reply_guided", "forward"):
                    agent_id = _p.get("agent_id", agent_id)
        except Exception as _be:
            logger.warning("Butler-Check fehlgeschlagen: %s", _be)

        from .project_config import ProjectAgents as _PA, ProjectConfig as _PC, ProjectIdentity as _PI

        virtual_cfg = _PC(
            id=agent_id,
            identity=_PI(name=agent_id),
            agents=_PA(boss=agent_id, workers=[]),
        )

        # Absender-Kontext und Berechtigungen bestimmen
        sender_label = from_name if from_name else f"+{sender}"
        chat_type    = "Gruppen-Chat" if is_group else "Einzel-Chat"
        is_owner     = bool(owners) and any(sender.endswith(o) for o in owners)

        if is_owner:
            execution_mode = "elevated"
            enriched_msg = f"[WhatsApp {chat_type} von {sender_label} (+{sender}) — vertrauenswürdiger Kontakt]\n{message}"
        else:
            execution_mode = "safe"
            enriched_msg = (
                f"[WhatsApp {chat_type} von {sender_label} (+{sender}) — unbekannter Kontakt]\n"
                f"[ANWEISUNG FÜR DIESEN KONTAKT: "
                f"1. Nenne NICHT den Namen des Besitzers dieses Assistenten. "
                f"2. Beschreibe KEINE spezifischen System-Fähigkeiten (kein 'kann Mails lesen', 'kann Server administrieren' usw.). "
                f"3. Teile keine privaten Daten, Passwörter oder persönliche Informationen. "
                f"4. Stelle dich nur als allgemeinen KI-Assistenten vor. "
                f"5. Führe keine System-Befehle oder Datei-Operationen aus. "
                f"Antworte freundlich und hilfsbereit, aber bleib bei allgemeinen Themen.]\n"
                f"{message}"
            )

        response_parts: list[str] = []
        try:
            async for chunk in orchestrator.handle_message_stream(
                project_id   = agent_id,
                project_cfg  = virtual_cfg,
                content      = enriched_msg,
                sender       = f"whatsapp:{sender}",
                execution_mode = execution_mode,
            ):
                import json as _json
                try:
                    data = _json.loads(chunk[6:]) if chunk.startswith("data: ") else {}
                    if "text" in data:
                        response_parts.append(data["text"])
                except Exception:
                    pass
        except Exception as e:
            logger.error("Orchestrator-Fehler für WhatsApp-Agent %s: %s", agent_id, e)
            return {"ok": False, "error": str(e)}

        response_text = "".join(response_parts).strip()
        if response_text:
            if is_audio:
                # Voice-Antwort: TTS → OGG → senden
                try:
                    from .whatsapp_tts import text_to_ogg_b64
                    from .whatsapp_agent import bridge_send_voice
                    audio_b64 = await text_to_ogg_b64(response_text)
                    if audio_b64:
                        await bridge_send_voice(agent_id, from_jid, audio_b64)
                    else:
                        # TTS fehlgeschlagen → Fallback auf Text
                        from .whatsapp_agent import bridge_send
                        await bridge_send(agent_id, from_jid, response_text)
                except Exception as e:
                    logger.error("TTS/Voice-Send Fehler: %s", e)
                    from .whatsapp_agent import bridge_send
                    await bridge_send(agent_id, from_jid, response_text)
            else:
                # Text-Antwort (max 4096 Zeichen pro Nachricht)
                from .whatsapp_agent import bridge_send
                for i in range(0, len(response_text), 4096):
                    await bridge_send(agent_id, from_jid, response_text[i:i+4096])

        return {"ok": True}

    # ── Mail ────────────────────────────────────────────────────────────────

    @auth_router.get("/me/mail")
    def get_my_mail(auth: tuple = Depends(require_auth)):
        username = _username_from_auth(auth)
        cfg = load_mail_config(username)
        if not cfg:
            return {"configured": False}
        return {"configured": True, "mail_address": cfg.get("mail_address", ""), "smtp_host": cfg.get("smtp_host", "")}

    @auth_router.put("/me/mail")
    async def update_my_mail(req: MailConfigRequest, auth: tuple = Depends(require_auth)):
        username = _username_from_auth(auth)
        kas_cfg = _kas_config()
        domain = req.domain.strip() or kas_cfg.get("default_domain", "")
        if not domain:
            raise HTTPException(400, "Keine Domain angegeben und keine Default-Domain in KAS-Config")

        local_part = (req.mail_address.split("@")[0] if "@" in req.mail_address else req.mail_address.strip()) or f"personal_{username}"
        mail_address = f"{local_part}@{domain}"

        if req.create_account:
            if not kas_cfg:
                raise HTTPException(400, "KAS nicht konfiguriert — /etc/hydrahive/kas.json fehlt")
            import secrets, string
            alphabet = string.ascii_letters + string.digits + "!@#$%"
            while True:
                password = "".join(secrets.choice(alphabet) for _ in range(16))
                if (any(c.isdigit() for c in password) and
                    any(c in "!@#$%" for c in password) and
                    any(c.isupper() for c in password)):
                    break
            result = await kas_create_mailaccount(local_part, domain, password, kas_cfg)
            if not result["ok"] and "already_exists" not in result.get("error", ""):
                raise HTTPException(400, f"KAS Fehler: {result['error']}")
            smtp_host = kas_cfg.get("smtp_host", f"smtp.{domain}")
            cfg = {
                "mail_address": mail_address,
                "smtp_host": smtp_host,
                "smtp_port": kas_cfg.get("smtp_port", 587),
                "smtp_user": mail_address,
                "smtp_password": password,
            }
            save_mail_config(username, cfg)
            audit_log("mail.created", details={"user": username, "address": mail_address})
            return {"configured": True, "mail_address": mail_address, "created": True}

        # Manuelle Konfiguration oder nur Adresse speichern
        existing = load_mail_config(username) or {}
        existing["mail_address"] = mail_address
        if req.smtp_host.strip():
            existing["smtp_host"] = req.smtp_host.strip()
            existing["smtp_port"] = req.smtp_port
            existing["smtp_user"] = req.smtp_user.strip() or mail_address
            if req.smtp_password.strip():
                existing["smtp_password"] = req.smtp_password.strip()
            if req.imap_host.strip():
                existing["imap_host"] = req.imap_host.strip()
        save_mail_config(username, existing)
        audit_log("mail.configured", details={"user": username, "address": mail_address, "manual": bool(req.smtp_host.strip())})
        return {"configured": True, "mail_address": mail_address, "created": False}

    @auth_router.delete("/me/mail")
    def delete_my_mail(auth: tuple = Depends(require_auth)):
        username = _username_from_auth(auth)
        p = _mail_config_path(username)
        if p.exists():
            p.unlink()
        audit_log("mail.removed", details={"user": username})
        return {"deleted": True}

    # ── Telegram ──────────────────────────────────────────────────────────────

    @auth_router.get("/me/telegram")
    async def get_my_telegram(auth: tuple = Depends(require_auth)):
        from .telegram_agent import load_telegram_config, get_bot_status
        username = _username_from_auth(auth)
        cfg = load_telegram_config(username) or {}
        agent_id = f"personal_{username}"
        status = get_bot_status(agent_id) if cfg.get("enabled") else "stopped"
        return {
            "configured":      bool(cfg.get("bot_token")),
            "enabled":         cfg.get("enabled", False),
            "status":          status,
            "bot_username":    cfg.get("bot_username", ""),
            "allow_private":   cfg.get("allow_private", True),
            "allow_groups":    cfg.get("allow_groups", False),
            "require_keyword": cfg.get("require_keyword", ""),
            "allowed_user_ids": cfg.get("allowed_user_ids", []),
            "blocked_user_ids": cfg.get("blocked_user_ids", []),
            "admin_user_ids":  cfg.get("admin_user_ids", []),
        }

    @auth_router.post("/me/telegram/connect")
    async def connect_my_telegram(body: dict = Body(...), auth: tuple = Depends(require_auth)):
        from .telegram_agent import (
            load_telegram_config, save_telegram_config,
            start_telegram_bot, stop_telegram_bot,
        )
        username = _username_from_auth(auth)
        token = body.get("bot_token", "").strip()
        if not token:
            raise HTTPException(400, "bot_token fehlt")

        agent_id = f"personal_{username}"
        await stop_telegram_bot(agent_id)

        cfg = load_telegram_config(username) or {}
        cfg.update({
            "enabled": True,
            "bot_token": token,
            "allow_private":   body.get("allow_private", cfg.get("allow_private", True)),
            "allow_groups":    body.get("allow_groups", cfg.get("allow_groups", False)),
            "require_keyword": body.get("require_keyword", cfg.get("require_keyword", "")),
            "allowed_user_ids": body.get("allowed_user_ids", cfg.get("allowed_user_ids", [])),
            "blocked_user_ids": body.get("blocked_user_ids", cfg.get("blocked_user_ids", [])),
            "admin_user_ids":  body.get("admin_user_ids", cfg.get("admin_user_ids", [])),
        })
        save_telegram_config(username, cfg)

        result = await start_telegram_bot(agent_id, username, token, cfg, orchestrator)
        if result.get("bot_username"):
            cfg["bot_username"] = result["bot_username"]
            save_telegram_config(username, cfg)

        audit_log("telegram.connect", details={"user": username, "status": result.get("status")})
        return {
            "configured": True,
            "status": result.get("status", "running"),
            "bot_username": cfg.get("bot_username", ""),
        }

    @auth_router.put("/me/telegram/config")
    async def update_my_telegram_config(body: dict = Body(...), auth: tuple = Depends(require_auth)):
        from .telegram_agent import load_telegram_config, save_telegram_config
        username = _username_from_auth(auth)
        cfg = load_telegram_config(username)
        if not cfg:
            raise HTTPException(404, "Telegram nicht konfiguriert")
        for field in ("allow_private", "allow_groups", "require_keyword",
                      "allowed_user_ids", "blocked_user_ids", "admin_user_ids"):
            if field in body:
                cfg[field] = body[field]
        save_telegram_config(username, cfg)
        return {"updated": True}

    @auth_router.delete("/me/telegram")
    async def delete_my_telegram(auth: tuple = Depends(require_auth)):
        from .telegram_agent import delete_telegram_config, stop_telegram_bot
        username = _username_from_auth(auth)
        agent_id = f"personal_{username}"
        await stop_telegram_bot(agent_id)
        delete_telegram_config(username)
        audit_log("telegram.disconnect", details={"user": username})
        return {"disconnected": True}
