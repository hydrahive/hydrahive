#!/usr/bin/env python3
"""
A-MEM MCP Server
================

MCP (Model Context Protocol) Server fuer A-MEM (Agentic Memory System).
Stellt die A-MEM Zettelkasten-basierte Wissens-DB als MCP-Tools bereit.

Konfiguration via Umgebungsvariablen:
    AMEM_LLM_BACKEND    - LLM Backend: "ollama" oder "openai" (default: "ollama")
    AMEM_LLM_MODEL      - LLM Modell (default: "qwen2.5:7b")
    AMEM_EMBEDDING_MODEL - Embedding Modell (default: "all-MiniLM-L6-v2")
    OLLAMA_HOST          - Ollama API URL (default: "http://127.0.0.1:11434")
    AMEM_HOST            - Server Host (default: "0.0.0.0")
    AMEM_PORT            - Server Port (default: 8020)

Starten:
    cd ~/A-mem && source .venv/bin/activate
    python amem_mcp_server.py

MCP-Verbindung (claude_desktop_config.json / CLAUDE.md):
    {
        "mcpServers": {
            "amem": {
                "url": "http://127.0.0.1:8020/sse"
            }
        }
    }

Verfuegbare Tools:
    - amem_add_note       : Neuen Eintrag anlegen (Zettelkasten-Prinzip)
    - amem_search         : Hybride Suche (BM25 + Vektor)
    - amem_search_agentic : Reine Vektor-Suche via ChromaDB
    - amem_read           : Einzelnen Eintrag per ID lesen
    - amem_update         : Eintrag aktualisieren
    - amem_delete         : Eintrag loeschen
    - amem_find_related   : Verwandte Eintraege finden
    - amem_analyze        : Inhalt analysieren (Keywords, Kontext, Tags)
    - amem_consolidate    : Retriever mit neuen Dokumenten aktualisieren
    - amem_stats          : Statistiken ueber die Wissensdatenbank

Architektur:
    Client (Claude Code / Instanzen) --> MCP/SSE --> amem_mcp_server.py
                                                         |
                                                    AgenticMemorySystem
                                                    /        |        \\
                                           ChromaDB    Embeddings    LLM (Ollama)
                                          (Vektoren)  (MiniLM-L6)  (qwen2.5:7b)
"""

import os
import json
import logging
from datetime import datetime
from typing import Any

from mcp.server.fastmcp import FastMCP

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.FileHandler(os.environ.get("AMEM_LOG_FILE", "/var/log/octopos/amem_mcp.log")),
        logging.StreamHandler(),
    ],
)
logger = logging.getLogger("amem-mcp")

# ---------------------------------------------------------------------------
# Konfiguration aus Umgebungsvariablen
# ---------------------------------------------------------------------------
LLM_BACKEND = os.environ.get("AMEM_LLM_BACKEND", "ollama")
LLM_MODEL = os.environ.get("AMEM_LLM_MODEL", "qwen2.5:7b")
EMBEDDING_MODEL = os.environ.get("AMEM_EMBEDDING_MODEL", "all-MiniLM-L6-v2")
OLLAMA_HOST = os.environ.get("OLLAMA_HOST", "http://127.0.0.1:11434")
HOST = os.environ.get("AMEM_HOST", "0.0.0.0")
PORT = int(os.environ.get("AMEM_PORT", "8020"))

# Ollama Host setzen bevor A-MEM importiert wird
os.environ["OLLAMA_HOST"] = OLLAMA_HOST

# ---------------------------------------------------------------------------
# A-MEM initialisieren
# ---------------------------------------------------------------------------
logger.info("A-MEM wird initialisiert...")
logger.info(f"  LLM Backend:     {LLM_BACKEND}")
logger.info(f"  LLM Model:       {LLM_MODEL}")
logger.info(f"  Embedding Model: {EMBEDDING_MODEL}")
logger.info(f"  Ollama Host:     {OLLAMA_HOST}")

from agentic_memory.memory_system import AgenticMemorySystem
from agentic_memory.retrievers import PersistentChromaRetriever

# ChromaDB Persistenz-Verzeichnis
CHROMADB_DIR = os.environ.get("AMEM_CHROMADB_DIR", "/var/lib/octopos/amem/chromadb_data")

memory = AgenticMemorySystem(
    model_name=EMBEDDING_MODEL,
    llm_backend=LLM_BACKEND,
    llm_model=LLM_MODEL,
)

# Retriever auf PersistentChromaRetriever umstellen
# Damit bleiben Daten ueber Neustarts erhalten
logger.info(f"  ChromaDB Dir:    {CHROMADB_DIR}")
memory.retriever = PersistentChromaRetriever(
    directory=CHROMADB_DIR,
    collection_name="memories",
    model_name=EMBEDDING_MODEL,
    extend=True,
)
logger.info("A-MEM erfolgreich initialisiert (persistent)!")

# ---------------------------------------------------------------------------
# self.memories aus ChromaDB hydratisieren
# ---------------------------------------------------------------------------
# A-MEM haelt Eintraege im In-Memory-Dict self.memories.
# Bulk-importierte Daten sind nur in ChromaDB, nicht in self.memories.
# Ohne Hydration findet search() diese Eintraege nicht.
# ---------------------------------------------------------------------------
from agentic_memory.memory_system import MemoryNote

def hydrate_memories():
    """Laedt alle ChromaDB-Eintraege in memory.memories (In-Memory-Dict).

    Fuer jeden Eintrag wird ein MemoryNote-Objekt erstellt:
    - A-MEM-native Eintraege: Volle Metadaten aus ChromaDB metadata
    - Bulk-importierte Eintraege: Content aus ChromaDB document, Basis-Metadaten
    """
    collection = memory.retriever.collection
    total = collection.count()
    if total == 0:
        logger.info("Hydration: Keine Eintraege in ChromaDB")
        return

    logger.info(f"Hydration: {total} Eintraege aus ChromaDB laden...")
    loaded = 0
    batch_size = 500
    offset = 0

    while offset < total:
        batch = collection.get(
            limit=batch_size,
            offset=offset,
            include=["documents", "metadatas"],
        )

        for i, doc_id in enumerate(batch["ids"]):
            if doc_id in memory.memories:
                continue

            doc_text = batch["documents"][i] if batch["documents"] else ""
            meta = batch["metadatas"][i] if batch["metadatas"] else {}

            # Content: aus metadata (A-MEM-native) oder document (import)
            note_content = meta.get("content", "") or doc_text or ""

            note = MemoryNote(
                content=note_content,
                id=doc_id,
                keywords=meta.get("keywords", []),
                links=meta.get("links", []),
                retrieval_count=int(meta.get("retrieval_count", 0)) if meta.get("retrieval_count") else 0,
                timestamp=meta.get("timestamp", ""),
                last_accessed=meta.get("last_accessed", ""),
                context=meta.get("context", ""),
                evolution_history=meta.get("evolution_history", []),
                category=meta.get("category", "Uncategorized"),
                tags=meta.get("tags", []),
            )

            # JSON-Strings aus ChromaDB metadata zurueck in Listen konvertieren
            for attr in ("keywords", "tags", "links", "evolution_history"):
                val = getattr(note, attr)
                if isinstance(val, str):
                    try:
                        parsed = json.loads(val)
                        if isinstance(parsed, list):
                            setattr(note, attr, parsed)
                    except (ValueError, TypeError):
                        pass

            memory.memories[doc_id] = note
            loaded += 1

        offset += batch_size

    logger.info(f"Hydration abgeschlossen: {loaded} Eintraege geladen")

hydrate_memories()

# ---------------------------------------------------------------------------
# MCP Server erstellen
# ---------------------------------------------------------------------------
mcp = FastMCP("A-MEM", host=HOST, port=PORT)


# ---------------------------------------------------------------------------
# Helper: MemoryNote -> Dict
# ---------------------------------------------------------------------------
def note_to_dict(note) -> dict:
    """Konvertiert ein MemoryNote-Objekt in ein serialisierbares Dict.

    Args:
        note: MemoryNote-Instanz aus A-MEM

    Returns:
        Dict mit allen Feldern des MemoryNote
    """
    return {
        "id": note.id,
        "content": note.content,
        "keywords": note.keywords,
        "context": note.context,
        "category": note.category,
        "tags": note.tags,
        "links": note.links,
        "timestamp": note.timestamp,
        "last_accessed": note.last_accessed,
        "retrieval_count": note.retrieval_count,
        "evolution_history": note.evolution_history,
    }


# ---------------------------------------------------------------------------
# Tools
# ---------------------------------------------------------------------------


@mcp.tool()
def amem_add_note(content: str, context: str = "", tags: str = "") -> str:
    """Neuen Eintrag in der Wissensdatenbank anlegen.

    A-MEM analysiert den Inhalt automatisch mit dem LLM und extrahiert
    Keywords, Kontext und Kategorie (Zettelkasten-Prinzip).

    Args:
        content: Der zu speichernde Text/Wissen
        context: Optionaler Kontext (z.B. "server-config", "bugfix", "architektur")
        tags: Optionale komma-separierte Tags (z.B. "soketi,websocket,config")

    Returns:
        JSON mit der ID des neuen Eintrags
    """
    logger.info(f"add_note: {content[:80]}...")
    try:
        kwargs = {}
        if context:
            kwargs["context"] = context
        if tags:
            kwargs["tags"] = [t.strip() for t in tags.split(",")]

        note_id = memory.add_note(content, **kwargs)
        logger.info(f"add_note OK: {note_id}")
        return json.dumps({"success": True, "id": note_id}, ensure_ascii=False)
    except Exception as e:
        logger.error(f"add_note FEHLER: {e}")
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


@mcp.tool()
def amem_search(query: str, k: int = 5) -> str:
    """Hybride Suche in der Wissensdatenbank (BM25 + Vektor-Suche).

    Kombiniert klassische Keyword-Suche (BM25) mit semantischer
    Vektor-Aehnlichkeit fuer beste Ergebnisse.

    Args:
        query: Suchbegriff oder Frage
        k: Maximale Anzahl Ergebnisse (default: 5)

    Returns:
        JSON-Array mit Treffern (id, content, score, keywords, context)
    """
    logger.info(f"search: '{query}' (k={k})")
    try:
        results = memory.search(query, k=k)
        logger.info(f"search: {len(results)} Treffer")
        return json.dumps(results, ensure_ascii=False, default=str)
    except Exception as e:
        logger.error(f"search FEHLER: {e}")
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@mcp.tool()
def amem_search_agentic(query: str, k: int = 5) -> str:
    """Reine Vektor-Suche via ChromaDB (search_agentic_fixed).

    Nutzt ausschliesslich die Embedding-Aehnlichkeit fuer die Suche.
    Besser fuer semantisch aehnliche aber woertlich unterschiedliche Inhalte.
    Liest Content aus self.memories falls ChromaDB-Metadata leer ist.

    Args:
        query: Suchbegriff oder Frage
        k: Maximale Anzahl Ergebnisse (default: 5)

    Returns:
        JSON-Array mit Treffern
    """
    logger.info(f"search_agentic: '{query}' (k={k})")
    try:
        # Direkt ChromaDB abfragen fuer volle Kontrolle
        results = memory.retriever.search(query, k=k)
        memories_list = []
        seen_ids = set()

        if results and results.get("ids") and results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0][:k]):
                if doc_id in seen_ids:
                    continue

                meta = results["metadatas"][0][i] if results.get("metadatas") and results["metadatas"][0] else {}
                doc_text = results["documents"][0][i] if results.get("documents") and results["documents"][0] else ""

                # Content: metadata > self.memories > document
                note_content = meta.get("content", "")
                if not note_content:
                    mem_obj = memory.memories.get(doc_id)
                    if mem_obj:
                        note_content = mem_obj.content
                if not note_content:
                    note_content = doc_text

                memory_dict = {
                    "id": doc_id,
                    "content": note_content,
                    "context": meta.get("context", ""),
                    "keywords": meta.get("keywords", []),
                    "tags": meta.get("tags", []),
                    "timestamp": meta.get("timestamp", ""),
                    "category": meta.get("category", "Uncategorized"),
                }

                if results.get("distances") and results["distances"][0]:
                    memory_dict["score"] = results["distances"][0][i]

                memories_list.append(memory_dict)
                seen_ids.add(doc_id)

        logger.info(f"search_agentic: {len(memories_list)} Treffer")
        return json.dumps(memories_list, ensure_ascii=False, default=str)
    except Exception as e:
        logger.error(f"search_agentic FEHLER: {e}")
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@mcp.tool()
def amem_read(memory_id: str) -> str:
    """Einzelnen Eintrag per ID lesen.

    Gibt alle Metadaten zurueck: content, keywords, context, tags,
    links, timestamps, retrieval_count, evolution_history.

    Args:
        memory_id: Die UUID des Eintrags

    Returns:
        JSON mit allen Feldern des Eintrags, oder Fehlermeldung
    """
    logger.info(f"read: {memory_id}")
    try:
        note = memory.read(memory_id)
        if note is None:
            return json.dumps(
                {"error": f"Eintrag {memory_id} nicht gefunden"}, ensure_ascii=False
            )
        return json.dumps(note_to_dict(note), ensure_ascii=False, default=str)
    except Exception as e:
        logger.error(f"read FEHLER: {e}")
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@mcp.tool()
def amem_update(memory_id: str, content: str = "", context: str = "", tags: str = "") -> str:
    """Bestehenden Eintrag aktualisieren.

    Nur angegebene Felder werden geaendert, der Rest bleibt erhalten.

    Args:
        memory_id: Die UUID des Eintrags
        content: Neuer Inhalt (leer = nicht aendern)
        context: Neuer Kontext (leer = nicht aendern)
        tags: Neue komma-separierte Tags (leer = nicht aendern)

    Returns:
        JSON mit Erfolgs-Status
    """
    logger.info(f"update: {memory_id}")
    try:
        kwargs = {}
        if content:
            kwargs["content"] = content
        if context:
            kwargs["context"] = context
        if tags:
            kwargs["tags"] = [t.strip() for t in tags.split(",")]

        success = memory.update(memory_id, **kwargs)
        return json.dumps({"success": success}, ensure_ascii=False)
    except Exception as e:
        logger.error(f"update FEHLER: {e}")
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


@mcp.tool()
def amem_delete(memory_id: str) -> str:
    """Eintrag aus der Wissensdatenbank loeschen.

    Args:
        memory_id: Die UUID des Eintrags

    Returns:
        JSON mit Erfolgs-Status
    """
    logger.info(f"delete: {memory_id}")
    try:
        success = memory.delete(memory_id)
        return json.dumps({"success": success}, ensure_ascii=False)
    except Exception as e:
        logger.error(f"delete FEHLER: {e}")
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


@mcp.tool()
def amem_find_related(query: str, k: int = 5) -> str:
    """Verwandte Eintraege zu einem Thema finden.

    Nutzt ChromaDB um semantisch verwandte Eintraege zu finden.
    Gibt eine formatierte Zusammenfassung zurueck.

    Args:
        query: Thema oder Frage
        k: Maximale Anzahl verwandter Eintraege (default: 5)

    Returns:
        JSON mit formatiertem Text und IDs der verwandten Eintraege
    """
    logger.info(f"find_related: '{query}' (k={k})")
    try:
        # Direkt ChromaDB nutzen statt find_related_memories
        # (das hat einen "if not self.memories" Guard der brechen kann)
        results = memory.retriever.search(query, k=k)
        related = []

        if results and results.get("ids") and results["ids"] and results["ids"][0]:
            for i, doc_id in enumerate(results["ids"][0][:k]):
                meta = results["metadatas"][0][i] if results.get("metadatas") and results["metadatas"][0] else {}
                doc_text = results["documents"][0][i] if results.get("documents") and results["documents"][0] else ""

                note_content = meta.get("content", "")
                if not note_content:
                    mem_obj = memory.memories.get(doc_id)
                    if mem_obj:
                        note_content = mem_obj.content
                if not note_content:
                    note_content = doc_text

                score = results["distances"][0][i] if results.get("distances") and results["distances"][0] else 0
                related.append({
                    "id": doc_id,
                    "content": note_content[:500],
                    "context": meta.get("context", ""),
                    "score": score,
                })

        logger.info(f"find_related: {len(related)} Treffer")
        return json.dumps(related, ensure_ascii=False, default=str)
    except Exception as e:
        logger.error(f"find_related FEHLER: {e}")
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@mcp.tool()
def amem_analyze(content: str) -> str:
    """Inhalt mit dem LLM analysieren (ohne zu speichern).

    Extrahiert automatisch:
    - Keywords: Wichtige Begriffe und Konzepte
    - Context: Uebergeordnetes Thema/Domaene
    - Tags: Klassifikations-Kategorien

    Nuetzlich um vor dem Speichern zu pruefen, wie A-MEM den Inhalt einordnet.

    Args:
        content: Der zu analysierende Text

    Returns:
        JSON mit keywords, context und tags
    """
    logger.info(f"analyze: {content[:80]}...")
    try:
        result = memory.analyze_content(content)
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        logger.error(f"analyze FEHLER: {e}")
        return json.dumps({"error": str(e)}, ensure_ascii=False)


@mcp.tool()
def amem_consolidate() -> str:
    """Wissensdatenbank konsolidieren.

    Aktualisiert den internen Retriever mit allen neuen Dokumenten.
    Sollte nach groesseren Import-Vorgaengen aufgerufen werden.

    Returns:
        JSON mit Erfolgs-Status
    """
    logger.info("consolidate gestartet")
    try:
        memory.consolidate_memories()
        logger.info("consolidate abgeschlossen")
        return json.dumps({"success": True, "message": "Konsolidierung abgeschlossen"})
    except Exception as e:
        logger.error(f"consolidate FEHLER: {e}")
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


@mcp.tool()
def amem_stats() -> str:
    """Statistiken ueber die Wissensdatenbank.

    Gibt Infos ueber Anzahl Eintraege, Konfiguration und System-Status zurueck.

    Returns:
        JSON mit Statistiken (total_notes, llm_backend, llm_model, etc.)
    """
    logger.info("stats abgerufen")
    try:
        collection = memory.retriever.collection
        count = collection.count()

        stats = {
            "total_notes": count,
            "llm_backend": LLM_BACKEND,
            "llm_model": LLM_MODEL,
            "embedding_model": EMBEDDING_MODEL,
            "ollama_host": OLLAMA_HOST,
            "server": f"{HOST}:{PORT}",
            "chromadb_collection": collection.name,
        }
        return json.dumps(stats, ensure_ascii=False)
    except Exception as e:
        logger.error(f"stats FEHLER: {e}")
        return json.dumps({"error": str(e)}, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Server starten
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    logger.info(f"A-MEM MCP Server startet auf {HOST}:{PORT}")
    mcp.run(transport="sse")
