"""
blueprint_service.py — Blueprint-Persistenz und Schema (#312)

#312: Blueprints als versionierte, reproduzierbare Konfigurationsartefakte.
"""
from __future__ import annotations

import logging
import time
import yaml
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)
BLUEPRINTS_DIR = Path("/etc/hydrahive/blueprints")
AGENTS_DIR = Path("/etc/hydrahive/agents")

# ── Manifest-Schema ──────────────────────────────────────────────────────────

class BlueprintNode(BaseModel):
    type: str                                    # "repository" | "credential" | "skill" | "memory" | "toolpolicy"
    label: str
    config: dict[str, Any] = Field(default_factory=dict)


class BlueprintManifest(BaseModel):
    id:          str                             # Blueprint-ID (slug)
    version:    str = "1.0"
    description: str = ""
    created_at:  str = ""                         # ISO-8601
    installed_at: str = ""                        # ISO-8601, gesetzt bei Installation
    nodes:       list[BlueprintNode] = Field(default_factory=list)
    edges:       list[dict] = Field(default_factory=list)  # {source, target, ...}

    def to_dict(self) -> dict:
        return self.model_dump(exclude_none=True)


# ── Service ──────────────────────────────────────────────────────────────────

def _ensure_blueprints_dir() -> None:
    BLUEPRINTS_DIR.mkdir(parents=True, exist_ok=True)


def list_blueprints() -> list[dict]:
    """Gibt alle Blueprints (ohne Full-Nodes) zurück."""
    _ensure_blueprints_dir()
    result = []
    for d in BLUEPRINTS_DIR.iterdir():
        if d.is_dir():
            m = d / "manifest.yaml"
            if m.exists():
                try:
                    data = yaml.safe_load(m.read_text(encoding="utf-8"))
                    result.append({
                        "id": d.name,
                        "version": data.get("version", "?"),
                        "description": data.get("description", ""),
                        "installed_at": data.get("installed_at", ""),
                        "node_count": len(data.get("nodes", [])),
                    })
                except Exception:
                    pass
    return result


def get_blueprint(bp_id: str) -> BlueprintManifest | None:
    """Lädt Blueprint <bp_id> aus /etc/hydrahive/blueprints/<bp_id>/manifest.yaml."""
    path = BLUEPRINTS_DIR / bp_id / "manifest.yaml"
    if not path.exists():
        return None
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        return BlueprintManifest.model_validate(data)
    except Exception as e:
        logger.warning("Blueprint %s konnte nicht geladen werden: %s", bp_id, e)
        return None


def save_blueprint(bp: BlueprintManifest) -> None:
    """Speichert Blueprint nach /etc/hydrahive/blueprints/<bp.id>/manifest.yaml."""
    _ensure_blueprints_dir()
    if not bp.created_at:
        bp.created_at = datetime.now(timezone.utc).isoformat()
    out = BlueprintManifest.model_validate(bp.to_dict())
    out.nodes = [BlueprintNode.model_validate(n) for n in out.nodes]
    path = BLUEPRINTS_DIR / bp.id / "manifest.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    import json
    path.write_text(
        json.dumps(out.to_dict(), indent=2, ensure_ascii=False, sort_keys=False),
        encoding="utf-8"
    )
    logger.info("Blueprint %s v%s gespeichert", bp.id, bp.version)


def delete_blueprint(bp_id: str) -> bool:
    """Löscht Blueprint-Verzeichnis. Gibt True zurück wenn vorhanden."""
    import shutil
    path = BLUEPRINTS_DIR / bp_id
    if path.exists():
        shutil.rmtree(path)
        return True
    return False


def install_to_agent(bp_id: str, agent_id: str) -> dict:
    """Kopiert Blueprint-Nodes/-Edges als workflow_blueprint.json in Agent-Dir."""
    bp = get_blueprint(bp_id)
    if not bp:
        return {"error": f"Blueprint '{bp_id}' nicht gefunden"}

    agents_dir = Path("/etc/hydrahive/agents")
    agent_dir  = agents_dir / agent_id
    if not agent_dir.exists():
        return {"error": f"Agent '{agent_id}' nicht gefunden"}

    wf_path = agent_dir / "workflow_blueprint.json"
    import json
    # installed_at setzen
    bp.installed_at = datetime.now(timezone.utc).isoformat()
    wf_path.write_text(json.dumps(bp.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

    # Cache invalidieren
    try:
        from .orchestrator_context import invalidate_prompt_cache
        invalidate_prompt_cache(agent_id)
    except Exception:
        pass

    logger.info("Blueprint %s -> Agent %s installiert", bp_id, agent_id)
    return {"installed": bp_id, "agent": agent_id}


# ── #314: Promotion Scratchpad → Blueprint ────────────────────────────────────

def promote_scratchpad_to_blueprint(
    agent_id: str,
    bp_id: str,
    description_override: str = "",
) -> dict:
    """Promoted den aktuellen Scratchpad-Inhalt eines Agents in einen Blueprint."""
    import hashlib

    scratchpad_path = AGENTS_DIR / agent_id / "scratchpad.md"
    if not scratchpad_path.exists():
        return {"error": f"Kein Scratchpad für Agent '{agent_id}' gefunden"}

    scratchpad_content = scratchpad_path.read_text(encoding="utf-8")

    existing = get_blueprint(bp_id)
    nodes = list(existing.nodes) if existing else []
    edges = list(existing.edges) if existing else []

    SP_HASH = hashlib.sha256(scratchpad_content.encode()).hexdigest()[:12]
    nodes = [n for n in nodes if n.type != "scratchpad"]
    nodes.append(BlueprintNode(
        type="scratchpad",
        label=f"scratchpad-{SP_HASH}",
        config={"content": scratchpad_content, "agent_id": agent_id},
    ))

    description = description_override or (
        f"Promoted von Scratchpad-Agent {agent_id} am {datetime.now(timezone.utc).date().isoformat()}"
    )

    bp = BlueprintManifest(
        id=bp_id,
        version="1.0",
        description=description,
        nodes=nodes,
        edges=edges,
        promoted_from={"agent_id": agent_id, "scratchpad_hash": SP_HASH},
    )
    save_blueprint(bp)
    return {
        "promoted": bp_id,
        "agent": agent_id,
        "node_count": len(nodes),
        "scratchpad_hash": SP_HASH,
        "size_chars": len(scratchpad_content),
    }


def preview_promotion(agent_id: str) -> dict:
    """Gibt eine Vorschau zurück: aktueller Scratchpad-Inhalt + Blueprint-Vorschau."""
    import hashlib

    scratchpad_path = AGENTS_DIR / agent_id / "scratchpad.md"
    if not scratchpad_path.exists():
        return {"error": f"Kein Scratchpad für Agent '{agent_id}' gefunden"}

    scratchpad_content = scratchpad_path.read_text(encoding="utf-8")
    SP_HASH = hashlib.sha256(scratchpad_content.encode()).hexdigest()[:12]
    preview = scratchpad_content[:200] + ("..." if len(scratchpad_content) > 200 else "")
    return {
        "agent_id": agent_id,
        "scratchpad_content": scratchpad_content,
        "scratchpad_hash": SP_HASH,
        "size_chars": len(scratchpad_content),
        "preview_node": {
            "type": "scratchpad",
            "label": f"scratchpad-{SP_HASH}",
            "config": {"content": preview, "agent_id": agent_id},
        },
    }
