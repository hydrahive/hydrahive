from __future__ import annotations

import logging
import re
import subprocess as _sp
from pathlib import Path

from fastapi import APIRouter, Body, Depends, HTTPException, Request
from fastapi.responses import Response
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


_USERNAME_RE = re.compile(r"^[a-z0-9._-]+$")


def _sanitize_username(username: str) -> str:
    """Validiert Username — erlaubt Dots, verhindert Path-Traversal."""
    if not username or ".." in username or "/" in username or not _USERNAME_RE.fullmatch(username):
        raise ValueError(f"Ungültiger Username: '{username}'")
    return username


def _username_from_auth(auth: tuple) -> str:
    username, _ = auth
    try:
        return _sanitize_username(username)
    except ValueError as e:
        raise HTTPException(400, str(e))


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
    import json as _json

    login    = kas_cfg.get("login", "")
    auth_data = kas_cfg.get("password", "")
    if not login:
        return {"ok": False, "error": "KAS nicht konfiguriert (/etc/hydrahive/kas.json)"}
    # #282: JSON-Serialisierung statt f-Strings — verhindert XML/JSON-Injection
    params = _json.dumps({
        "kas_login": login,
        "kas_auth_data": auth_data,
        "kas_auth_type": "plain",
        "kas_action": "add_mailaccount",
        "KasRequestParams": {
            "local_part": local_part,
            "domain_part": domain_part,
            "mail_password": password,
        },
    }, ensure_ascii=True)
    soap = f"""<?xml version="1.0" encoding="UTF-8"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/" xmlns:ns1="urn:xmethodsKasApi">
<SOAP-ENV:Body>
  <ns1:KasApi>
    <Params><![CDATA[{params}]]></Params>
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

    client = _discord_clients.get(f"personal_{username}")
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
    projects=None,
    internal_router: APIRouter | None = None,
) -> None:

    # ── Prompt-Coach (#169) ──────────────────────────────────────

    COACH_SYSTEM_PROMPT = (
        "Du bist ein Prompt-Qualitäts-Checker. Bewerte ob der User-Prompt "
        "klar genug ist damit ein KI-Agent ihn gut beantworten kann.\n\n"
        "Antworte NUR als JSON: {\"ok\": true} oder {\"ok\": false, \"suggestion\": \"verbesserter Prompt\", \"reason\": \"kurze Begründung\"}\n\n"
        "NICHT OK: kein Kontext, zu vage (\"mach mal\", \"fix das\"), kein Ziel beschrieben, "
        "mehrere komplexe Aufgaben ohne Struktur.\n"
        "OK: spezifische Datei/Funktion genannt, klares Ziel, auch kurze Prompts wenn spezifisch.\n"
        "Kurze Follow-ups sind OK (\"ja mach das\", \"genau so\", \"weiter\")."
    )

    @auth_router.post("/me/agent/coach")
    async def prompt_coach(body: dict = Body(...), auth: tuple = Depends(require_auth)):
        """LLM-gestützter Prompt-Qualitätscheck vor dem Senden."""
        import json as _json
        content = body.get("content", "").strip()
        if not content:
            return {"ok": True}

        # Coach-Config laden
        from .router_llm import _load_llm_config
        cfg = _load_llm_config()
        coach_cfg = cfg.get("coach", {})
        if not coach_cfg.get("enabled"):
            return {"ok": True}
        coach_model = coach_cfg.get("model", "")
        if not coach_model:
            return {"ok": True}

        try:
            from .orchestrator_llm import _resolve_model, _llm_with_retry
            import litellm
            resolved_model, extra_kwargs = _resolve_model(coach_model, cfg.get("providers", {}).get("ollama", {}).get("base_url"))
            messages = [
                {"role": "system", "content": COACH_SYSTEM_PROMPT},
                {"role": "user", "content": content},
            ]

            async def _call():
                return await litellm.acompletion(
                    model=resolved_model, messages=messages,
                    max_tokens=200, temperature=0.1,
                    timeout=5, **extra_kwargs,
                )

            resp = await _llm_with_retry(_call)
            text = (resp.choices[0].message.content or "").strip()
            # JSON aus der Antwort extrahieren
            if "{" in text:
                json_str = text[text.index("{"):text.rindex("}") + 1]
                result = _json.loads(json_str)
                return {
                    "ok": result.get("ok", True),
                    "suggestion": result.get("suggestion"),
                    "reason": result.get("reason"),
                }
            return {"ok": True}
        except Exception as e:
            logger.debug("Prompt-Coach Fehler (durchlassen): %s", e)
            return {"ok": True}  # Bei Fehler: durchlassen

    @auth_router.get("/me/credentials")
    def get_my_credentials(auth: tuple = Depends(require_auth)):
        """Alle Zugangsdaten des eingeloggten Users."""
        import subprocess as _sp
        username = _username_from_auth(auth)
        users = load_users()
        user = users.get(username, {})

        # Samba-Credentials für persönliches Projekt
        samba_user = f"proj_personal_{username}"
        samba_pw = None
        try:
            r = _sp.run(["sudo", "grep", f"^{samba_user}:", "/etc/hydrahive/samba_credentials"],
                        capture_output=True, text=True, timeout=5)
            if r.returncode == 0 and ":" in r.stdout.strip():
                samba_pw = r.stdout.strip().split(":", 1)[1]
        except Exception:
            pass

        # Alle Projekt-Samba-Credentials die der User sehen darf
        samba_shares = []
        allowed = user.get("allowed_projects") or []
        if projects:
            for pid in (projects.projects if not allowed else [p for p in projects.projects if p in allowed or p == f"personal_{username}"]):
                s_user = f"proj_{pid}"
                s_pw = None
                try:
                    r = _sp.run(["sudo", "grep", f"^{s_user}:", "/etc/hydrahive/samba_credentials"],
                                capture_output=True, text=True, timeout=3)
                    if r.returncode == 0 and ":" in r.stdout.strip():
                        s_pw = r.stdout.strip().split(":", 1)[1]
                except Exception:
                    pass
                if s_pw:
                    samba_shares.append({"project": pid, "username": s_user, "password": s_pw})

        # Tailscale
        tailscale_ip = None
        try:
            r = _sp.run(["tailscale", "ip", "-4"], capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                tailscale_ip = r.stdout.strip()
        except Exception:
            pass

        # Server-IP
        import socket
        try:
            server_ip = socket.gethostbyname(socket.gethostname())
        except Exception:
            server_ip = "127.0.0.1"

        return {
            "username": username,
            "role": user.get("role", "user"),
            "group": user.get("group", "standard"),
            "server_ip": server_ip,
            "tailscale_ip": tailscale_ip,
            "samba": {
                "shares": samba_shares,
                "hint": f"\\\\{tailscale_ip or server_ip}\\ + Projektname",
            },
            "wks": user.get("wks", {}),
            "console_url": f"https://{tailscale_ip or server_ip}",
        }

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
        _os.unlink(tmp_path)  # ssh-keygen braucht nicht-existente Zieldatei
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
            if test_result.get("invalid_token"):
                delete_discord_config(username)
            raise HTTPException(400, f"Discord-Verbindung fehlgeschlagen: {test_result.get('error', '')}")

        await runtime.detach_discord_client(personal_agent_id)
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

        personal_agent_id = f"personal_{username}"
        await runtime.detach_discord_client(personal_agent_id)
        _discord_clients.pop(personal_agent_id, None)
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
                    "private_chats_enabled": cfg.get("private_chats_enabled", True) if cfg else True,
                    "group_chats_enabled":   cfg.get("group_chats_enabled", False) if cfg else False,
                    "require_keyword":       cfg.get("require_keyword", "") if cfg else "",
                    "allowed_numbers":       cfg.get("allowed_numbers", []) if cfg else [],
                    "blocked_numbers":       cfg.get("blocked_numbers", []) if cfg else [],
                    "owner_numbers":         cfg.get("owner_numbers", []) if cfg else []}
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
            "bridge_error": bridge.get("error") or None,
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
            "voice_mode":            str(body.get("voice_mode", cfg.get("voice_mode", "echo"))).strip(),
            "voice_name":            str(body.get("voice_name", cfg.get("voice_name", "de-DE-KatjaNeural"))).strip(),
        })
        save_whatsapp_config(username, cfg)
        return {"updated": True}

    @auth_router.post("/me/whatsapp/voice-preview")
    async def whatsapp_voice_preview(body: dict = Body(...), auth: tuple = Depends(require_auth)):
        """Spielt eine TTS-Stimme als Preview ab."""
        voice = body.get("voice", "de-DE-KatjaNeural")
        text = body.get("text", "Hallo, ich bin dein HydraHive Assistent. So klinge ich!")
        if len(text) > 200:
            text = text[:200]
        try:
            from .whatsapp_tts import text_to_ogg_b64
            audio_b64 = await text_to_ogg_b64(text, voice=voice)
            if audio_b64:
                import base64
                audio_bytes = base64.b64decode(audio_b64)
                return Response(content=audio_bytes, media_type="audio/ogg")
            return Response(content=b"", status_code=500)
        except Exception as e:
            raise HTTPException(500, f"TTS Preview fehlgeschlagen: {e}")

    @auth_router.get("/me/whatsapp/voices")
    def list_whatsapp_voices(auth: tuple = Depends(require_auth)):
        """Liste der verfügbaren TTS-Stimmen."""
        return {"voices": [
            {"id": "de-DE-KatjaNeural",    "label": "Katja (DE, weiblich)",     "lang": "de"},
            {"id": "de-DE-ConradNeural",   "label": "Conrad (DE, männlich)",    "lang": "de"},
            {"id": "de-AT-IngridNeural",   "label": "Ingrid (AT, weiblich)",    "lang": "de"},
            {"id": "de-CH-LeniNeural",     "label": "Leni (CH, weiblich)",      "lang": "de"},
            {"id": "en-US-JennyNeural",    "label": "Jenny (US, female)",       "lang": "en"},
            {"id": "en-US-GuyNeural",      "label": "Guy (US, male)",           "lang": "en"},
            {"id": "en-GB-SoniaNeural",    "label": "Sonia (GB, female)",       "lang": "en"},
            {"id": "en-GB-RyanNeural",     "label": "Ryan (GB, male)",          "lang": "en"},
            {"id": "fr-FR-DeniseNeural",   "label": "Denise (FR, féminin)",     "lang": "fr"},
            {"id": "es-ES-ElviraNeural",   "label": "Elvira (ES, femenino)",    "lang": "es"},
            {"id": "it-IT-ElsaNeural",     "label": "Elsa (IT, femminile)",     "lang": "it"},
            {"id": "tr-TR-EmelNeural",     "label": "Emel (TR, kadın)",         "lang": "tr"},
            {"id": "pl-PL-AgnieszkaNeural","label": "Agnieszka (PL, kobieta)",  "lang": "pl"},
            {"id": "ru-RU-SvetlanaNeural", "label": "Svetlana (RU, женский)",   "lang": "ru"},
        ]}

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

    @auth_router.post("/me/whatsapp/install-chromium")
    async def install_whatsapp_chromium(auth: tuple = Depends(require_auth)):
        """Installiert Chromium (Puppeteer-Backend) falls noch nicht vorhanden."""
        from .router_doctor import _fix_install_chromium
        result = await _fix_install_chromium()
        if result.get("ok"):
            # Bridge neu starten damit sie Chromium erkennt
            import subprocess as _sp
            _sp.run(["sudo", "-n", "systemctl", "restart", "hydrahive-whatsapp-bridge"],
                    capture_output=True, timeout=15)
        return result

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

        logger.info("WhatsApp /incoming: agent=%s from=%s", agent_id or "(leer)", from_jid or "(leer)")
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

        # Status-Broadcasts ignorieren (würde sonst als Status gepostet)
        if from_jid == "status@broadcast" or from_jid.endswith("@broadcast"):
            return {"ok": True, "filtered": "status_broadcast"}

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
        owners     = [re.sub(r'\s+', '', n).lstrip("+") for n in wa_cfg.get("owner_numbers", []) if n.strip()]

        logger.info(
            "WhatsApp filter: agent=%s sender=%s is_group=%s private_ok=%s group_ok=%s "
            "keyword=%r allowed=%s blocked=%s",
            agent_id, sender, is_group, private_ok, group_ok,
            keyword, bool(allowed), bool(blocked),
        )

        # Typ-Filter
        if is_group and not group_ok:
            logger.info("WhatsApp filtered (group_chats_disabled): agent=%s sender=%s", agent_id, sender)
            return {"ok": True, "filtered": "group_chats_disabled"}
        if not is_group and not private_ok:
            logger.info("WhatsApp filtered (private_chats_disabled): agent=%s sender=%s", agent_id, sender)
            return {"ok": True, "filtered": "private_chats_disabled"}

        # Nummer-Filter
        if blocked and any(sender.endswith(b.lstrip("+")) for b in blocked):
            logger.info("WhatsApp filtered (blocked): agent=%s sender=%s", agent_id, sender)
            return {"ok": True, "filtered": "blocked"}
        if allowed and not any(sender.endswith(a.lstrip("+")) for a in allowed):
            logger.info("WhatsApp filtered (not_in_allowlist): agent=%s sender=%s", agent_id, sender)
            return {"ok": True, "filtered": "not_in_allowlist"}

        # Keyword-Filter
        if keyword and keyword.lower() not in message.lower():
            logger.info("WhatsApp filtered (keyword_missing): agent=%s sender=%s", agent_id, sender)
            return {"ok": True, "filtered": "keyword_missing"}

        # Butler-Check
        try:
            from .butler_executor import ButlerEvent as _BE, check_flows as _butler, execute_generic_actions as _butler_generic
            _event = _BE(
                channel="whatsapp",
                contact_id=sender,
                contact_name=from_name,
                is_known=bool(owners) and any(sender.endswith(o) for o in owners),
                message_text=message,
            )
            _butler_actions = await _butler(_event, owner=username)
            import asyncio as _aio
            _aio.create_task(_butler_generic(_butler_actions, _event))
            for _act in _butler_actions:
                _sub = _act.get("subtype")
                _p   = _act.get("params", {})
                if _sub == "ignore":
                    return {"ok": True, "filtered": "butler_ignore"}
                elif _sub == "reply_fixed":
                    # #321: Voice-Modus auch bei reply_fixed prüfen
                    _fixed_text = _p.get("text", "")
                    _voice_mode = wa_cfg.get("voice_mode", "echo") if wa_cfg else "echo"
                    _voice_name = wa_cfg.get("voice_name", "de-DE-KatjaNeural") if wa_cfg else "de-DE-KatjaNeural"
                    _send_voice = (_voice_mode == "echo" and is_audio) or (_voice_mode == "always")
                    if _send_voice and _voice_mode != "never":
                        try:
                            from .whatsapp_tts import text_to_ogg_b64 as _tts
                            from .whatsapp_agent import bridge_send_voice as _bsv
                            _audio = await _tts(_fixed_text, voice=_voice_name)
                            if _audio:
                                await _bsv(agent_id, from_jid, _audio)
                            else:
                                from .whatsapp_agent import bridge_send as _bsend
                                await _bsend(agent_id, from_jid, _fixed_text)
                        except Exception:
                            from .whatsapp_agent import bridge_send as _bsend
                            await _bsend(agent_id, from_jid, _fixed_text)
                    else:
                        from .whatsapp_agent import bridge_send as _bsend
                        await _bsend(agent_id, from_jid, _fixed_text)
                    return {"ok": True, "filtered": "butler_reply_fixed"}
                elif _sub == "agent_reply_guided":
                    _instr = str(_p.get("instruction", "")).strip()
                    if _instr:
                        from .butler_executor import get_agent_display_name as _gname
                        _name = _gname(agent_id)
                        message = f"Dein Name ist {_name}.\n{_instr}\n\n" + message
                if _sub in ("agent_reply", "agent_reply_guided", "forward"):
                    _new_agent = _p.get("agent_id", "").strip()
                    if _new_agent:
                        logger.info("Butler leitet WhatsApp-Nachricht um: %s → %s", agent_id, _new_agent)
                        agent_id = _new_agent
                    # else: agent_id bleibt unverändert
        except Exception as _be:
            logger.warning("Butler-Check fehlgeschlagen: %s", _be)

        # #320: Original WhatsApp-Session-ID für Bridge-Sends (Butler kann agent_id umrouten)
        wa_session_id = f"personal_{username}"

        from .project_config import ProjectAgents as _PA, ProjectConfig as _PC, ProjectIdentity as _PI

        # Echte Projekt-Config laden (inkl. Identity/Soul), Fallback auf Minimal-Config
        real_cfg = projects.get(agent_id) if projects else None
        virtual_cfg = real_cfg or _PC(
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
                f"2. Beschreibe KEINE internen System-Fähigkeiten (kein 'kann Mails lesen', 'kann Server administrieren' usw.). "
                f"3. Teile keine privaten Daten, Passwörter oder persönliche Informationen des Besitzers. "
                f"4. Stelle dich als allgemeinen KI-Assistenten vor — du darfst aber Web-Suche und öffentliche URLs abrufen. "
                f"5. Führe KEINE System-Befehle, Datei-Operationen oder interne Admin-Aktionen aus. "
                f"Antworte freundlich und hilfsbereit. Web-Suche und URL-Abruf sind erlaubt.]\n"
                f"{message}"
            )

        logger.info(
            "WhatsApp incoming: agent=%s sender=%s is_owner=%s is_group=%s msg_len=%d",
            agent_id, sender, is_owner, is_group, len(message),
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
                    elif "error" in data and data["error"]:
                        # Orchestrator-Fehler (z.B. Boss-Agent nicht gefunden) als Antwort behandeln
                        err_txt = str(data["error"])
                        logger.error("WhatsApp Orchestrator-Error für %s: %s", agent_id, err_txt)
                        response_parts.append(f"[Fehler: {err_txt}]")
                except Exception:
                    pass
        except Exception as e:
            logger.error("Orchestrator-Fehler für WhatsApp-Agent %s: %s", agent_id, e)
            return {"ok": False, "error": str(e)}

        logger.info(
            "WhatsApp orchestrator done: agent=%s response_len=%d",
            agent_id, sum(len(p) for p in response_parts),
        )

        response_text = "".join(response_parts).strip()
        if response_text:
            # Voice-Modus bestimmen (#172)
            voice_mode = wa_cfg.get("voice_mode", "echo") if wa_cfg else "echo"
            voice_name = wa_cfg.get("voice_name", "de-DE-KatjaNeural") if wa_cfg else "de-DE-KatjaNeural"
            send_voice = (
                (voice_mode == "echo" and is_audio) or
                (voice_mode == "always")
            )
            if send_voice and voice_mode != "never":
                # Voice-Antwort: TTS → OGG → senden
                try:
                    from .whatsapp_tts import text_to_ogg_b64
                    from .whatsapp_agent import bridge_send_voice
                    audio_b64 = await text_to_ogg_b64(response_text, voice=voice_name)
                    if audio_b64:
                        await bridge_send_voice(wa_session_id, from_jid, audio_b64)
                    else:
                        from .whatsapp_agent import bridge_send
                        await bridge_send(wa_session_id, from_jid, response_text)
                except Exception as e:
                    logger.error("TTS/Voice-Send Fehler: %s", e)
                    from .whatsapp_agent import bridge_send
                    await bridge_send(wa_session_id, from_jid, response_text)
            else:
                # Text-Antwort (max 4096 Zeichen pro Nachricht)
                from .whatsapp_agent import bridge_send
                logger.info("WhatsApp bridge_send: agent=%s to=%s len=%d", wa_session_id, from_jid, len(response_text))
                try:
                    for i in range(0, len(response_text), 4096):
                        await bridge_send(wa_session_id, from_jid, response_text[i:i+4096])
                    logger.info("WhatsApp reply sent: agent=%s to=%s", wa_session_id, from_jid)
                except Exception as e:
                    logger.error("WhatsApp bridge_send Fehler: agent=%s to=%s error=%s", agent_id, from_jid, e)
                    return {"ok": False, "error": f"bridge_send: {e}"}

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
