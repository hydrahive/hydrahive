"""
agentlink.py — Filesystem-basierter State-Transfer zwischen Agenten (#13)

Handoffs werden als JSON-Dateien gespeichert:
  /projects/{project_id}/agentlink/{handoff_id}.json

write_handoff  — Agent schreibt einen Handoff (mit TTL)
read_handoff   — Agent liest den naechsten fuer ihn bestimmten Handoff
list_handoffs  — Alle aktiven Handoffs eines Projekts (fuer die Console)
delete_handoff — Manuelles Loeschen (Console / Cleanup)
cleanup_expired — Abgelaufene Handoffs entfernen (Hintergrund-Task)
"""

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

_DT_FMT = "%Y-%m-%dT%H:%M:%S.%f"


def _agentlink_dir(project_dir: Path) -> Path:
    d = project_dir / "agentlink"
    d.mkdir(exist_ok=True)
    return d


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(s: str) -> datetime:
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def write_handoff(
    project_dir: Path,
    from_agent: str,
    to_agent: str | None,
    context: str = "",
    data: dict | None = None,
    ttl_seconds: int = 3600,
) -> dict:
    """
    Handoff-Datei anlegen. Gibt das gespeicherte Handoff-Dict zurueck.
    to_agent=None bedeutet: jeder Agent darf lesen.
    """
    handoff_id = str(uuid.uuid4())
    now        = _now_utc()
    expires_at = now + timedelta(seconds=ttl_seconds)

    entry = {
        "id":         handoff_id,
        "created_at": now.isoformat(),
        "expires_at": expires_at.isoformat(),
        "from_agent": from_agent,
        "to_agent":   to_agent or None,
        "context":    context,
        "data":       data or {},
    }

    path = _agentlink_dir(project_dir) / f"{handoff_id}.json"
    path.write_text(json.dumps(entry, indent=2), encoding="utf-8")
    logger.info(
        "AgentLink write_handoff: %s -> %s (id=%s, ttl=%ds)",
        from_agent, to_agent or "any", handoff_id, ttl_seconds,
    )
    return entry


def read_handoff(
    project_dir: Path,
    to_agent: str | None = None,
    consume: bool = True,
) -> dict | None:
    """
    Naechsten passenden Handoff lesen.
    to_agent=None: ersten verfuegbaren lesen.
    consume=True:  Datei nach dem Lesen loeschen.
    Gibt None zurueck wenn kein passender Handoff vorhanden.
    """
    now = _now_utc()
    al_dir = _agentlink_dir(project_dir)

    for path in sorted(al_dir.glob("*.json")):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue

        # Abgelaufen?
        if _parse_dt(entry["expires_at"]) <= now:
            continue

        # Passt to_agent-Filter?
        stored_to = entry.get("to_agent")
        if to_agent is not None:
            if stored_to is not None and stored_to != to_agent:
                continue

        # Treffer
        if consume:
            path.unlink(missing_ok=True)
            logger.info(
                "AgentLink read_handoff (consumed): id=%s by %s",
                entry["id"], to_agent or "any",
            )
        else:
            logger.info(
                "AgentLink read_handoff (peek): id=%s by %s",
                entry["id"], to_agent or "any",
            )
        return entry

    return None


def list_handoffs(project_dir: Path) -> list[dict]:
    """Alle Handoffs eines Projekts zurueckgeben (inkl. abgelaufener fuer die Console)."""
    al_dir = _agentlink_dir(project_dir)
    result = []
    for path in sorted(al_dir.glob("*.json"), reverse=True):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            result.append(entry)
        except (OSError, json.JSONDecodeError):
            continue
    return result


def delete_handoff(project_dir: Path, handoff_id: str) -> bool:
    """Einzelnen Handoff loeschen. Gibt True zurueck wenn gefunden und geloescht."""
    # Sicherheitscheck: nur UUID-artige Namen erlaubt
    safe_id = handoff_id.replace("/", "").replace("..", "")
    path = _agentlink_dir(project_dir) / f"{safe_id}.json"
    if path.exists():
        path.unlink()
        logger.info("AgentLink delete_handoff: id=%s", handoff_id)
        return True
    return False


def cleanup_expired(project_dir: Path) -> int:
    """Abgelaufene Handoffs loeschen. Gibt Anzahl geloeschter Dateien zurueck."""
    now    = _now_utc()
    al_dir = _agentlink_dir(project_dir)
    count  = 0
    for path in al_dir.glob("*.json"):
        try:
            entry = json.loads(path.read_text(encoding="utf-8"))
            if _parse_dt(entry["expires_at"]) <= now:
                path.unlink(missing_ok=True)
                count += 1
        except (OSError, json.JSONDecodeError):
            path.unlink(missing_ok=True)
            count += 1
    if count:
        logger.info("AgentLink cleanup: %d abgelaufene Handoffs entfernt (%s)", count, project_dir.name)
    return count
