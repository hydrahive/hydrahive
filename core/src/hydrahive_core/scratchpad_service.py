"""
scratchpad_service.py — Flüchtiger Arbeitskontext für Agenten (#313)

Scratchpad: /etc/hydrahive/agents/<agent_id>/scratchpad.md
Wird bei neuer Session automatisch geleert.
"""
from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)
AGENTS_DIR = Path("/etc/hydrahive/agents")


def _scratchpad_path(agent_id: str) -> Path:
    return AGENTS_DIR / agent_id / "scratchpad.md"


def get_scratchpad(agent_id: str) -> str:
    """Liest den aktuellen Scratchpad-Inhalt oder leeren String."""
    path = _scratchpad_path(agent_id)
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except Exception:
        return ""


def save_scratchpad(agent_id: str, content: str) -> None:
    """Speichert Scratchpad-Inhalt."""
    path = _scratchpad_path(agent_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    logger.debug("scratchpad.md für Agent %s gespeichert (%d Zeichen)", agent_id, len(content))


def clear_scratchpad(agent_id: str) -> None:
    """Löscht den Scratchpad-Inhalt (flüchtig — bei neuer Session)."""
    path = _scratchpad_path(agent_id)
    if path.exists():
        path.unlink()
    logger.debug("scratchpad.md für Agent %s geleert", agent_id)
