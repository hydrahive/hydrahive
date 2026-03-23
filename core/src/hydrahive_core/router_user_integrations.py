from __future__ import annotations

import logging
import subprocess as _sp
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel


logger = logging.getLogger(__name__)


class WksConfigRequest(BaseModel):
    ip: str
    ssh_user: str = ""
    ollama_port: int = 11434
    ssh_key: str = ""


class DiscordConfigRequest(BaseModel):
    bot_token: str = ""           # leer = bestehenden Token behalten
    guild_id: str = ""
    channel_ids: list[str] = []
    ignore_bots: bool = True      # Nachrichten von anderen Bots ignorieren
    require_mention: bool = False  # Nur bei @Mention antworten


SUPPORTED_PLATFORMS = {
    "matrix": {"supported": True, "label": "Matrix"},
    "discord": {"supported": True, "label": "Discord"},
    "wks": {"supported": True, "label": "WKS"},
    "telegram": {"supported": False, "label": "Telegram"},
    "whatsapp": {"supported": False, "label": "WhatsApp"},
    "signal": {"supported": False, "label": "Signal"},
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

    user = users.get(username, {})
    wks = user.get("wks", {})
    discord_cfg = load_discord_config(username)
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
            "platform": "telegram",
            "label": SUPPORTED_PLATFORMS["telegram"]["label"],
            "supported": False,
            "configured": False,
            "connected": False,
            "details": {"status": "planned"},
        },
        {
            "platform": "whatsapp",
            "label": SUPPORTED_PLATFORMS["whatsapp"]["label"],
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
            orchestrator=orchestrator,
        )
        _discord_clients[username] = client
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
) -> None:
    @auth_router.get("/me/wks")
    def get_my_wks(auth: tuple = Depends(require_auth)):
        username, _ = auth
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
        username, _ = auth
        users = load_users()
        return {
            "username": username,
            "platforms": _build_platform_overview(username, users, wks_keys_dir),
        }

    @auth_router.put("/me/wks")
    async def update_my_wks(req: WksConfigRequest, auth: tuple = Depends(require_auth)):
        username, _ = auth
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

        username, _ = auth
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

        username, _ = auth
        wks_keys_dir.mkdir(parents=True, exist_ok=True)
        key_file = wks_keys_dir / username
        with tempfile.NamedTemporaryFile(delete=False, suffix="_wks") as tf:
            tmp_path = tf.name
        try:
            _sp.run(
                ["ssh-keygen", "-t", "ed25519", "-f", tmp_path, "-N", "", "-C", f"octopos-wks@{username}"],
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

        username, _ = auth
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

        username, _ = auth
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
        username, _ = auth
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
            "connected": discord_client_connected(username),
        }

    @auth_router.get("/me/discord/channels")
    async def get_my_discord_channels(auth: tuple = Depends(require_auth)):
        """Channels der konfigurierten Guild per Discord REST API abrufen."""
        import httpx
        username, _ = auth
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

    @auth_router.put("/me/discord", status_code=200)
    async def update_my_discord(req: DiscordConfigRequest, auth: tuple = Depends(require_auth)):
        username, _ = auth
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
        }
        save_discord_config(username, cfg)
        personal_agent_id = f"personal_{username}"

        test_client = AgentDiscordClient(
            agent_id=personal_agent_id,
            bot_token=cfg["bot_token"],
            guild_id=cfg["guild_id"],
            channel_ids=cfg["channel_ids"],
            ignore_bots=cfg.get("ignore_bots", True),
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
            orchestrator=orchestrator,
        )
        _discord_clients[username] = client
        await runtime.attach_discord_client(username, client)

        audit_log("discord.configured", details={"user": username, "bot": test_result.get("bot_name", "")})
        logger.info("Discord konfiguriert fuer %s: Bot '%s'", username, test_result.get("bot_name", ""))
        return {"updated": True, "bot_name": test_result.get("bot_name", ""), "bot_id": test_result.get("bot_id", "")}

    @auth_router.delete("/me/discord", status_code=200)
    async def delete_my_discord(auth: tuple = Depends(require_auth)):
        username, _ = auth
        from .discord_agent import delete_discord_config
        from .tool_registry import _discord_clients

        await runtime.detach_discord_client(username)
        _discord_clients.pop(username, None)
        delete_discord_config(username)
        audit_log("discord.removed", details={"user": username})
        return {"deleted": True}

    @auth_router.post("/me/discord/test")
    async def test_my_discord(auth: tuple = Depends(require_auth)):
        username, _ = auth
        from .discord_agent import AgentDiscordClient, load_discord_config

        cfg = load_discord_config(username)
        if not cfg:
            raise HTTPException(400, "Discord nicht konfiguriert")
        test_client = AgentDiscordClient(
            agent_id=f"personal_{username}",
            bot_token=cfg["bot_token"],
            guild_id=cfg["guild_id"],
            channel_ids=cfg.get("channel_ids", []),
            orchestrator=orchestrator,
        )
        return await test_client.test_connection()
