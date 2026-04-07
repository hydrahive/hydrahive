"""router_pipelines.py — Datei-Pipeline CRUD (#60)

Visuelle Datei-Verarbeitungs-Pipelines: Ordner beobachten, filtern,
verschieben, umbenennen, Agent beauftragen, benachrichtigen.

Endpoints (Admin):
  GET    /pipelines              → alle Pipelines
  POST   /pipelines              → neue Pipeline
  PUT    /pipelines/{id}         → Pipeline überschreiben
  PATCH  /pipelines/{id}/toggle  → aktivieren / deaktivieren
  DELETE /pipelines/{id}         → Pipeline löschen
  POST   /pipelines/{id}/run     → Pipeline manuell mit Dateipfad testen
"""
from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from .settings import settings

logger = logging.getLogger(__name__)

PIPELINES_DIR = settings.pipelines_dir


# ── Persistenz ────────────────────────────────────────────────────────────────

def _ensure_dir() -> None:
    PIPELINES_DIR.mkdir(parents=True, exist_ok=True)


def load_all_pipelines() -> list[dict]:
    _ensure_dir()
    result = []
    for f in sorted(PIPELINES_DIR.glob("*.json")):
        try:
            result.append(json.loads(f.read_text(encoding="utf-8")))
        except Exception:
            pass
    return result


def load_pipeline(pipeline_id: str) -> dict | None:
    _ensure_dir()
    p = PIPELINES_DIR / f"{pipeline_id}.json"
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def save_pipeline(data: dict) -> None:
    _ensure_dir()
    p = PIPELINES_DIR / f"{data['id']}.json"
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def delete_pipeline(pipeline_id: str) -> bool:
    p = PIPELINES_DIR / f"{pipeline_id}.json"
    if p.exists():
        p.unlink()
        return True
    return False


# ── Schemas ───────────────────────────────────────────────────────────────────

class PipelineSaveRequest(BaseModel):
    name: str
    enabled: bool = True
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


class PipelineRunRequest(BaseModel):
    file_path: str  # absoluter Pfad zur Testdatei


# ── Router-Registrierung ──────────────────────────────────────────────────────

def register_pipeline_routes(
    admin_router: APIRouter,
    *,
    require_admin,
    notify_fn=None,   # optional: Notification-Service callback
) -> None:

    @admin_router.get("/pipelines")
    def list_pipelines(_a: tuple = Depends(require_admin)):
        return load_all_pipelines()

    @admin_router.post("/pipelines", status_code=201)
    def create_pipeline(req: PipelineSaveRequest, _a: tuple = Depends(require_admin)):
        pipeline_id = str(uuid.uuid4())
        data = {
            "id": pipeline_id,
            "name": req.name,
            "enabled": req.enabled,
            "nodes": req.nodes,
            "edges": req.edges,
        }
        save_pipeline(data)
        logger.info("Pipeline '%s' erstellt: %s", req.name, pipeline_id)
        return data

    @admin_router.put("/pipelines/{pipeline_id}")
    def update_pipeline(
        pipeline_id: str,
        req: PipelineSaveRequest,
        _a: tuple = Depends(require_admin),
    ):
        if load_pipeline(pipeline_id) is None:
            raise HTTPException(404, "Pipeline nicht gefunden")
        data = {
            "id": pipeline_id,
            "name": req.name,
            "enabled": req.enabled,
            "nodes": req.nodes,
            "edges": req.edges,
        }
        save_pipeline(data)
        return data

    @admin_router.patch("/pipelines/{pipeline_id}/toggle")
    def toggle_pipeline(pipeline_id: str, _a: tuple = Depends(require_admin)):
        data = load_pipeline(pipeline_id)
        if data is None:
            raise HTTPException(404, "Pipeline nicht gefunden")
        data["enabled"] = not data.get("enabled", True)
        save_pipeline(data)
        return {"id": pipeline_id, "enabled": data["enabled"]}

    @admin_router.delete("/pipelines/{pipeline_id}")
    def del_pipeline(pipeline_id: str, _a: tuple = Depends(require_admin)):
        if not delete_pipeline(pipeline_id):
            raise HTTPException(404, "Pipeline nicht gefunden")
        logger.info("Pipeline gelöscht: %s", pipeline_id)
        return {"deleted": True}

    @admin_router.post("/pipelines/{pipeline_id}/run")
    async def run_pipeline_manual(
        pipeline_id: str,
        req: PipelineRunRequest,
        _a: tuple = Depends(require_admin),
    ):
        """Pipeline manuell mit einem Dateipfad testen."""
        data = load_pipeline(pipeline_id)
        if data is None:
            raise HTTPException(404, "Pipeline nicht gefunden")
        from .pipeline_executor import execute_pipeline
        result = await execute_pipeline(data, req.file_path, notify_fn=notify_fn)
        return {"file": req.file_path, "steps": result}
