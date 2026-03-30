"""
memory_decay.py — Ebbinghaus Forgetting Curve für HydraHive Memory (#93)

Implementiert importance-gewichtetes, selbstbereinigendes Memory:
- Chunks verfallen mathematisch über Zeit (Ebbinghaus-Kurve)
- Häufig abgerufene Chunks werden verstärkt (recall_count-Boost)
- Widersprüchliche neue Memories ersetzen alte automatisch
- Chunks unter Stärke-Schwelle 0.05 werden automatisch gelöscht
"""
from __future__ import annotations

import math
import re
import sqlite3
import time
import logging
from typing import Literal

logger = logging.getLogger(__name__)

# ── Decay-Konfiguration ────────────────────────────────────────────────────────

DECAY_RATES: dict[str, float] = {
    "strategy":   0.10,   # ~38 Tage Überlebensdauer (was funktioniert hat)
    "fact":       0.16,   # ~24 Tage (Präferenzen, Identität, Fakten)
    "assumption": 0.20,   # ~19 Tage (inferierter Kontext)
    "failure":    0.35,   # ~11 Tage (Fehler — veralten schnell)
}
VALID_CATEGORIES: frozenset[str] = frozenset(DECAY_RATES.keys())
PRUNE_THRESHOLD:  float          = 0.05
DEFAULT_IMPORTANCE: float        = 0.5
DEFAULT_CATEGORY:   str          = "fact"

DedupeAction = Literal["reinforce", "replace", "merge", "new"]

# ── Schema ────────────────────────────────────────────────────────────────────

DECAY_SCHEMA_SQL = """
    CREATE TABLE IF NOT EXISTS chunk_meta (
        chunk_id      INTEGER PRIMARY KEY REFERENCES chunks(id) ON DELETE CASCADE,
        importance    REAL    NOT NULL DEFAULT 0.5,
        category      TEXT    NOT NULL DEFAULT 'fact',
        recall_count  INTEGER NOT NULL DEFAULT 0,
        created_at    REAL    NOT NULL,
        last_accessed REAL    NOT NULL,
        strength      REAL    NOT NULL DEFAULT 1.0
    );
    CREATE INDEX IF NOT EXISTS idx_chunk_meta_strength ON chunk_meta(strength);

    CREATE TABLE IF NOT EXISTS file_meta (
        source     TEXT PRIMARY KEY,
        importance REAL NOT NULL DEFAULT 0.5,
        category   TEXT NOT NULL DEFAULT 'fact'
    );
"""


def apply_decay_schema(conn: sqlite3.Connection) -> None:
    """Schema-Migration — idempotent, safe bei bestehenden DBs."""
    conn.executescript(DECAY_SCHEMA_SQL)


# ── Decay-Berechnung ──────────────────────────────────────────────────────────

def compute_strength(
    importance: float,
    category: str,
    recall_count: int,
    created_at: float,
    now: float | None = None,
) -> float:
    """
    Ebbinghaus-Stärke eines Memory-Chunks.

    strength = importance × e^(-λ_eff × Δt_days) × (1 + recall_count × 0.2)

    λ_eff = base_λ × (1 - importance × 0.8)
    → hohe Wichtigkeit → langsamer Verfall
    → häufiger Abruf → Verstärkung
    """
    if now is None:
        now = time.time()
    days = max(0.0, (now - created_at) / 86_400.0)
    base_lambda = DECAY_RATES.get(category, DECAY_RATES[DEFAULT_CATEGORY])
    effective_lambda = base_lambda * (1.0 - min(importance, 1.0) * 0.8)
    raw = importance * math.exp(-effective_lambda * days) * (1.0 + recall_count * 0.2)
    return max(0.0, min(2.0, raw))  # Obergrenze 2.0 erlaubt verstärkte Chunks > 1.0


# ── DB-Operationen ────────────────────────────────────────────────────────────

def recompute_all_strengths(conn: sqlite3.Connection) -> int:
    """
    Aktualisiert die strength-Spalte für alle chunk_meta-Zeilen.
    Gibt Anzahl aktualisierter Einträge zurück.
    """
    rows = conn.execute(
        "SELECT chunk_id, importance, category, recall_count, created_at FROM chunk_meta"
    ).fetchall()
    now = time.time()
    updated = 0
    for chunk_id, importance, category, recall_count, created_at in rows:
        s = compute_strength(importance, category, recall_count, created_at, now)
        conn.execute(
            "UPDATE chunk_meta SET strength = ? WHERE chunk_id = ?", (s, chunk_id)
        )
        updated += 1
    return updated


def prune_weak_chunks(
    conn: sqlite3.Connection,
    threshold: float = PRUNE_THRESHOLD,
) -> list[int]:
    """
    Löscht Chunks mit strength < threshold.
    ON DELETE CASCADE entfernt chunk_meta automatisch.
    Gibt Liste der gelöschten chunk_ids zurück.
    """
    weak = conn.execute(
        "SELECT chunk_id FROM chunk_meta WHERE strength < ?", (threshold,)
    ).fetchall()
    ids = [r[0] for r in weak]
    if not ids:
        return []

    placeholders = ",".join("?" * len(ids))
    # FTS5-Einträge zuerst entfernen
    conn.execute(f"DELETE FROM chunks_fts WHERE rowid IN ({placeholders})", ids)
    # Dann chunks — cascade löscht chunk_meta
    conn.execute(f"DELETE FROM chunks WHERE id IN ({placeholders})", ids)
    logger.debug("memory_decay: %d schwache Chunks gelöscht (strength < %.2f)", len(ids), threshold)
    return ids


def touch_recall(conn: sqlite3.Connection, chunk_ids: list[int]) -> None:
    """recall_count++ und last_accessed = now() für gegebene Chunk-IDs."""
    if not chunk_ids:
        return
    now = time.time()
    placeholders = ",".join("?" * len(chunk_ids))
    conn.execute(
        f"UPDATE chunk_meta SET recall_count = recall_count + 1, last_accessed = ? "
        f"WHERE chunk_id IN ({placeholders})",
        [now, *chunk_ids],
    )


def get_file_meta(
    conn: sqlite3.Connection, source: str
) -> tuple[float, str]:
    """Liest importance und category für eine Datei (source = path.stem)."""
    row = conn.execute(
        "SELECT importance, category FROM file_meta WHERE source = ?", (source,)
    ).fetchone()
    return (row[0], row[1]) if row else (DEFAULT_IMPORTANCE, DEFAULT_CATEGORY)


def set_file_meta(
    conn: sqlite3.Connection, source: str, importance: float, category: str
) -> None:
    """Speichert importance und category für eine Datei."""
    conn.execute(
        "INSERT OR REPLACE INTO file_meta (source, importance, category) VALUES (?,?,?)",
        (source, importance, category),
    )


# ── Migration bestehender Chunks ──────────────────────────────────────────────

def needs_decay_migration(conn: sqlite3.Connection) -> bool:
    """True wenn chunks vorhanden sind aber keine chunk_meta-Einträge existieren."""
    chunk_count = conn.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
    meta_count  = conn.execute("SELECT COUNT(*) FROM chunk_meta").fetchone()[0]
    return chunk_count > 0 and meta_count == 0


def migrate_existing_chunks(conn: sqlite3.Connection, agents_dir_mtime: float | None = None) -> int:
    """
    Trägt alle chunks ohne chunk_meta-Eintrag mit Default-Werten ein.
    created_at = now() (konservativ — echtes Datum unbekannt).
    Gibt Anzahl migrierter Einträge zurück.
    """
    now = time.time()
    result = conn.execute("""
        INSERT OR IGNORE INTO chunk_meta
            (chunk_id, importance, category, recall_count, created_at, last_accessed, strength)
        SELECT id, 0.5, 'fact', 0, ?, ?, 0.5
        FROM chunks
        WHERE id NOT IN (SELECT chunk_id FROM chunk_meta)
    """, (now, now))
    count = result.rowcount
    if count:
        logger.info("memory_decay: %d bestehende Chunks migriert (importance=0.5, category=fact)", count)
    return count


# ── Semantische Deduplizierung ────────────────────────────────────────────────

def dedup_decision(
    similarity: float,
    is_contradiction: bool = False,
) -> DedupeAction:
    """
    Entscheidet wie ein neuer Chunk mit einem ähnlichen bestehenden umgehen soll.

    Returns:
        'reinforce' — nur recall_count erhöhen, nicht neu speichern
        'replace'   — bestehenden Chunk ersetzen (Widerspruch erkannt)
        'merge'     — Inhalte zusammenführen
        'new'       — neuer unabhängiger Eintrag
    """
    if similarity >= 0.85:
        return "reinforce"
    if 0.65 <= similarity < 0.85:
        return "replace" if is_contradiction else "merge"
    return "new"


# Negations-Wörter
_NEG_WORDS = frozenset(["nicht", "kein", "keine", "keiner", "keinem", "keinen",
                         "nie", "niemals", "never", "not", "no", "cannot", "can't"])


def detect_contradiction(text_a: str, text_b: str) -> bool:
    """
    Heuristik: Prüft ob text_b einem Aspekt von text_a widerspricht.
    Keine LLM-Calls — wortbasiert, deterministisch, schnell.

    Strategie: wenn einer der Texte Negations-Wörter enthält und der andere nicht,
    und beide Texte signifikante gemeinsame Wörter teilen, gilt das als Widerspruch.
    """
    a_lower = text_a.lower()
    b_lower = text_b.lower()

    a_words = set(re.findall(r"[a-zA-ZäöüÄÖÜß]{4,}", a_lower))
    b_words = set(re.findall(r"[a-zA-ZäöüÄÖÜß]{4,}", b_lower))

    a_has_neg = bool(a_words & _NEG_WORDS)
    b_has_neg = bool(b_words & _NEG_WORDS)

    # Nur wenn genau einer der Texte negiert ist
    if a_has_neg == b_has_neg:
        return False

    # Gemeinsame signifikante Wörter (ohne Negations-Wörter selbst)
    stopwords = _NEG_WORDS | {"sein", "sind", "wird", "wird", "haben", "dass",
                               "this", "that", "with", "have", "been", "they",
                               "server", "agent"}  # zu generisch
    significant_a = a_words - _NEG_WORDS - stopwords
    significant_b = b_words - _NEG_WORDS - stopwords

    shared = significant_a & significant_b
    return len(shared) >= 1
