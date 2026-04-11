"""
execution_mode_policy.py — v2: Vereinfachte Execution-Mode Validierung

Execution-Mode wird nur noch von shell_exec genutzt:
  - "safe" / None → Blocklist aktiv, kein sudo
  - "unrestricted" → sudo, keine Blocklist

Rollen-basierte Permissions gibt es nicht mehr.
"""
from __future__ import annotations

from typing import Literal

from fastapi import HTTPException

ExecutionMode = Literal["safe", "elevated", "root", "unrestricted"]


def resolve_request_execution_mode(
    auth: tuple[str, str],
    requested_mode: ExecutionMode | None,
    *,
    audit_log=None,
    audit_target: str | None = None,
    audit_source: str | None = None,
    personal_agent: bool = False,
) -> ExecutionMode | None:
    """Bestimmt den effektiven Execution-Mode für einen Request.

    v2: Vereinfacht — nur noch Admin-Check für unrestricted/root.
    Internal Requests (username="internal") dürfen alles.
    """
    username, role = auth
    is_internal = username == "internal"

    if is_internal:
        return requested_mode

    # Kein expliziter Modus → Agent-Default
    if requested_mode is None:
        return None

    # unrestricted/root nur für Admins
    if requested_mode in {"root", "unrestricted"} and role != "admin":
        raise HTTPException(403, "Execution mode erfordert Admin-Rechte")

    # Audit-Log wenn gewünscht
    if requested_mode in {"elevated", "root", "unrestricted"} and audit_log is not None:
        audit_log(
            "agent.execution_mode",
            user=username,
            target=audit_target,
            details={"requested_mode": requested_mode, "source": audit_source or "unknown"},
        )

    return requested_mode
