"""
token_blacklist.py — persistente JWT-Blacklist (#809)

Der JWT-Server muss nach einem Logout bzw. einem Force-Revoke sicherstellen,
dass alte Tokens auch nach einem Core-Restart / Worker-Wechsel ungültig
bleiben. Die bisherige in-memory dict[str, float]-Lösung ging bei jedem
Neustart verloren — damit wirkte Logout nicht und kompromittierte Tokens
blieben gültig.

Hier: SQLite-Backend (WAL, file-based). Kein neuer Runtime-Dep,
prozessübergreifend nutzbar. API ist bewusst schlank gehalten:
    .add(jti, exp)
    .is_revoked(jti)
    .cleanup_expired(now)
plus dict-ähnliche Bequemlichkeits-Operationen für Bestandscode
(``jti in blacklist``).
"""
from __future__ import annotations

import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


class TokenBlacklist:
    """
    Thread-safe, persistente JWT-JTI-Blacklist.

    SQLite WAL-Mode → mehrere Prozesse können parallel lesen/schreiben.
    Einträge haben `exp` als Unix-Timestamp (Sekunden); abgelaufene
    Einträge werden per ``cleanup_expired`` entfernt.

    Bei Initialisierungs-Fehlern (z.B. Permission denied) fällt die
    Klasse automatisch auf in-memory Modus zurück und loggt eine
    WARNUNG — Server bleibt lauffähig, Revocation überlebt dann aber
    keinen Restart.
    """

    _SCHEMA = """
    CREATE TABLE IF NOT EXISTS revoked_tokens (
        jti  TEXT PRIMARY KEY,
        exp  REAL NOT NULL
    );
    CREATE INDEX IF NOT EXISTS idx_revoked_exp ON revoked_tokens(exp);
    """

    def __init__(self, db_path: Path | str | None) -> None:
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        self._memory: dict[str, float] = {}

        if db_path is None:
            logger.warning("TokenBlacklist: kein db_path gesetzt — in-memory Modus")
            return

        try:
            p = Path(db_path)
            p.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(p), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._conn.executescript(self._SCHEMA)
            self._conn.commit()
            logger.info("TokenBlacklist: SQLite-Backend aktiv (%s)", p)
        except (sqlite3.Error, OSError) as e:
            logger.warning(
                "TokenBlacklist: SQLite-Init fehlgeschlagen (%s) — Fallback "
                "auf in-memory. Revocation ueberlebt keinen Restart.",
                e,
            )
            self._conn = None

    # ------------------------------------------------------------------- API

    def add(self, jti: str, exp: float) -> None:
        """Revokiert einen JTI bis zum Ablauf-Timestamp ``exp``."""
        if not jti:
            return
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.execute(
                        "INSERT OR REPLACE INTO revoked_tokens (jti, exp) VALUES (?, ?)",
                        (jti, float(exp)),
                    )
                    self._conn.commit()
                    return
                except sqlite3.Error as e:
                    logger.warning("TokenBlacklist.add(%s) SQLite-Fehler: %s", jti, e)
            self._memory[jti] = float(exp)

    def is_revoked(self, jti: str) -> bool:
        """True wenn ``jti`` aktuell revokiert ist (und noch nicht abgelaufen)."""
        if not jti:
            return False
        with self._lock:
            if self._conn is not None:
                try:
                    cur = self._conn.execute(
                        "SELECT exp FROM revoked_tokens WHERE jti = ?", (jti,)
                    )
                    row = cur.fetchone()
                    if row is None:
                        return False
                    # Selbstheilung: schon abgelaufen → leise löschen
                    if row[0] <= time.time():
                        self._conn.execute(
                            "DELETE FROM revoked_tokens WHERE jti = ?", (jti,)
                        )
                        self._conn.commit()
                        return False
                    return True
                except sqlite3.Error as e:
                    logger.warning("TokenBlacklist.is_revoked(%s) SQLite-Fehler: %s", jti, e)
            return jti in self._memory and self._memory[jti] > time.time()

    def cleanup_expired(self, now: float | None = None) -> int:
        """Entfernt alle Einträge mit ``exp < now``. Gibt Anzahl zurück."""
        ref = float(now if now is not None else time.time())
        with self._lock:
            if self._conn is not None:
                try:
                    cur = self._conn.execute(
                        "DELETE FROM revoked_tokens WHERE exp < ?", (ref,)
                    )
                    self._conn.commit()
                    return cur.rowcount
                except sqlite3.Error as e:
                    logger.warning("TokenBlacklist.cleanup_expired SQLite-Fehler: %s", e)
            removed = [jti for jti, exp in self._memory.items() if exp < ref]
            for jti in removed:
                self._memory.pop(jti, None)
            return len(removed)

    def close(self) -> None:
        with self._lock:
            if self._conn is not None:
                try:
                    self._conn.close()
                except sqlite3.Error:
                    pass
                self._conn = None

    # ------------------------------------------------------- dict-Compat API
    # Zwei Altcall-Sites erwarten dict-Verhalten (``jti in _token_blacklist``
    # und ``_token_blacklist[jti] = exp``). Wir exponieren genau diese zwei
    # Operationen, nicht mehr — keine .items()/.pop()/etc., damit neue
    # Aufrufer direkt die explizite API nutzen.

    def __contains__(self, jti: object) -> bool:
        return isinstance(jti, str) and self.is_revoked(jti)

    def __setitem__(self, jti: str, exp: float) -> None:
        self.add(jti, exp)
