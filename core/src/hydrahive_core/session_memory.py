"""
session_memory.py — Automatische Notizen während Chat (#489)

Inspiriert von Claude Code sessionMemory.ts.
Nach N Tool-Calls extrahiert ein Background-LLM-Call Key-Facts
und schreibt sie in die Agent-Memory.

Unterschied zu AutoDream:
- AutoDream: Nachträglich, über mehrere Sessions, periodisch (24h)
- Session Memory: Live, innerhalb einer Session, nach jedem 10. Tool-Call
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

# Schwellwerte
TOOL_CALL_THRESHOLD = 10   # Nach N Tool-Calls erstmals extrahieren
UPDATE_THRESHOLD = 10      # Danach alle N Tool-Calls updaten

# Pro-Agent Zähler (In-Memory, resettet bei Restart)
_tool_call_counts: dict[str, int] = {}
_last_extraction_at: dict[str, int] = {}  # agent_id → tool_call_count bei letzter Extraction


def record_tool_call(agent_id: str) -> bool:
    """Tool-Call zählen. Gibt True zurück wenn Extraction fällig ist."""
    _tool_call_counts[agent_id] = _tool_call_counts.get(agent_id, 0) + 1
    count = _tool_call_counts[agent_id]
    last = _last_extraction_at.get(agent_id, 0)

    if last == 0 and count >= TOOL_CALL_THRESHOLD:
        return True
    if last > 0 and (count - last) >= UPDATE_THRESHOLD:
        return True
    return False


def mark_extracted(agent_id: str) -> None:
    """Markiert dass Extraction durchgeführt wurde."""
    _last_extraction_at[agent_id] = _tool_call_counts.get(agent_id, 0)


async def extract_session_facts(
    agent_id: str,
    agent_dir: Path,
    recent_context: list[dict],
) -> str | None:
    """Extrahiert Key-Facts aus dem aktuellen Konversations-Kontext.
    Schreibt in memory/session-notes-{date}.md. Gibt Summary zurück oder None."""
    if not recent_context:
        return None

    from .orchestrator import _load_claude_oauth_token
    oauth_token = _load_claude_oauth_token()
    if not oauth_token:
        return None

    # Kontext aufbereiten
    lines = []
    for m in recent_context[-20:]:  # Letzte 20 Messages
        role = m.get("role", "")
        content = m.get("content", "")
        if isinstance(content, list):
            content = " ".join(p.get("text", "") for p in content if isinstance(p, dict))
        if role in ("user", "assistant") and content:
            lines.append(f"{role.upper()}: {content[:500]}")
    conversation = "\n\n".join(lines)
    if not conversation or len(conversation) < 100:
        return None

    try:
        import anthropic
        from .provider_config import ANTHROPIC_OAUTH_HEADERS
        client = anthropic.AsyncAnthropic(
            api_key="",
            auth_token=oauth_token,
            default_headers=ANTHROPIC_OAUTH_HEADERS,
        )
        resp = await client.messages.create(
            model="claude-haiku-4-5-20251001",
            max_tokens=400,
            system=[{"type": "text", "text": (
                "Extrahiere die 3-5 wichtigsten Fakten aus dieser Konversation. "
                "Format: Bullet-Points, kompakt, max 200 Wörter. Auf Deutsch. "
                "Fokus auf: Entscheidungen, technische Fakten, User-Präferenzen, offene Punkte. "
                "Nur Fakten, keine Zusammenfassung der Unterhaltung."
            )}],
            messages=[{"role": "user", "content": conversation}],
        )
        facts = (resp.content[0].text if resp.content else "").strip()
        if not facts or len(facts) < 20:
            return None

        # In Memory-Datei schreiben
        memory_dir = agent_dir / "memory"
        memory_dir.mkdir(exist_ok=True)
        date_str = datetime.now().strftime("%Y-%m-%d")
        notes_path = memory_dir / f"session-notes-{date_str}.md"

        timestamp = datetime.now(timezone.utc).strftime("%H:%M")
        entry = f"\n\n## {timestamp} UTC\n{facts}\n"

        with notes_path.open("a", encoding="utf-8") as f:
            if notes_path.stat().st_size == 0:
                f.write(f"# Session-Notizen {date_str}\n")
            f.write(entry)
        notes_path.chmod(0o600)

        logger.info("Session Memory für '%s': %d Zeichen extrahiert", agent_id, len(facts))
        return facts

    except Exception as e:
        logger.debug("Session memory extraction failed for %s: %s", agent_id, e)
        return None
