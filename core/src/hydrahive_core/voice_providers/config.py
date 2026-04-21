"""VoiceConfigLayer — Provider-Wahl (global) + User-Stimm-Preferences (SQLite).

Schema:
    voice_preferences(username TEXT, provider_type TEXT, provider_id TEXT,
                      voice_id TEXT, updated_at TEXT, PRIMARY KEY (username, provider_type))

Provider-Wahl global: live_config.json key "voice.tts_provider" / "voice.stt_provider".
Wenn nicht gesetzt, fällt auf den ersten registrierten Provider zurück.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
from pathlib import Path

from ..settings import settings
from . import registry
from .base import STTProvider, TTSProvider

logger = logging.getLogger(__name__)

DB_PATH = settings.log_dir / "voice.db"
CONFIG_FILE = settings.voice_config


class VoiceConfigLayer:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._db: sqlite3.Connection | None = None

    def _ensure_db(self) -> sqlite3.Connection:
        if self._db is not None:
            return self._db
        with self._lock:
            if self._db is not None:
                return self._db
            DB_PATH.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
            conn.row_factory = sqlite3.Row
            conn.execute("""
                CREATE TABLE IF NOT EXISTS voice_preferences (
                    username TEXT NOT NULL,
                    provider_type TEXT NOT NULL CHECK(provider_type IN ('stt','tts')),
                    provider_id TEXT NOT NULL,
                    voice_id TEXT,
                    updated_at TEXT DEFAULT (datetime('now')),
                    PRIMARY KEY (username, provider_type)
                )
            """)
            conn.commit()
            self._db = conn
            logger.info("VoiceConfigLayer DB bereit: %s", DB_PATH)
            return conn

    # ── Global Provider-Wahl aus voice.json ────────────────────────────

    def _load_global(self) -> dict:
        if not CONFIG_FILE.exists():
            return {}
        try:
            raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
        v = raw.get("voice") if isinstance(raw, dict) else None
        return v if isinstance(v, dict) else {}

    def _save_global(self, patch: dict) -> None:
        current: dict = {}
        if CONFIG_FILE.exists():
            try:
                current = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
                if not isinstance(current, dict):
                    current = {}
            except Exception:
                current = {}
        voice = current.get("voice") if isinstance(current.get("voice"), dict) else {}
        voice.update(patch)
        current["voice"] = voice
        CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
        CONFIG_FILE.write_text(json.dumps(current, indent=2, ensure_ascii=False), encoding="utf-8")

    def get_global_provider_id(self, provider_type: str) -> str | None:
        g = self._load_global()
        key = "tts_provider" if provider_type == "tts" else "stt_provider"
        val = g.get(key)
        return val if isinstance(val, str) and val else None

    def set_global_provider(self, provider_type: str, provider_id: str) -> None:
        if provider_type not in ("stt", "tts"):
            raise ValueError("provider_type muss 'stt' oder 'tts' sein")
        if provider_type == "tts":
            if provider_id not in registry.list_tts_providers():
                raise KeyError(f"TTS-Provider nicht registriert: {provider_id}")
            self._save_global({"tts_provider": provider_id})
        else:
            if provider_id not in registry.list_stt_providers():
                raise KeyError(f"STT-Provider nicht registriert: {provider_id}")
            self._save_global({"stt_provider": provider_id})

    # ── Provider-Lookup ───────────────────────────────────────────────

    def get_tts_provider_for_user(self, username: str) -> TTSProvider:
        pid = self._user_provider(username, "tts")
        if pid and pid in registry.list_tts_providers():
            return registry.get_tts(pid)
        pid = self.get_global_provider_id("tts")
        if pid and pid in registry.list_tts_providers():
            return registry.get_tts(pid)
        default = registry.get_default_tts()
        if default is None:
            raise RuntimeError("Kein TTS-Provider registriert")
        return default

    def get_stt_provider_for_user(self, username: str) -> STTProvider:
        pid = self._user_provider(username, "stt")
        if pid and pid in registry.list_stt_providers():
            return registry.get_stt(pid)
        pid = self.get_global_provider_id("stt")
        if pid and pid in registry.list_stt_providers():
            return registry.get_stt(pid)
        default = registry.get_default_stt()
        if default is None:
            raise RuntimeError("Kein STT-Provider registriert")
        return default

    def get_voice_for_user(self, username: str, provider_id: str) -> str | None:
        conn = self._ensure_db()
        row = conn.execute(
            "SELECT voice_id FROM voice_preferences WHERE username=? AND provider_id=? LIMIT 1",
            (username, provider_id),
        ).fetchone()
        if row and row["voice_id"]:
            return row["voice_id"]
        return None

    # ── User-Preferences ──────────────────────────────────────────────

    def get_user_preferences(self, username: str) -> dict:
        conn = self._ensure_db()
        rows = conn.execute(
            "SELECT provider_type, provider_id, voice_id FROM voice_preferences WHERE username=?",
            (username,),
        ).fetchall()
        out: dict = {"stt_provider": None, "stt_voice": None, "tts_provider": None, "tts_voice": None}
        for r in rows:
            t = r["provider_type"]
            out[f"{t}_provider"] = r["provider_id"]
            out[f"{t}_voice"] = r["voice_id"]
        return out

    def set_user_preference(
        self,
        username: str,
        provider_type: str,
        provider_id: str,
        voice_id: str | None = None,
    ) -> None:
        if provider_type not in ("stt", "tts"):
            raise ValueError("provider_type muss 'stt' oder 'tts' sein")
        if provider_type == "tts":
            if provider_id not in registry.list_tts_providers():
                raise KeyError(f"TTS-Provider nicht registriert: {provider_id}")
        else:
            if provider_id not in registry.list_stt_providers():
                raise KeyError(f"STT-Provider nicht registriert: {provider_id}")
        conn = self._ensure_db()
        with self._lock:
            conn.execute(
                """
                INSERT INTO voice_preferences (username, provider_type, provider_id, voice_id, updated_at)
                VALUES (?, ?, ?, ?, datetime('now'))
                ON CONFLICT(username, provider_type) DO UPDATE SET
                    provider_id=excluded.provider_id,
                    voice_id=excluded.voice_id,
                    updated_at=excluded.updated_at
                """,
                (username, provider_type, provider_id, voice_id),
            )
            conn.commit()

    def _user_provider(self, username: str, provider_type: str) -> str | None:
        conn = self._ensure_db()
        row = conn.execute(
            "SELECT provider_id FROM voice_preferences WHERE username=? AND provider_type=? LIMIT 1",
            (username, provider_type),
        ).fetchone()
        return row["provider_id"] if row else None


voice_config = VoiceConfigLayer()
