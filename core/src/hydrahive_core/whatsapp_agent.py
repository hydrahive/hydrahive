"""
whatsapp_agent.py — WhatsApp Bridge Integration für persönliche Agenten

Kommuniziert mit dem Node.js WhatsApp Bridge Service (Baileys).

Config: /etc/octopos/agent_tokens/personal_{username}_whatsapp.json
Format: {"enabled": true}

Bridge läuft auf http://127.0.0.1:8767
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

TOKEN_DIR  = Path("/etc/octopos/agent_tokens")
BRIDGE_URL = "http://127.0.0.1:8767"


# ── Config-Persistenz ────────────────────────────────────────────────────────

def load_whatsapp_config(username: str) -> dict | None:
    p = TOKEN_DIR / f"personal_{username}_whatsapp.json"
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        return data or None
    except Exception as e:
        logger.warning("WhatsApp-Config für %s nicht lesbar: %s", username, e)
        return None


def save_whatsapp_config(username: str, cfg: dict) -> None:
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    p = TOKEN_DIR / f"personal_{username}_whatsapp.json"
    p.write_text(json.dumps(cfg, indent=2))
    p.chmod(0o600)


def delete_whatsapp_config(username: str) -> None:
    p = TOKEN_DIR / f"personal_{username}_whatsapp.json"
    if p.exists():
        p.unlink()


# ── Bridge-Kommunikation ─────────────────────────────────────────────────────

async def bridge_start_session(agent_id: str) -> dict:
    """Session in der Bridge starten oder bestehende abrufen."""
    import httpx
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(f"{BRIDGE_URL}/sessions/{agent_id}/start")
        r.raise_for_status()
        return r.json()


async def bridge_get_status(agent_id: str) -> dict:
    """Session-Status und ggf. QR-Code abrufen."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{BRIDGE_URL}/sessions/{agent_id}/status")
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"status": "bridge_unavailable", "error": str(e), "qr": None, "phone": None}


async def bridge_disconnect(agent_id: str) -> dict:
    """Session trennen und Auth-Daten löschen."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.delete(f"{BRIDGE_URL}/sessions/{agent_id}")
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"disconnected": False, "error": str(e)}


async def bridge_send(agent_id: str, to: str, message: str) -> dict:
    """Nachricht über WhatsApp senden."""
    import httpx
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            f"{BRIDGE_URL}/sessions/{agent_id}/send",
            json={"to": to, "message": message},
        )
        r.raise_for_status()
        return r.json()


async def bridge_health() -> bool:
    """Prüfen ob die Bridge erreichbar ist."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=3) as client:
            r = await client.get(f"{BRIDGE_URL}/health")
            return r.status_code == 200
    except Exception:
        return False


# ── Startup ──────────────────────────────────────────────────────────────────

async def setup_whatsapp_sessions(*, load_users, logger_) -> None:
    """WhatsApp-Sessions für alle konfigurierten User beim Core-Start wiederherstellen."""
    if not await bridge_health():
        logger_.info("WhatsApp Bridge nicht erreichbar — Sessions werden nicht wiederhergestellt")
        return

    users = load_users()
    for username in users:
        cfg = load_whatsapp_config(username)
        if not cfg or not cfg.get("enabled"):
            continue
        agent_id = f"personal_{username}"
        try:
            result = await bridge_start_session(agent_id)
            logger_.info(
                "WhatsApp-Session für '%s' (Agent: %s) gestartet: %s",
                username, agent_id, result.get("status"),
            )
        except Exception as e:
            logger_.warning("WhatsApp-Session für '%s' nicht gestartet: %s", username, e)
