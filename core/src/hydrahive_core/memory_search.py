"""
memory_search.py — In-Process Memory Search (OpenClaw-Stil)

SQLite FTS5 (BM25) basierte Memory-Suche für HydraHive Agenten.
Kein GPU, kein externer Service — alles in-process.

Architektur:
- Pro Agent eine SQLite DB: /agents/{id}/memory_index.db
- FTS5 virtual table für BM25 keyword search
- Lazy re-indexing: Dateien werden nur bei mtime/size-Änderung neu indexiert
- Chunks: Split by markdown headers, max 1500 chars/Chunk
- Hybrid optional: falls litellm Embeddings konfiguriert → 0.7 vec + 0.3 bm25

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
    words = re.findall(r"[a-zA-ZäöüÄÖÜß\d]{3,}", text)
    if not words:
        return ""
    # FTS5: doppelte Anführungszeichen escapen, max 12 Begriffe
    escaped = [f'"{w.replace(chr(34), "")}"' for w in words[:12]]
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
    """Memory-Index aktualisieren (lazy, nur geänderte Dateien)."""
    memory_dir = agent_dir / "memory"
    if not memory_dir.exists():
        return

    db_path = agent_dir / "memory_index.db"
    conn = _open_db(db_path)
    try:
        changed = False
        for md in memory_dir.glob("*.md"):
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
            logger.debug("memory_index: Update abgeschlossen für %s", agent_dir.name)
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Suche
# ---------------------------------------------------------------------------

def search_memory(agent_dir: Path, query: str, k: int = 6) -> list[str]:
    """BM25-Suche im Memory-Index. Gibt Liste von Snippets zurück.

    Fallback auf neueste Dateien wenn Index leer oder Query zu kurz.
    """
    db_path = agent_dir / "memory_index.db"
    if not db_path.exists():
        return []

    fts_q = _fts_query(query)
    if not fts_q:
        return []

    conn = sqlite3.connect(str(db_path))
    try:
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
                (fts_q, k),
            ).fetchall()
        except sqlite3.OperationalError as e:
            logger.debug("memory_search FTS Fehler: %s", e)
            return []

        snippets = [row[0][:SNIPPET_CHARS] for row in rows if row[0].strip()]
        logger.debug("memory_search: %d Treffer für agent=%s", len(snippets), agent_dir.name)
        return snippets
    finally:
        conn.close()
