"""
tool_confirmation.py — CONFIRM-Round-Trip für Tool-Calls (#641)

`permission_classifier.classify_action()` kann `RiskLevel.CONFIRM` liefern.
Vorher wurde dieser Risk-Level nur geloggt — der Tool-Call lief durch.
Hier wird er erzwungen: Pending-Eintrag → User-Antwort über REST-Endpoint
→ Event freigegeben → Tool-Loop fährt fort.

Pending-Store ist In-Memory, session-gebunden, mit Timeout-GC.
Schlüssel: (session_id, tool_call_id) — eindeutig, kein Cross-Session-Leak.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Literal

logger = logging.getLogger(__name__)

Decision = Literal["approve", "deny"]
WaitResult = Literal["approve", "deny", "timeout"]

# Default-Timeout für eine ausstehende Bestätigung (5 Minuten — analog
# zu Anthropic-Cache-TTL und etablierter SSE-Wartezeit).
DEFAULT_CONFIRM_TIMEOUT = 300.0


@dataclass
class _PendingEntry:
    tool_name: str
    tool_input: dict
    created_at: float
    event: asyncio.Event = field(default_factory=asyncio.Event)
    decision: Decision | None = None


# In-Memory-Store. Schlüssel (session_id, tool_call_id) — eindeutig.
_pending: dict[tuple[str, str], _PendingEntry] = {}


def request_confirmation(
    session_id: str,
    tool_call_id: str,
    tool_name: str,
    tool_input: dict,
) -> _PendingEntry:
    """Legt einen Pending-Eintrag an. Idempotent: bei vorhandenem
    Eintrag mit gleichem Schlüssel wird der existierende zurückgegeben."""
    key = (session_id, tool_call_id)
    existing = _pending.get(key)
    if existing is not None:
        return existing
    entry = _PendingEntry(
        tool_name=tool_name,
        tool_input=tool_input,
        created_at=time.time(),
    )
    _pending[key] = entry
    logger.info(
        "tool-confirm pending: session=%s tool_call_id=%s tool=%s",
        session_id[:8] if session_id else "?", tool_call_id, tool_name,
    )
    return entry


async def wait_for_confirmation(
    session_id: str,
    tool_call_id: str,
    timeout: float | None = None,
) -> WaitResult:
    """Wartet auf User-Antwort. Räumt den Pending-Eintrag in jedem Fall auf.

    Default-Timeout wird zur Laufzeit aus `DEFAULT_CONFIRM_TIMEOUT` aufgelöst,
    damit Tests den Modul-Wert per monkeypatch ändern können."""
    if timeout is None:
        timeout = DEFAULT_CONFIRM_TIMEOUT
    key = (session_id, tool_call_id)
    entry = _pending.get(key)
    if entry is None:
        # Sollte nicht passieren, weil Caller direkt vorher request_confirmation
        # aufgerufen hat. Defensiv: als deny werten, statt blind weiterauszuführen.
        logger.warning(
            "wait_for_confirmation: kein Pending-Eintrag für session=%s tool_call_id=%s",
            session_id[:8] if session_id else "?", tool_call_id,
        )
        return "deny"
    try:
        await asyncio.wait_for(entry.event.wait(), timeout=timeout)
        decision = entry.decision or "deny"  # defensiv falls Event ohne Decision gesetzt
        return decision
    except asyncio.TimeoutError:
        logger.warning(
            "tool-confirm timeout: session=%s tool_call_id=%s tool=%s nach %.0fs",
            session_id[:8] if session_id else "?", tool_call_id, entry.tool_name, timeout,
        )
        return "timeout"
    finally:
        _pending.pop(key, None)


def resolve_confirmation(
    session_id: str,
    tool_call_id: str,
    decision: Decision,
) -> Literal["resolved", "not_found", "already_resolved"]:
    """Wird vom REST-Endpoint gerufen. Setzt Decision + signalisiert Event.

    Returns:
        "resolved"          — Pending-Eintrag gefunden + Decision gesetzt
        "not_found"         — kein Eintrag für (session_id, tool_call_id)
        "already_resolved"  — Decision war schon gesetzt (Doppel-Klick)
    """
    key = (session_id, tool_call_id)
    entry = _pending.get(key)
    if entry is None:
        return "not_found"
    if entry.decision is not None:
        return "already_resolved"
    entry.decision = decision
    entry.event.set()
    logger.info(
        "tool-confirm resolved: session=%s tool_call_id=%s decision=%s",
        session_id[:8] if session_id else "?", tool_call_id, decision,
    )
    return "resolved"


def get_pending(session_id: str) -> list[dict]:
    """Liefert alle pending Confirms einer Session — für Polling-Fallback
    und Diagnose."""
    out: list[dict] = []
    for (sid, tcid), entry in _pending.items():
        if sid != session_id:
            continue
        out.append({
            "tool_call_id": tcid,
            "tool_name":    entry.tool_name,
            "tool_input":   entry.tool_input,
            "created_at":   entry.created_at,
            "age_seconds":  time.time() - entry.created_at,
            "resolved":     entry.decision is not None,
        })
    return out


def clear_session_pending(session_id: str) -> int:
    """Räumt alle pending Einträge einer Session auf (z.B. bei /clear).
    Setzt die Events auf 'deny', damit hängende Waiter sauber zurückkommen."""
    keys_to_remove = [k for k in _pending if k[0] == session_id]
    for key in keys_to_remove:
        entry = _pending.get(key)
        if entry is not None and entry.decision is None:
            entry.decision = "deny"
            entry.event.set()
        _pending.pop(key, None)
    return len(keys_to_remove)


def _reset_for_tests() -> None:
    """Nur für Tests — leert den globalen Store."""
    _pending.clear()
