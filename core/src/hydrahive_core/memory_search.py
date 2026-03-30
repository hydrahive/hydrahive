"""
memory_search.py — Hybrid Memory Search für HydraHive (#44)

SQLite FTS5 (BM25) + optionale FAISS-Semantic-Suche.
Kein GPU, kein externer Service — alles in-process.

Architektur:
- Pro Agent eine SQLite DB: /agents/{id}/memory_index.db (BM25)
- Pro Agent ein FAISS-Index: /agents/{id}/memory_index.faiss (Semantic)
- Lazy re-indexing: Dateien werden nur bei mtime/size-Änderung neu indexiert
- Chunks: Split by markdown headers, max 1500 chars/Chunk
- Hybrid: BM25 top-k/2 + Semantic top-k/2, dedup, max k Ergebnisse

Verwendung:
    from .memory_search import search_memory, update_index

    update_index(agent_dir)              # lazy, schnell wenn nichts geändert
    snippets = search_memory(agent_dir, user_text, k=6)
"""
from __future__ import annotations

import logging
import re
import sqlite3
from pathlib import Path

from .semantic_index import update_index as _update_semantic, search_index as _search_semantic

logger = logging.getLogger(__name__)

SKIP_FILES: frozenset[str] = frozenset({"learned-facts.md", "MEMORY.md", "INDEX.md"})
MAX_CHUNK_CHARS = 1500   # max chars pro Chunk beim Indexieren
SNIPPET_CHARS   = 700    # max chars pro Treffer im System-Prompt


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
            # Langer Block → 1500-char Stücke mit 100-char Overlap
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
    # Nur alphanumerische + Umlaute — FTS5-Sonderzeichen wie * : " werden nicht extrahiert
    words = re.findall(r"[a-zA-ZäöüÄÖÜß\d]{3,}", text)
    if not words:
        return ""
    # Doppelte Anführungszeichen aus Wörtern entfernen, dann als Phrase quoten
    escaped = [f'"{w.replace(chr(34), chr(39))}"' for w in words[:12]]
    return " OR ".join(escaped)


# ---------------------------------------------------------------------------
# DB Initialisierung
# ---------------------------------------------------------------------------

def _open_db(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
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

    # Alte Chunks entfernen
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

    for chunk in _split_chunks(text):
        row = conn.execute(
            "INSERT INTO chunks (source, text) VALUES (?,?)", (source, chunk)
        )
        conn.execute(
            "INSERT INTO chunks_fts (rowid, text, source) VALUES (?,?,?)",
            (row.lastrowid, chunk, source),
        )

    conn.execute(
        "INSERT OR REPLACE INTO indexed_files (path, mtime, size) VALUES (?,?,?)",
        (str(path), stat.st_mtime, stat.st_size),
    )


def update_index(agent_dir: Path) -> None:
    """Memory-Index aktualisieren (lazy, nur geänderte Dateien).
    Baut BM25 (SQLite FTS5) und FAISS Semantic Index parallel.
    """
    memory_dir = agent_dir / "memory"
    if not memory_dir.exists():
        return

    db_path = agent_dir / "memory_index.db"
    conn = _open_db(db_path)
    all_chunks: list[str] = []
    try:
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

        # Alle aktuellen Chunks für FAISS-Index sammeln
        rows = conn.execute("SELECT text FROM chunks").fetchall()
        all_chunks = [r[0] for r in rows if r[0].strip()]
    finally:
        conn.close()

    # FAISS Semantic Index aktualisieren (lazy, nur wenn Chunks geändert)
    if all_chunks:
        _update_semantic(agent_dir, all_chunks, "memory")


# ---------------------------------------------------------------------------
# Suche
# ---------------------------------------------------------------------------

def search_memory(agent_dir: Path, query: str, k: int = 6) -> list[str]:
    """Hybrid Memory-Suche: BM25 + Semantik (FAISS).

    Strategie: top-(k//2 + 1) BM25 + top-(k//2 + 1) Semantik, dedup, max k.
    Fallback auf BM25-only wenn FAISS/Embeddings nicht verfügbar.
    """
    db_path = agent_dir / "memory_index.db"
    if not db_path.exists():
        return []

    half = max(k // 2 + 1, 3)

    # ── BM25 ──────────────────────────────────────────────────────────────────
    bm25_snippets: list[str] = []
    fts_q = _fts_query(query)
    if fts_q:
        conn = sqlite3.connect(str(db_path))
        try:
            rows = conn.execute(
                """
                SELECT c.text
                FROM chunks_fts f
                JOIN chunks c ON c.id = f.rowid
                WHERE chunks_fts MATCH ?
                ORDER BY rank
                LIMIT ?
                """,
                (fts_q, half),
            ).fetchall()
            bm25_snippets = [row[0][:SNIPPET_CHARS] for row in rows if row[0].strip()]
        except sqlite3.OperationalError as e:
            logger.debug("memory_search FTS Fehler: %s", e)
        finally:
            conn.close()

    # ── Semantik ──────────────────────────────────────────────────────────────
    semantic_snippets: list[str] = []
    sem_results = _search_semantic(agent_dir, query, k=half, name="memory")
    semantic_snippets = [text[:SNIPPET_CHARS] for text, _score in sem_results]

    # ── Score-Normalisierung + gewichteter Merge ──────────────────────────────
    # BM25-rank ist negativ (niedriger = besser), Vektor-Score ist Cosine (höher = besser).
    # Beide min-max normalisieren → [0, 1], dann kombinieren.
    def _minmax_norm(values: list[float], invert: bool = False) -> list[float]:
        if not values:
            return []
        lo, hi = min(values), max(values)
        if hi == lo:
            return [1.0] * len(values)
        normed = [(v - lo) / (hi - lo) for v in values]
        return [1.0 - n for n in normed] if invert else normed

    # BM25: SQLite rank ist negativ → invertieren damit höher = besser
    bm25_raw   = list(range(len(bm25_snippets)))          # Positions-Proxy (0=beste)
    bm25_norm  = _minmax_norm([float(i) for i in bm25_raw], invert=True)
    sem_scores = [s for _, s in sem_results]
    sem_norm   = _minmax_norm(sem_scores)

    alpha = 0.5   # Gewichtung BM25 vs. Semantik
    combined: dict[str, float] = {}
    for snippet, score in zip(bm25_snippets, bm25_norm):
        key = snippet[:120]
        combined[key] = combined.get(key, 0.0) + alpha * score
    for (text, _), score in zip(sem_results, sem_norm):
        snippet = text[:SNIPPET_CHARS]
        key = snippet[:120]
        combined[key] = combined.get(key, 0.0) + (1 - alpha) * score

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
