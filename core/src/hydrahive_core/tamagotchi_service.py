"""
tamagotchi_service.py — Persistenter Companion-Zustand

Der Floating Companion ("Tamagotchi") hat pro User einen Zustand mit
Happy / Hunger / Energy, der über Zeit verfällt und durch Interaktion
(streicheln, füttern) wieder aufgebaut wird. Zustand persistiert in
SQLite, Decay wird beim Read gelazy angewendet.

Benutzung:
    from .tamagotchi_service import tamagotchi_service
    state = tamagotchi_service.get_state("till")
    state = tamagotchi_service.interact("till", "pet")
"""
from __future__ import annotations

import logging
import sqlite3
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

DB_PATH = Path("/var/log/hydrahive/tamagotchi.db")

# Decay-Raten pro Stunde (awake)
HAPPY_DECAY        = 4.0     # -4 happy/h
HUNGER_GROWTH      = 8.0     # +8 hunger/h (0 = satt, 100 = verhungert)
ENERGY_DECAY       = 3.0     # -3 energy/h awake
ENERGY_REGEN_SLEEP = 15.0    # +15 energy/h schlafend

# Interaktions-Effekte
PET_HAPPY_GAIN   = 10.0
PET_ENERGY_COST  = 1.0
FEED_HUNGER_DROP = 30.0
FEED_HAPPY_GAIN  = 5.0


def _clamp(v: float, lo: float = 0.0, hi: float = 100.0) -> float:
    return max(lo, min(hi, v))


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _iso_to_dt(s: str) -> datetime:
    return datetime.fromisoformat(s)


@dataclass
class TamagotchiState:
    user:               str
    happy:              float
    hunger:             float
    energy:             float
    is_sleeping:        bool
    birth_at:           str
    last_update:        str
    interactions_total: int
    pet_count:          int
    feed_count:         int

    def age_days(self) -> int:
        try:
            delta = datetime.now(timezone.utc) - _iso_to_dt(self.birth_at)
            return max(0, delta.days)
        except Exception:
            return 0


def derive_mood(s: TamagotchiState) -> str:
    """Frontend-Mood aus State ableiten (kompatibel mit 7 bestehenden Moods)."""
    if s.is_sleeping or s.energy < 15:
        return "sleep"
    if s.hunger > 80:
        return "shock"    # hangry
    if s.happy < 20:
        return "sad"
    if s.happy > 80 and s.hunger < 30 and s.energy > 60:
        return "love"
    if s.happy > 55:
        return "happy"
    return "idle"


def status_hint(s: TamagotchiState) -> str:
    """Kurzer Zustandshinweis fürs LLM-System-Prompt (max 1 Satz)."""
    bits: list[str] = []
    if s.is_sleeping:
        return "You are asleep and dreaming peacefully."
    if s.energy < 20:
        bits.append("very tired")
    elif s.energy < 40:
        bits.append("a bit sleepy")
    if s.hunger > 75:
        bits.append("extremely hungry")
    elif s.hunger > 50:
        bits.append("peckish")
    if s.happy < 25:
        bits.append("feeling down")
    elif s.happy > 80:
        bits.append("thrilled")
    if not bits:
        bits.append("content")
    return f"Your current mood: {', '.join(bits)}."


class TamagotchiService:
    def __init__(self) -> None:
        self._db: sqlite3.Connection | None = None

    def start(self) -> None:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(DB_PATH), check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS tamagotchi_state (
                user                TEXT PRIMARY KEY,
                happy               REAL NOT NULL,
                hunger              REAL NOT NULL,
                energy              REAL NOT NULL,
                is_sleeping         INTEGER NOT NULL DEFAULT 0,
                birth_at            TEXT NOT NULL,
                last_update         TEXT NOT NULL,
                interactions_total  INTEGER NOT NULL DEFAULT 0,
                pet_count           INTEGER NOT NULL DEFAULT 0,
                feed_count          INTEGER NOT NULL DEFAULT 0
            )
        """)
        self._db.commit()
        logger.info("TamagotchiService gestartet (DB: %s)", DB_PATH)

    def stop(self) -> None:
        if self._db:
            self._db.close()
            self._db = None

    def _load(self, user: str) -> TamagotchiState:
        assert self._db is not None
        row = self._db.execute(
            "SELECT * FROM tamagotchi_state WHERE user = ?", (user,)
        ).fetchone()
        if row is None:
            now = _now_iso()
            state = TamagotchiState(
                user=user, happy=80.0, hunger=20.0, energy=80.0,
                is_sleeping=False, birth_at=now, last_update=now,
                interactions_total=0, pet_count=0, feed_count=0,
            )
            self._save(state)
            return state
        return TamagotchiState(
            user=row["user"],
            happy=float(row["happy"]),
            hunger=float(row["hunger"]),
            energy=float(row["energy"]),
            is_sleeping=bool(row["is_sleeping"]),
            birth_at=row["birth_at"],
            last_update=row["last_update"],
            interactions_total=int(row["interactions_total"]),
            pet_count=int(row["pet_count"]),
            feed_count=int(row["feed_count"]),
        )

    def _save(self, s: TamagotchiState) -> None:
        assert self._db is not None
        self._db.execute("""
            INSERT INTO tamagotchi_state
            (user, happy, hunger, energy, is_sleeping, birth_at, last_update,
             interactions_total, pet_count, feed_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user) DO UPDATE SET
              happy=excluded.happy, hunger=excluded.hunger, energy=excluded.energy,
              is_sleeping=excluded.is_sleeping, last_update=excluded.last_update,
              interactions_total=excluded.interactions_total,
              pet_count=excluded.pet_count, feed_count=excluded.feed_count
        """, (
            s.user, s.happy, s.hunger, s.energy, int(s.is_sleeping),
            s.birth_at, s.last_update,
            s.interactions_total, s.pet_count, s.feed_count,
        ))
        self._db.commit()

    def _apply_decay(self, s: TamagotchiState) -> TamagotchiState:
        try:
            last = _iso_to_dt(s.last_update)
        except Exception:
            s.last_update = _now_iso()
            return s
        now = datetime.now(timezone.utc)
        hours = max(0.0, (now - last).total_seconds() / 3600.0)
        if hours <= 0:
            return s
        if s.is_sleeping:
            s.energy = _clamp(s.energy + ENERGY_REGEN_SLEEP * hours)
            # Während Schlaf kein Happy-Decay, aber Hunger läuft halb weiter
            s.hunger = _clamp(s.hunger + HUNGER_GROWTH * 0.5 * hours)
            # Automatisch aufwachen wenn voll ausgeschlafen
            if s.energy >= 95:
                s.is_sleeping = False
        else:
            s.happy  = _clamp(s.happy  - HAPPY_DECAY   * hours)
            s.hunger = _clamp(s.hunger + HUNGER_GROWTH * hours)
            s.energy = _clamp(s.energy - ENERGY_DECAY  * hours)
        s.last_update = _now_iso()
        return s

    # ── Public API ──────────────────────────────────────────────

    def get_state(self, user: str) -> TamagotchiState:
        s = self._load(user)
        s = self._apply_decay(s)
        self._save(s)
        return s

    def interact(self, user: str, kind: str) -> TamagotchiState:
        s = self.get_state(user)  # fresh state with decay applied
        kind = (kind or "").lower().strip()
        if kind == "pet":
            s.happy  = _clamp(s.happy + PET_HAPPY_GAIN)
            s.energy = _clamp(s.energy - PET_ENERGY_COST)
            s.pet_count += 1
        elif kind == "feed":
            s.hunger = _clamp(s.hunger - FEED_HUNGER_DROP)
            s.happy  = _clamp(s.happy  + FEED_HAPPY_GAIN)
            s.feed_count += 1
        elif kind == "sleep":
            s.is_sleeping = True
        elif kind == "wake":
            s.is_sleeping = False
        else:
            raise ValueError(f"Unbekannte Interaktion: {kind}")
        s.interactions_total += 1
        s.last_update = _now_iso()
        self._save(s)
        return s

    def snapshot_dict(self, user: str) -> dict:
        s = self.get_state(user)
        d = asdict(s)
        d["mood"] = derive_mood(s)
        d["status_hint"] = status_hint(s)
        d["age_days"] = s.age_days()
        return d


tamagotchi_service = TamagotchiService()
