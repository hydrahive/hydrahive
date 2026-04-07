"""butler_rule.py — Butler Flow Datenmodell + JSON-Persistenz

Flows sind user-scoped: /etc/hydrahive/butler/{owner}/{flow_id}.json
load_flows(owner=None) lädt alle Flows (für Ausführung durch den Server).
load_flows(owner="alice") lädt nur Flows von "alice" (für die API).

Legacy-Migration: Root-level *.json werden als admin-Flows behandelt.
"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from .settings import settings

BUTLER_DIR = settings.butler_dir


class ButlerFlow(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    owner: str = ""
    enabled: bool = True
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


def _owner_dir(owner: str) -> Path:
    return BUTLER_DIR / owner


def _migrate_legacy(owner: str) -> None:
    """Verschiebt root-level *.json (alte globale Flows) in den admin-Unterordner."""
    if owner != "admin":
        return
    target = _owner_dir("admin")
    for f in BUTLER_DIR.glob("*.json"):
        try:
            target.mkdir(parents=True, exist_ok=True)
            dest = target / f.name
            if not dest.exists():
                dest.write_text(f.read_text(encoding="utf-8"), encoding="utf-8")
            f.unlink()
        except Exception:
            pass


def load_flows(owner: str | None = None) -> list[ButlerFlow]:
    """Lädt Flows.
    owner=None → alle User-Flows (für Server-seitige Ausführung).
    owner=str  → nur Flows dieses Users (für die API).
    """
    BUTLER_DIR.mkdir(parents=True, exist_ok=True)
    if owner is not None:
        _migrate_legacy(owner)
        files = sorted(_owner_dir(owner).glob("*.json"))
    else:
        # System-Ausführung: alle Unterordner-Flows
        files = sorted(BUTLER_DIR.glob("*/*.json"))
    flows: list[ButlerFlow] = []
    for f in files:
        try:
            flows.append(ButlerFlow.model_validate_json(f.read_text()))
        except Exception:
            pass
    return flows


def get_flow(flow_id: str, owner: str) -> ButlerFlow | None:
    _migrate_legacy(owner)
    p = _owner_dir(owner) / f"{flow_id}.json"
    if not p.exists():
        return None
    try:
        return ButlerFlow.model_validate_json(p.read_text())
    except Exception:
        return None


def save_flow(flow: ButlerFlow) -> None:
    d = _owner_dir(flow.owner)
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{flow.id}.json").write_text(
        flow.model_dump_json(indent=2), encoding="utf-8"
    )


def delete_flow(flow_id: str, owner: str) -> bool:
    p = _owner_dir(owner) / f"{flow_id}.json"
    if p.exists():
        p.unlink()
        return True
    return False
