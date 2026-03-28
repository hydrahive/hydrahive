"""butler_rule.py — Butler Flow Datenmodell + JSON-Persistenz"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

BUTLER_DIR = Path("/etc/hydrahive/butler")


class ButlerFlow(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    enabled: bool = True
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


def load_flows() -> list[ButlerFlow]:
    BUTLER_DIR.mkdir(parents=True, exist_ok=True)
    flows: list[ButlerFlow] = []
    for f in sorted(BUTLER_DIR.glob("*.json")):
        try:
            flows.append(ButlerFlow.model_validate_json(f.read_text()))
        except Exception:
            pass
    return flows


def get_flow(flow_id: str) -> ButlerFlow | None:
    p = BUTLER_DIR / f"{flow_id}.json"
    if not p.exists():
        return None
    try:
        return ButlerFlow.model_validate_json(p.read_text())
    except Exception:
        return None


def save_flow(flow: ButlerFlow) -> None:
    BUTLER_DIR.mkdir(parents=True, exist_ok=True)
    (BUTLER_DIR / f"{flow.id}.json").write_text(
        flow.model_dump_json(indent=2), encoding="utf-8"
    )


def delete_flow(flow_id: str) -> bool:
    p = BUTLER_DIR / f"{flow_id}.json"
    if p.exists():
        p.unlink()
        return True
    return False
