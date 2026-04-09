"""
built_in_workers.py — Produktisierte Built-in Worker-Profile (#526)

Explore Worker: read-only Codebase-Exploration, findet Dateien und Patterns.
Plan Worker: Implementierungsansatz, betroffene Files, Risiken.

Diese Worker werden als virtuelle Agenten im dispatch_task verfügbar gemacht.
Sie haben eingeschränkte Tool-Sets und spezialisierte System-Prompts.
"""
from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


# ── Worker Definitions ────────────────────────────────────────────────────────

BUILTIN_WORKERS = {
    "explore": {
        "name": "Explore Worker",
        "description": "Read-only Codebase-Exploration. Findet Dateien, Patterns und Code-Strukturen.",
        "system_prompt": (
            "Du bist ein Explore Worker — ein spezialisierter Agent für Codebase-Exploration.\n\n"
            "## Regeln\n"
            "- Du darfst NUR lesen, NIEMALS Dateien ändern oder Befehle mit Seiteneffekten ausführen.\n"
            "- Nutze file_read, list_directory, git_grep, git_diff, git_log, git_status.\n"
            "- Antworte mit konkreten Fundstellen: Dateipfad + Zeilennummer + relevanter Code.\n"
            "- Fasse am Ende zusammen: Was gefunden, wo, wie es zusammenhängt.\n"
            "- Sei gründlich aber effizient — lies nicht ganze Dateien wenn grep reicht.\n"
        ),
        "allowed_tools": [
            "file_read", "list_directory", "read_system_file",
            "git_status", "git_diff", "git_log", "git_grep",
            "web_search", "read_memory",
        ],
    },
    "plan": {
        "name": "Plan Worker",
        "description": "Erstellt Implementierungspläne mit betroffenen Files und Risiken.",
        "system_prompt": (
            "Du bist ein Plan Worker — ein spezialisierter Agent für Implementierungsplanung.\n\n"
            "## Regeln\n"
            "- Du darfst lesen und analysieren, aber KEINE Änderungen vornehmen.\n"
            "- Erstelle einen strukturierten Plan in diesem Format:\n\n"
            "### Ansatz\nBeschreibe den Implementierungsansatz in 2-3 Sätzen.\n\n"
            "### Betroffene Dateien\n- Datei: was dort geändert werden muss\n\n"
            "### Abhängigkeiten\nWas muss vorher erledigt sein?\n\n"
            "### Risiken\n- Risiko: Beschreibung + Mitigation\n\n"
            "### Geschätzter Umfang\nKlein/Mittel/Groß\n\n"
            "- Lies die relevanten Dateien bevor du den Plan erstellst.\n"
            "- Sei konkret: Zeilennummern, Funktionsnamen, Import-Pfade.\n"
        ),
        "allowed_tools": [
            "file_read", "list_directory", "read_system_file",
            "git_status", "git_diff", "git_log", "git_grep",
            "web_search", "read_memory",
        ],
    },
}


def get_builtin_worker(worker_id: str) -> dict | None:
    """Gibt ein Built-in Worker-Profil zurück oder None."""
    return BUILTIN_WORKERS.get(worker_id)


def is_builtin_worker(worker_id: str) -> bool:
    """True wenn worker_id ein Built-in Worker ist."""
    return worker_id in BUILTIN_WORKERS


def list_builtin_workers() -> list[dict]:
    """Alle verfügbaren Built-in Worker."""
    return [
        {"id": wid, "name": w["name"], "description": w["description"], "builtin": True}
        for wid, w in BUILTIN_WORKERS.items()
    ]
