"""
mail_watcher.py — IMAP-Polling für Butler E-Mail Trigger (#67)

Pollt ein IMAP-Postfach und feuert ButlerEvents für neue Mails.
Konfiguration aus /etc/hydrahive/kas.json (imap_host, imap_port, login, password).
Gesehene Message-IDs in /etc/hydrahive/mail_seen_ids.json.
"""
from __future__ import annotations

import asyncio
import email
import imaplib
import json
import logging
from email.header import decode_header, make_header
from pathlib import Path

logger = logging.getLogger(__name__)

_KAS_PATH      = Path("/etc/hydrahive/kas.json")
_SEEN_IDS_PATH = Path("/etc/hydrahive/mail_seen_ids.json")
_MAX_SEEN      = 2000  # Maximale Anzahl gespeicherter IDs


def _load_seen_ids() -> set[str]:
    if _SEEN_IDS_PATH.exists():
        try:
            return set(json.loads(_SEEN_IDS_PATH.read_text(encoding="utf-8")))
        except Exception:
            pass
    return set()


def _save_seen_ids(ids: set[str]) -> None:
    try:
        _SEEN_IDS_PATH.write_text(
            json.dumps(sorted(ids)[-_MAX_SEEN:], ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        logger.warning("mail_watcher: Fehler beim Speichern der Seen-IDs: %s", e)


def _decode_header_str(raw: str | None) -> str:
    if not raw:
        return ""
    try:
        return str(make_header(decode_header(raw)))
    except Exception:
        return raw or ""


def _poll_imap(cfg: dict, folder: str = "INBOX") -> list[dict]:
    """Synchron IMAP-Polling. Gibt Liste neuer Mails zurück."""
    host     = cfg.get("imap_host", "")
    port     = int(cfg.get("imap_port", 993))
    login    = cfg.get("login", "") or cfg.get("imap_login", "")
    password = cfg.get("password", "") or cfg.get("imap_password", "")

    if not host or not login or not password:
        logger.debug("mail_watcher: IMAP nicht konfiguriert (host/login/password fehlt)")
        return []

    seen_ids = _load_seen_ids()
    new_mails: list[dict] = []

    try:
        conn = imaplib.IMAP4_SSL(host, port)
        conn.login(login, password)
        conn.select(folder, readonly=True)

        _, data = conn.search(None, "UNSEEN")
        if not data or not data[0]:
            conn.logout()
            return []

        msg_ids = data[0].split()
        for mid in msg_ids[-50:]:  # max. 50 pro Durchlauf
            _, msg_data = conn.fetch(mid, "(RFC822)")
            if not msg_data or not msg_data[0]:
                continue
            raw = msg_data[0][1]
            msg = email.message_from_bytes(raw)

            msg_id = msg.get("Message-ID", "").strip()
            if not msg_id:
                msg_id = f"noId-{mid.decode()}"
            if msg_id in seen_ids:
                continue

            seen_ids.add(msg_id)

            # Body extrahieren
            body_plain = ""
            if msg.is_multipart():
                for part in msg.walk():
                    if part.get_content_type() == "text/plain":
                        try:
                            body_plain = part.get_payload(decode=True).decode(
                                part.get_content_charset("utf-8"), errors="replace"
                            )
                            break
                        except Exception:
                            pass
            else:
                try:
                    body_plain = msg.get_payload(decode=True).decode(
                        msg.get_content_charset("utf-8"), errors="replace"
                    )
                except Exception:
                    pass

            new_mails.append({
                "message_id": msg_id,
                "from":       _decode_header_str(msg.get("From", "")),
                "to":         _decode_header_str(msg.get("To", "")),
                "subject":    _decode_header_str(msg.get("Subject", "")),
                "date":       msg.get("Date", ""),
                "body_plain": body_plain[:4000],  # Budget-Limit
            })

        conn.logout()
        _save_seen_ids(seen_ids)

    except imaplib.IMAP4.error as e:
        logger.warning("mail_watcher IMAP-Fehler: %s", e)
    except Exception as e:
        logger.warning("mail_watcher Fehler: %s", e)

    return new_mails


async def _run_mail_watcher(interval: int = 60) -> None:
    """Async Polling-Loop. Läuft als Background-Task."""
    from .butler_executor import ButlerEvent, check_flows, execute_generic_actions

    logger.info("mail_watcher gestartet (Intervall: %ds)", interval)
    while True:
        try:
            await asyncio.sleep(interval)
            if not _KAS_PATH.exists():
                continue
            cfg = json.loads(_KAS_PATH.read_text(encoding="utf-8"))
            folder = cfg.get("imap_folder", "INBOX")

            mails = await asyncio.to_thread(_poll_imap, cfg, folder)
            for mail in mails:
                logger.info("mail_watcher: Neue Mail von %s — %s", mail["from"], mail["subject"])
                event = ButlerEvent(
                    event_type   = "email",
                    channel      = "email",
                    contact_id   = mail["from"],
                    contact_name = mail["from"],
                    message_text = mail["body_plain"],
                    extra        = mail,
                )
                actions = await check_flows(event, owner=None)
                asyncio.create_task(execute_generic_actions(actions, event))

        except asyncio.CancelledError:
            logger.info("mail_watcher beendet")
            return
        except Exception as e:
            logger.warning("mail_watcher Loop-Fehler: %s", e)


def start_mail_watcher(interval: int = 60) -> asyncio.Task:
    """Startet den Mail-Watcher als asyncio-Task."""
    return asyncio.create_task(_run_mail_watcher(interval))
