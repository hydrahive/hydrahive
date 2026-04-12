"""
whatsapp_agent.py — WhatsApp Bridge Integration pro Projekt (#615)

Kommuniziert mit dem Node.js WhatsApp Bridge Service (Baileys).
Jedes Projekt kann seine eigene WhatsApp-Session haben — keine geteilten
Verbindungen mehr zwischen Projekten.

Config: /etc/hydrahive/agent_tokens/{project_id}_whatsapp.json
Format: {"enabled": true, "phone": "...", "private_chats_enabled": ..., ...}

Bridge läuft auf http://127.0.0.1:8767
Bridge-Session-ID = project_id (1:1).

Backwards-Compat: Bestehende personal_{username}_whatsapp.json-Dateien
bleiben gültig, weil das persönliche Projekt den Namen personal_{username} hat.
"""

import json
import logging
from pathlib import Path

from .settings import settings

logger = logging.getLogger(__name__)

TOKEN_DIR  = settings.agent_tokens_dir
BRIDGE_URL = "http://127.0.0.1:8767"


# ── Config-Persistenz ────────────────────────────────────────────────────────

def _config_path(project_id: str) -> Path:
    return TOKEN_DIR / f"{project_id}_whatsapp.json"


def load_whatsapp_config(project_id: str) -> dict | None:
    """WhatsApp-Config für ein Projekt laden.

    Historisch wurde username übergeben (personal_{username}_whatsapp.json).
    In v2 wird project_id übergeben — das persönliche Projekt heißt
    personal_{username}, also ergibt sich der gleiche Dateiname.
    """
    p = _config_path(project_id)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text())
        return data or None
    except Exception as e:
        logger.warning("WhatsApp-Config für %s nicht lesbar: %s", project_id, e)
        return None


def save_whatsapp_config(project_id: str, cfg: dict) -> None:
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    p = _config_path(project_id)
    p.write_text(json.dumps(cfg, indent=2))
    p.chmod(0o600)


def delete_whatsapp_config(project_id: str) -> None:
    p = _config_path(project_id)
    if p.exists():
        p.unlink()


def list_whatsapp_projects() -> list[str]:
    """Gibt alle Projekt-IDs zurück die eine WhatsApp-Config haben."""
    if not TOKEN_DIR.exists():
        return []
    out: list[str] = []
    for f in TOKEN_DIR.iterdir():
        if f.is_file() and f.name.endswith("_whatsapp.json"):
            out.append(f.name[: -len("_whatsapp.json")])
    return sorted(out)


# ── Bridge-Kommunikation ─────────────────────────────────────────────────────

async def bridge_start_session(session_id: str) -> dict:
    """Session in der Bridge starten oder bestehende abrufen.

    session_id = project_id (= Bridge-Session-ID).
    """
    import httpx
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(f"{BRIDGE_URL}/sessions/{session_id}/start")
        r.raise_for_status()
        return r.json()


async def bridge_get_status(session_id: str) -> dict:
    """Session-Status und ggf. QR-Code abrufen."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{BRIDGE_URL}/sessions/{session_id}/status")
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"status": "bridge_unavailable", "error": str(e), "qr": None, "phone": None}


async def bridge_disconnect(session_id: str) -> dict:
    """Session trennen und Auth-Daten löschen."""
    import httpx
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.delete(f"{BRIDGE_URL}/sessions/{session_id}")
            r.raise_for_status()
            return r.json()
    except Exception as e:
        return {"disconnected": False, "error": str(e)}


async def bridge_send(session_id: str, to: str, message: str) -> dict:
    """Nachricht über WhatsApp senden."""
    import httpx
    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.post(
            f"{BRIDGE_URL}/sessions/{session_id}/send",
            json={"to": to, "message": message},
        )
        r.raise_for_status()
        return r.json()


async def bridge_send_voice(session_id: str, to: str, audio_b64: str) -> dict:
    """Voice-Note (OGG/Opus) über WhatsApp senden."""
    import httpx
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            f"{BRIDGE_URL}/sessions/{session_id}/send-voice",
            json={"to": to, "audio_data": audio_b64},
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

async def setup_whatsapp_sessions(*, logger_) -> None:
    """WhatsApp-Sessions für alle Projekte mit aktiver Config beim Core-Start wiederherstellen.

    Iteriert über alle {project_id}_whatsapp.json-Dateien in agent_tokens_dir
    und startet für jedes Projekt mit enabled=True eine Bridge-Session.
    """
    if not await bridge_health():
        logger_.info("WhatsApp Bridge nicht erreichbar — Sessions werden nicht wiederhergestellt")
        return

    started = 0
    for project_id in list_whatsapp_projects():
        cfg = load_whatsapp_config(project_id)
        if not cfg or not cfg.get("enabled"):
            continue
        try:
            result = await bridge_start_session(project_id)
            logger_.info(
                "WhatsApp-Session für Projekt '%s' gestartet: %s",
                project_id, result.get("status"),
            )
            started += 1
        except Exception as e:
            logger_.warning("WhatsApp-Session für Projekt '%s' nicht gestartet: %s", project_id, e)
    if started:
        logger_.info("WhatsApp-Startup: %d Projekt-Sessions wiederhergestellt", started)
