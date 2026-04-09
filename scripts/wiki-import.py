#!/usr/bin/env python3
"""
wiki-import.py — Importiert vorhandenes Wissen ins BookStack Wiki

Liest Memory-Dateien der Agenten und erstellt Wiki-Seiten daraus.
Kategorisiert automatisch in die richtige Shelf/Book-Struktur.

Usage: python3 scripts/wiki-import.py
"""
import json
import sys
from pathlib import Path

try:
    import httpx
except ImportError:
    print("httpx nicht installiert: pip install httpx")
    sys.exit(1)

# ── Config ────────────────────────────────────────────────────────────────────

CONFIG_PATH = Path("/etc/hydrahive/bookstack.json")
AGENTS_DIR = Path("/agents")

# Kategorisierung: Dateiname-Pattern → (Book-Name, Tags)
CATEGORIES = {
    "hydrahive": ("HydraHive System", ["system", "architecture"]),
    "architecture": ("HydraHive System", ["architecture"]),
    "deployment": ("Workflows", ["deployment"]),
    "workflow": ("Workflows", ["workflow"]),
    "session-summary": ("Session Logs", ["session", "auto-compact"]),
    "daily_": ("Session Logs", ["daily-log"]),
    "sprint": ("Session Logs", ["sprint"]),
    "system-test": ("Systeme", ["testing"]),
    "systemtest": ("Systeme", ["testing"]),
    "test-": ("Systeme", ["testing"]),
    "security": ("Systeme", ["security"]),
    "whatsapp": ("Systeme", ["whatsapp", "integration"]),
    "discord": ("Systeme", ["discord", "integration"]),
    "server": ("Systeme", ["server"]),
    "git": ("Systeme", ["git"]),
    "branding": ("Projekte", ["branding"]),
    "migration": ("Systeme", ["migration"]),
    "user": ("Benutzer & Präferenzen", ["user"]),
    "admin": ("Benutzer & Präferenzen", ["admin"]),
    "learned": ("Lessons Learned", ["lesson-learned"]),
    "fixes": ("Lessons Learned", ["fix"]),
    "_learnings": ("Lessons Learned", ["auto-learning"]),
}

DEFAULT_CATEGORY = ("Allgemein", ["imported"])


def load_config():
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception as e:
        print(f"Config nicht lesbar: {e}")
        sys.exit(1)


def get_client(config):
    return httpx.Client(
        base_url=config["base_url"].rstrip("/"),
        headers={
            "Authorization": f"Token {config['token_id']}:{config['token_secret']}",
            "Content-Type": "application/json",
        },
        timeout=20,
    )


def ensure_book(client, name, shelf_name="Importiert"):
    """Findet oder erstellt ein Buch."""
    r = client.get("/api/books", params={"count": 100})
    for b in r.json().get("data", []):
        if b["name"].lower() == name.lower():
            return b["id"]
    # Erstellen
    r = client.post("/api/books", json={"name": name, "description": f"Importiert aus Agent-Memory"})
    if r.status_code < 300:
        print(f"  Book erstellt: {name}")
        return r.json()["id"]
    return None


def categorize(filename):
    """Bestimmt Book-Name und Tags anhand des Dateinamens."""
    lower = filename.lower()
    for pattern, (book, tags) in CATEGORIES.items():
        if pattern in lower:
            return book, tags
    return DEFAULT_CATEGORY


def import_memory(client, agent_id, memory_dir):
    """Importiert alle Memory-Dateien eines Agenten."""
    files = sorted(memory_dir.glob("*.md"))
    # Skip interne Dateien
    skip = {"INDEX.md", "MEMORY.md", "README.md", "AGENTS.md", "CONSOLIDATED.md",
            "_pre_compact_snapshot.md", "_last_session.md"}

    imported = 0
    skipped = 0

    # Existierende Seiten-Titel laden um Duplikate zu vermeiden
    r = client.get("/api/pages", params={"count": 500})
    existing_titles = {p["name"].lower() for p in r.json().get("data", [])}

    for f in files:
        if f.name in skip:
            skipped += 1
            continue

        content = f.read_text(encoding="utf-8", errors="replace").strip()
        if not content or len(content) < 20:
            skipped += 1
            continue

        book_name, tags = categorize(f.name)
        title = f"[{agent_id}] {f.stem}"

        if title.lower() in existing_titles:
            skipped += 1
            continue

        book_id = ensure_book(client, book_name)
        if not book_id:
            print(f"  SKIP: Kein Book für {f.name}")
            skipped += 1
            continue

        # Seite erstellen
        body = {
            "name": title,
            "markdown": content[:50000],  # BookStack Limit
            "book_id": book_id,
            "tags": [{"name": t} for t in tags + ["agent:" + agent_id, "imported"]],
        }
        r = client.post("/api/pages", json=body)
        if r.status_code < 300:
            imported += 1
            print(f"  ✓ {f.name} → {book_name}")
        else:
            print(f"  ✗ {f.name}: {r.status_code} {r.text[:100]}")
            skipped += 1

    return imported, skipped


def main():
    config = load_config()
    client = get_client(config)

    # Health Check
    try:
        r = client.get("/api/books")
        r.raise_for_status()
    except Exception as e:
        print(f"BookStack nicht erreichbar: {e}")
        sys.exit(1)

    print("BookStack Wiki Import")
    print("=" * 50)

    total_imported = 0
    total_skipped = 0

    # Alle Agenten durchgehen
    for agent_dir in sorted(AGENTS_DIR.iterdir()):
        memory_dir = agent_dir / "memory"
        if not memory_dir.exists():
            continue

        md_files = list(memory_dir.glob("*.md"))
        if not md_files:
            continue

        agent_id = agent_dir.name
        print(f"\n── Agent: {agent_id} ({len(md_files)} Dateien) ──")
        imported, skipped = import_memory(client, agent_id, memory_dir)
        total_imported += imported
        total_skipped += skipped

    # Claude Code Memory (meine)
    claude_mem = Path("/home/till/.claude/projects/-home-till/memory")
    if claude_mem.exists():
        md_files = list(claude_mem.glob("*.md"))
        if md_files:
            print(f"\n── Claude Code Memory ({len(md_files)} Dateien) ──")
            imported, skipped = import_memory(client, "claude-code", claude_mem)
            total_imported += imported
            total_skipped += skipped

    print(f"\n{'=' * 50}")
    print(f"Fertig: {total_imported} importiert, {total_skipped} übersprungen")


if __name__ == "__main__":
    main()
