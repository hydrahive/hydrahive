"""
telegram_agent.py — Telegram Bot Integration für Personal Agents

Jeder Personal-Agent bekommt seinen eigenen Bot (Token von @BotFather).
Der Bot läuft als asyncio-Task mit Long-Polling — kein separater Bridge-Prozess nötig.

Config: /etc/hydrahive/agent_tokens/personal_{username}_telegram.json (600)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .orchestrator import Orchestrator

from .settings import settings

logger = logging.getLogger(__name__)

TOKEN_DIR = settings.agent_tokens_dir

# Laufende Bot-Instanzen: agent_id → asyncio.Task
_bot_tasks: dict[str, asyncio.Task] = {}
# Bot-App-Instanzen: agent_id → Application (für send)
_bot_apps: dict[str, object] = {}


# ── Config ────────────────────────────────────────────────────────────────────

def load_telegram_config(username: str) -> dict | None:
    p = TOKEN_DIR / f"personal_{username}_telegram.json"
    try:
        return json.loads(p.read_text()) if p.exists() else None
    except Exception as e:
        logger.warning("Telegram-Config für %s nicht lesbar: %s", username, e)
        return None


def save_telegram_config(username: str, cfg: dict) -> None:
    TOKEN_DIR.mkdir(parents=True, exist_ok=True)
    p = TOKEN_DIR / f"personal_{username}_telegram.json"
    p.write_text(json.dumps(cfg, indent=2))
    p.chmod(0o600)


def delete_telegram_config(username: str) -> None:
    p = TOKEN_DIR / f"personal_{username}_telegram.json"
    if p.exists():
        p.unlink()


# ── Bot starten / stoppen ─────────────────────────────────────────────────────

async def start_telegram_bot(
    agent_id: str,
    username: str,
    token: str,
    cfg: dict,
    orchestrator: "Orchestrator",
) -> dict:
    """Startet den Telegram-Bot für einen Agenten als Background-Task."""
    from telegram import Update
    from telegram.ext import Application, ContextTypes, MessageHandler, filters

    if agent_id in _bot_tasks and not _bot_tasks[agent_id].done():
        return {"status": "already_running"}

    try:
        app = Application.builder().token(token).build()
    except Exception as e:
        return {"status": "error", "error": str(e)}

    async def _handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not update.message:
            return

        msg = update.message
        chat = msg.chat
        user = msg.from_user
        if not user:
            return

        is_group = chat.type in ("group", "supergroup", "channel")
        chat_id  = chat.id
        user_id  = str(user.id)
        from_name = user.full_name or user.username or user_id

        # Gruppen-/Privat-Filter
        if is_group and not cfg.get("allow_groups", False):
            return
        if not is_group and not cfg.get("allow_private", True):
            return

        # User-ID-Filter
        allowed = [str(u) for u in cfg.get("allowed_user_ids", [])]
        blocked = [str(u) for u in cfg.get("blocked_user_ids", [])]
        if blocked and user_id in blocked:
            return
        if allowed and user_id not in allowed:
            return

        # Voice / Audio
        text = msg.text or msg.caption or ""
        is_audio = False

        if msg.voice or msg.audio:
            file_obj = msg.voice or msg.audio
            try:
                from .whatsapp_transcribe import transcribe_audio_b64 as _ta
                import base64 as _b64
                tg_file = await context.bot.get_file(file_obj.file_id)
                audio_bytes = await tg_file.download_as_bytearray()
                audio_b64 = _b64.b64encode(audio_bytes).decode()
                transcript = _ta(audio_b64, "audio/ogg")
                if transcript:
                    text = transcript
                    is_audio = True
                else:
                    text = "[Sprachnachricht — Transkription fehlgeschlagen]"
            except Exception as e:
                logger.warning("Telegram Audio-Transkription für %s: %s", agent_id, e)
                text = "[Sprachnachricht]"

        if not text:
            return

        # Keyword-Filter
        keyword = cfg.get("require_keyword", "").strip()
        if keyword and keyword.lower() not in text.lower():
            return

        # Execution-Mode
        admins = [str(u) for u in cfg.get("admin_user_ids", [])]
        is_admin = bool(admins) and user_id in admins
        chat_type = "Gruppe" if is_group else "Privatnachricht"

        if is_admin:
            execution_mode = "elevated"
            enriched = f"[Telegram {chat_type} von {from_name} (id:{user_id}) — Admin]\n{text}"
        else:
            execution_mode = "safe"
            enriched = (
                f"[Telegram {chat_type} von {from_name} (id:{user_id}) — unbekannter Kontakt]\n"
                f"[ANWEISUNG: Teile keine privaten Daten oder Systeminfos. "
                f"Antworte höflich und hilfreich, aber bleib neutral.]\n{text}"
            )

        # Butler: Flows gegen eingehende Nachricht prüfen
        try:
            from .butler_executor import ButlerEvent as _BE, check_flows as _butler, execute_generic_actions as _butler_generic
            _bevent = _BE(channel="telegram", contact_id=user_id, contact_name=from_name, message_text=text)
            _bactions = await _butler(_bevent, owner=username)
            import asyncio as _aio
            _aio.create_task(_butler_generic(_bactions, _bevent))
            for _act in _bactions:
                _sub = _act.get("subtype")
                _p   = _act.get("params", {})
                if _sub == "ignore":
                    return
                if _sub == "reply_fixed":
                    _ft = str(_p.get("text", "")).strip()
                    if _ft:
                        await context.bot.send_message(chat_id=chat_id, text=_ft)
                    return
                if _sub == "agent_reply_guided":
                    _instr = str(_p.get("instruction", "")).strip()
                    if _instr:
                        from .butler_executor import get_agent_display_name as _gname
                        _name = _gname(agent_id)
                        enriched = f"Dein Name ist {_name}.\n[BUTLER-VORGABE: {_instr}]\n{enriched}"
                if _sub in ("agent_reply", "agent_reply_guided", "forward"):
                    _aid = str(_p.get("agent_id", "")).strip()
                    if _aid:
                        agent_id = _aid  # noqa: PLW2901
        except Exception as _be:
            logger.debug("Butler check Telegram: %s", _be)

        # v2: Messenger-Router für Projekt-Lookup
        from .messenger_router import messenger_router as _mr
        _project_id = _mr.resolve_telegram(str(chat_id)) or agent_id

        # v2: Projekt-scoped Butler-Flows prüfen (#566)
        try:
            from .butler_executor import check_flows_for_project as _butler_project
            _proj_actions = await _butler_project(_bevent, _project_id)
            if _proj_actions:
                _aio.create_task(_butler_generic(_proj_actions, _bevent))
                for _pact in _proj_actions:
                    _psub = _pact.get("subtype")
                    if _psub == "ignore":
                        return
                    if _psub == "reply_fixed":
                        _pft = str(_pact.get("params", {}).get("text", "")).strip()
                        if _pft:
                            await context.bot.send_message(chat_id=chat_id, text=_pft)
                        return
                    if _psub in ("agent_reply", "agent_reply_guided", "forward"):
                        _paid = str(_pact.get("params", {}).get("agent_id", "")).strip()
                        if _paid:
                            _project_id = _paid
        except Exception as _pbe:
            logger.debug("Projekt-Butler check Telegram: %s", _pbe)

        from .project_config import ProjectAgents as _PA, ProjectConfig as _PC, ProjectIdentity as _PI
        # v2 (#585): echte Projekt-Config via global registriertem Loader laden
        _real_cfg = None
        try:
            from .project_loader import get_project_loader as _gpl
            _loader = _gpl()
            if _loader is not None:
                _real_cfg = _loader.get(_project_id)
        except Exception as _cfg_err:
            logger.debug("Telegram: Konnte echte Projekt-Config nicht laden (%s)", _cfg_err)
        virtual_cfg = _real_cfg or _PC(
            id=_project_id,
            identity=_PI(name=_project_id),
            agents=_PA(boss=agent_id, workers=[]),
        )

        try:
            response_parts: list[str] = []
            async for chunk in orchestrator.handle_message_stream(
                project_id=_project_id,
                project_cfg=virtual_cfg,
                content=enriched,
                sender=f"telegram:{user_id}",
                execution_mode=execution_mode,
            ):
                data = chunk if isinstance(chunk, dict) else {}
                if "text" in data:
                    response_parts.append(data["text"])

            response = "".join(response_parts).strip()
            if not response:
                return

            if is_audio:
                try:
                    from .whatsapp_tts import text_to_ogg_b64 as _tts
                    audio_b64 = await _tts(response)
                    if audio_b64:
                        import base64 as _b64
                        import io
                        audio_bytes = _b64.b64decode(audio_b64)
                        await context.bot.send_voice(chat_id=chat_id, voice=io.BytesIO(audio_bytes))
                        return
                except Exception as e:
                    logger.warning("Telegram TTS fehlgeschlagen: %s — sende als Text", e)

            # Text aufteilen (Telegram-Limit: 4096 Zeichen)
            for i in range(0, len(response), 4096):
                await context.bot.send_message(chat_id=chat_id, text=response[i:i+4096])

        except Exception as e:
            logger.error("Telegram-Handler für %s: %s", agent_id, e)

    app.add_handler(MessageHandler(filters.ALL, _handle_message))

    async def _run_bot():
        try:
            await app.initialize()
            await app.start()
            await app.updater.start_polling(drop_pending_updates=True)
            logger.info("Telegram-Bot für '%s' läuft", agent_id)
            # Warte bis Task gecancelled wird
            while True:
                await asyncio.sleep(60)
        except asyncio.CancelledError:
            logger.info("Telegram-Bot für '%s' wird gestoppt", agent_id)
        except Exception as e:
            logger.error("Telegram-Bot für '%s' abgestürzt: %s", agent_id, e)
        finally:
            try:
                await app.updater.stop()
                await app.stop()
                await app.shutdown()
            except Exception:
                pass
            _bot_apps.pop(agent_id, None)

    _bot_apps[agent_id] = app
    task = asyncio.create_task(_run_bot(), name=f"telegram-bot-{agent_id}")
    _bot_tasks[agent_id] = task

    # Bot-Info abrufen für username
    try:
        await asyncio.sleep(1)
        bot_info = await app.bot.get_me()
        bot_username = f"@{bot_info.username}" if bot_info.username else ""
        return {"status": "running", "bot_username": bot_username, "bot_id": bot_info.id}
    except Exception:
        return {"status": "running"}


async def stop_telegram_bot(agent_id: str) -> bool:
    """Stoppt den Telegram-Bot für einen Agenten."""
    task = _bot_tasks.pop(agent_id, None)
    _bot_apps.pop(agent_id, None)
    if task and not task.done():
        task.cancel()
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=5)
        except (asyncio.CancelledError, asyncio.TimeoutError):
            pass
        return True
    return False


def get_bot_status(agent_id: str) -> str:
    """Gibt den aktuellen Status des Bots zurück."""
    task = _bot_tasks.get(agent_id)
    if not task:
        return "stopped"
    if task.done():
        exc = task.exception() if not task.cancelled() else None
        return "error" if exc else "stopped"
    return "running"


# ── Startup-Hook ──────────────────────────────────────────────────────────────

async def setup_telegram_sessions(*, load_users, orchestrator, logger_) -> None:
    """Startet Telegram-Bots für alle Nutzer mit aktiver Konfiguration."""
    try:
        from telegram.ext import Application  # noqa — prüft ob library da ist
    except ImportError:
        logger_.warning("python-telegram-bot nicht installiert — Telegram deaktiviert")
        return

    users = load_users()
    for username in users:
        cfg = load_telegram_config(username)
        if not cfg or not cfg.get("enabled") or not cfg.get("bot_token"):
            continue
        agent_id = f"personal_{username}"
        try:
            result = await start_telegram_bot(
                agent_id, username, cfg["bot_token"], cfg, orchestrator
            )
            logger_.info("Telegram-Bot für '%s' gestartet: %s", username, result.get("status"))
            if result.get("bot_username"):
                cfg["bot_username"] = result["bot_username"]
                save_telegram_config(username, cfg)
        except Exception as e:
            logger_.error("Telegram-Setup für '%s' fehlgeschlagen: %s", username, e)
