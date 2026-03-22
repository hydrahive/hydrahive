from __future__ import annotations

from typing import Literal

from fastapi import HTTPException

ExecutionMode = Literal["safe", "elevated", "root"]


def resolve_request_execution_mode(
    auth: tuple[str, str],
    requested_mode: ExecutionMode | None,
    *,
    audit_log=None,
    audit_target: str | None = None,
    audit_source: str | None = None,
) -> ExecutionMode | None:
    username, role = auth
    is_internal = username == "internal"

    if is_internal:
        return requested_mode

    mode: ExecutionMode = requested_mode or "safe"
    if mode in {"elevated", "root"}:
        if role != "admin":
            raise HTTPException(403, "Execution mode erfordert Admin-Rechte")
        if audit_log is not None:
            audit_log(
                "agent.execution_mode",
                user=username,
                target=audit_target,
                details={"requested_mode": mode, "source": audit_source or "unknown"},
            )
    return mode
