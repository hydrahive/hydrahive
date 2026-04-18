"""
memory_search.py — Hybrid Memory Search für HydraHive (#44, #93)

SQLite FTS5 (BM25) + optionale FAISS-Semantic-Suche.
Ab #93: Ebbinghaus Decay — Chunks werden importance-gewichtet und
selbstbereinigend. Stale Chunks fliegen automatisch raus.

Architektur:
- Pro Agent eine SQLite DB: /projects/{id}/memory_index.db (BM25 + Decay-Meta)
- Pro Agent ein FAISS-Index: /projects/{id}/memory_index.faiss (Semantic)
- Lazy re-indexing: Dateien werden nur bei mtime/size-Änderung neu indexiert
- Chunks: Split by markdown headers, max 1500 chars/Chunk
- Hybrid: BM25 top-k/2 + Semantic top-k/2, dedup, max k Ergebnisse
- Decay: final_score = hybrid_score × strength (Ebbinghaus-Stärke)
- Prune: Chunks mit strength < 0.05 werden bei update_index entfernt

Verwendung:
    from .memory_search import search_memory, update_index

    update_index(agent_dir)              # lazy, schnell wenn nichts geändert
    snippets = search_memory(agent_dir, user_text, k=6)
"""
from __future__ import annotations

import logging
import os
import re
import sqlite3
import time
from pathlib import Path

from .memory_decay import (
    apply_decay_schema,
    compute_strength,
    get_file_meta,
    migrate_existing_chunks,
    needs_decay_migration,
    prune_weak_chunks,
    recompute_all_strengths,
    touch_recall,
)
from .semantic_index import update_index as _update_semantic, search_index as _search_semantic

logger = logging.getLogger(__name__)

SKIP_FILES: frozenset[str] = frozenset({"learned-facts.md", "MEMORY.md", "INDEX.md"})
MAX_CHUNK_CHARS = 1500   # max chars pro Chunk beim Indexieren
SNIPPET_CHARS   = 700    # max chars pro Treffer im System-Prompt
# BM25 vs. Semantik-Gewichtung: 0.0 = nur Semantik, 1.0 = nur BM25
SEARCH_ALPHA: float = float(os.environ.get("HYDRAHIVE_SEARCH_ALPHA", "0.5"))


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

def _split_chunks(text: str) -> list[str]:
    """Markdown in Chunks splitten — an ## / ### Headings oder max 1500 chars."""
    parts = re.split(r"(?m)^(?=#{1,3} )", text.strip())
    chunks: list[str] = []
    for part in parts:
        part = part.strip()
        if not part:
            continue
        if len(part) <= MAX_CHUNK_CHARS:
            chunks.append(part)
        else:
            i = 0
            while i < len(part):
                chunk = part[i : i + MAX_CHUNK_CHARS].strip()
                if chunk:
                    chunks.append(chunk)
                i += MAX_CHUNK_CHARS - 100
    return chunks


# ---------------------------------------------------------------------------
# FTS5 Query Builder
# ---------------------------------------------------------------------------

def _fts_query(text: str) -> str:
    """User-Message → FTS5 OR-Query. Extrahiert Wörter ≥ 3 Zeichen."""
    words = re.findall(r"[a-zA-ZäöüÄÖÜß\d]{3,}", text)
    if not words:
        return ""
    escaped = [f'"{w.replace(chr(34), chr(39))}"' for w in words[:12]]
    return " OR ".join(escaped)


# ---------------------------------------------------------------------------
# DB Initialisierung
# ---------------------------------------------------------------------------

def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")   # Für ON DELETE CASCADE auf chunk_meta
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS indexed_files (
            path  TEXT PRIMARY KEY,
            mtime REAL NOT NULL,
            size  INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chunks (
            id     INTEGER PRIMARY KEY,
            source TEXT NOT NULL,
            text   TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            text,
            source UNINDEXED,
            content='chunks',
            content_rowid='id',
            tokenize='unicode61'
        );
    """)
    apply_decay_schema(conn)   # chunk_meta + file_meta Tabellen
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Indexierung
# ---------------------------------------------------------------------------

def _needs_reindex(conn: sqlite3.Connection, path: Path) -> bool:
    stat = path.stat()
    row = conn.execute(
        "SELECT mtime, size FROM indexed_files WHERE path = ?", (str(path),)
    ).fetchone()
    return not row or row[0] != stat.st_mtime or row[1] != stat.st_size


def _index_file(conn: sqlite3.Connection, path: Path) -> None:
    stat   = path.stat()
    source = path.stem

    # importance + category aus file_meta lesen (vom write_memory Tool gesetzt)
    importance, category = get_file_meta(conn, source)

    # Alte Chunks entfernen (cascade löscht chunk_meta)
    old = conn.execute("SELECT id FROM chunks WHERE source = ?", (source,)).fetchall()
    if old:
        ids = [r[0] for r in old]
        conn.execute(
            f"DELETE FROM chunks_fts WHERE rowid IN ({','.join('?'*len(ids))})", ids
        )
        conn.execute("DELETE FROM chunks WHERE source = ?", (source,))

    text = path.read_text(encoding="utf-8").strip()
    if not text:
        conn.execute(
            "INSERT OR REPLACE INTO indexed_files (path, mtime, size) VALUES (?,?,?)",
            (str(path), stat.st_mtime, stat.st_size),
        )
        return

    # created_at: mtime der Datei nutzen (realistischer als "now")
    created_at = stat.st_mtime
    now        = time.time()

    for chunk in _split_chunks(text):
        row = conn.execute(
            "INSERT INTO chunks (source, text) VALUES (?,?)", (source, chunk)
        )
        chunk_id = row.lastrowid
        conn.execute(
            "INSERT INTO chunks_fts (rowid, text, source) VALUES (?,?,?)",
            (chunk_id, chunk, source),
        )
        strength = compute_strength(importance, category, 0, created_at, now)
        conn.execute(
            """INSERT OR REPLACE INTO chunk_meta
               (chunk_id, importance, category, recall_count, created_at, last_accessed, strength)
               VALUES (?,?,?,0,?,?,?)""",
            (chunk_id, importance, category, created_at, now, strength),
        )

    conn.execute(
        "INSERT OR REPLACE INTO indexed_files (path, mtime, size) VALUES (?,?,?)",
        (str(path), stat.st_mtime, stat.st_size),
    )


def update_index(agent_dir: Path) -> None:
    """Memory-Index aktualisieren (lazy, nur geänderte Dateien).
    Baut BM25 (SQLite FTS5) und FAISS Semantic Index parallel.
    Führt Ebbinghaus-Decay-Migration und Prune durch.
    """
    memory_dir = agent_dir / "memory"
    if not memory_dir.exists():
        return

    db_path = agent_dir / "memory_index.db"
    conn = _open_db(db_path)
    all_chunks: list[str] = []
    try:
        # Migration bestehender Chunks ohne Decay-Metadata
        if needs_decay_migration(conn):
            migrate_existing_chunks(conn)
            conn.commit()

        changed = False
        for md in sorted(memory_dir.glob("*.md")):
            if md.name in SKIP_FILES:
                continue
            if _needs_reindex(conn, md):
                try:
                    _index_file(conn, md)
                    changed = True
                    logger.debug("memory_index: %s neu indexiert", md.name)
                except OSError as e:
                    logger.warning("memory_index: %s übersprungen (%s)", md.name, e)

        if changed:
            conn.commit()
            logger.debug("memory_index: BM25 Update abgeschlossen für %s", agent_dir.name)

        # Decay: Stärken neu berechnen und schwache Chunks entfernen
        recompute_all_strengths(conn)
        pruned = prune_weak_chunks(conn)
        if pruned:
            conn.commit()
            logger.info(
                "memory_decay: %d schwache Chunks entfernt für Agent %s",
                len(pruned), agent_dir.name,
            )

        # Alle aktuellen Chunks für FAISS-Index sammeln
        rows = conn.execute("SELECT text FROM chunks").fetchall()
        all_chunks = [r[0] for r in rows if r[0].strip()]
    finally:
        conn.close()

    # FAISS Semantic Index aktualisieren
    if all_chunks:
        _update_semantic(agent_dir, all_chunks, "memory")


# ---------------------------------------------------------------------------
# Suche
# ---------------------------------------------------------------------------

def search_memory(agent_dir: Path, query: str, k: int = 6) -> list[str]:
    """Hybrid Memory-Suche: BM25 + Semantik (FAISS) × Ebbinghaus-Strength.

    Strategie: top-(k//2 + 1) BM25 + top-(k//2 + 1) Semantik, dedup, max k.
    final_score = hybrid_score × strength   (Decay-Gewichtung)
    Fallback auf BM25-only wenn FAISS/Embeddings nicht verfügbar.
    """
    db_path = agent_dir / "memory_index.db"
    if not db_path.exists():
        return []

    half = max(k // 2 + 1, 3)

    # ── BM25 ──────────────────────────────────────────────────────────────────
    bm25_results: list[tuple[str, int, float]] = []  # (text, chunk_id, strength)
    fts_q = _fts_query(query)
    if fts_q:
        conn = sqlite3.connect(str(db_path))
        conn.execute("PRAGMA foreign_keys=ON")
        try:
            rows = conn.execute(
                """
                SELECT c.text, c.id, COALESCE(cm.strength, 0.5)
                FROM chunks_fts f
                JOIN chunks c ON c.id = f.rowid
                LEFT JOIN chunk_meta cm ON cm.chunk_id = c.id
                WHERE chunks_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (fts_q, half),
            ).fetchall()
            bm25_results = [(row[0], row[1], row[2]) for row in rows if row[0].strip()]

            # recall_count für gefundene Chunks erhöhen
            if bm25_results:
                touch_recall(conn, [r[1] for r in bm25_results])
                conn.commit()

        except sqlite3.OperationalError as e:
            logger.debug("memory_search FTS Fehler: %s", e)
        finally:
            conn.close()

    bm25_snippets  = [r[0][:SNIPPET_CHARS] for r in bm25_results]
    bm25_strengths = [r[2] for r in bm25_results]

    # ── Semantik ──────────────────────────────────────────────────────────────
    sem_results = _search_semantic(agent_dir, query, k=half, name="memory")
    semantic_snippets = [text[:SNIPPET_CHARS] for text, _score in sem_results]

    # Strength für Semantic-Treffer aus DB nachladen
    sem_strengths: list[float] = []
    if sem_results:
        conn2 = sqlite3.connect(str(db_path))
        conn2.execute("PRAGMA foreign_keys=ON")
        try:
            for text, _ in sem_results:
                row = conn2.execute(
                    "SELECT cm.strength, c.id FROM chunks c "
                    "LEFT JOIN chunk_meta cm ON cm.chunk_id = c.id "
                    "WHERE c.text = ? LIMIT 1",
                    (text,),
                ).fetchone()
                if row:
                    sem_strengths.append(row[0] if row[0] is not None else 0.5)
                    touch_recall(conn2, [row[1]])
                else:
                    sem_strengths.append(0.5)
            conn2.commit()
        finally:
            conn2.close()
    else:
        sem_strengths = []

    # ── Score-Normalisierung + gewichteter Merge mit Decay ────────────────────
    def _minmax_norm(values: list[float], invert: bool = False) -> list[float]:
        if not values:
            return []
        lo, hi = min(values), max(values)
        if hi == lo:
            return [1.0] * len(values)
        normed = [(v - lo) / (hi - lo) for v in values]
        return [1.0 - n for n in normed] if invert else normed

    bm25_raw  = list(range(len(bm25_snippets)))
    bm25_norm = _minmax_norm([float(i) for i in bm25_raw], invert=True)
    sem_scores = [s for _, s in sem_results]
    sem_norm   = _minmax_norm(sem_scores)

    alpha = SEARCH_ALPHA
    combined: dict[str, float] = {}

    for snippet, score, strength in zip(bm25_snippets, bm25_norm, bm25_strengths):
        key = snippet[:120]
        combined[key] = combined.get(key, 0.0) + alpha * score * max(strength, 0.01)

    for (text, _), score, strength in zip(sem_results, sem_norm, sem_strengths):
        snippet = text[:SNIPPET_CHARS]
        key = snippet[:120]
        combined[key] = combined.get(key, 0.0) + (1 - alpha) * score * max(strength, 0.01)

    # Nach kombiniertem Score sortieren
    snippet_map = {s[:120]: s for s in bm25_snippets}
    for text, _ in sem_results:
        s = text[:SNIPPET_CHARS]
        snippet_map.setdefault(s[:120], s)

    ranked = sorted(combined.items(), key=lambda x: -x[1])
    merged = [snippet_map[key] for key, _ in ranked if key in snippet_map][:k]

    logger.debug(
        "memory_search: %d Treffer (BM25=%d, sem=%d) für agent=%s",
        len(merged), len(bm25_snippets), len(semantic_snippets), agent_dir.name,
    )
    return merged
