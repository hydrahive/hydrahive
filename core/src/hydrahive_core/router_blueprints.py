"""
router_blueprints.py — Admin-API für Blueprints (#312)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .blueprint_service import (
    BlueprintManifest,
    delete_blueprint,
    get_blueprint,
    install_to_agent,
    list_blueprints,
    promote_scratchpad_to_blueprint,
    preview_promotion,
    save_blueprint,
)

# ── Schema ───────────────────────────────────────────────────────────────────

class BlueprintImportRequest(BaseModel):
    id:          str
    version:    str = "1.0"
    description: str = ""
    nodes:       list = []
    edges:       list = []


def register_blueprint_routes(
    router: APIRouter,
    *,
    require_admin,
) -> None:
    """Registriert Blueprint-Routen auf dem gegebenen Router."""

    # ── GET /admin/blueprints ─────────────────────────────────────────────────
    @router.get("/admin/blueprints", response_model=list)
    def list_blueprints_api(_a: tuple = Depends(require_admin)) -> list:
        return list_blueprints()

    # ── GET /admin/blueprints/export/{id} ────────────────────────────────────
    @router.get("/admin/blueprints/export/{bp_id}")
    def export_blueprint(bp_id: str, _a: tuple = Depends(require_admin)) -> dict:
        bp = get_blueprint(bp_id)
        if not bp:
            raise HTTPException(404, f"Blueprint '{bp_id}' nicht gefunden")
        return bp.to_dict()

    # ── POST /admin/blueprints/import ───────────────────────────────────────
    @router.post("/admin/blueprints/import")
    def import_blueprint(req: BlueprintImportRequest, _a: tuple = Depends(require_admin)) -> dict:
        bp = BlueprintManifest(
            id=req.id,
            version=req.version,
            description=req.description,
            nodes=req.nodes,
            edges=req.edges,
        )
        save_blueprint(bp)
        return {"imported": req.id}

    # ── GET /admin/blueprints/{id} ───────────────────────────────────────────
    @router.get("/admin/blueprints/{bp_id}")
    def get_blueprint_api(bp_id: str, _a: tuple = Depends(require_admin)) -> dict:
        bp = get_blueprint(bp_id)
        if not bp:
            raise HTTPException(404, f"Blueprint '{bp_id}' nicht gefunden")
        return bp.to_dict()

    # ── PUT /admin/blueprints/{id} ───────────────────────────────────────────
    @router.put("/admin/blueprints/{bp_id}")
    def update_blueprint(bp_id: str, req: BlueprintImportRequest, _a: tuple = Depends(require_admin)) -> dict:
        existing = get_blueprint(bp_id)
        bp = BlueprintManifest(
            id=bp_id,
            version=req.version or (existing.version if existing else "1.0"),
            description=req.description,
            nodes=req.nodes,
            edges=req.edges,
        )
        save_blueprint(bp)
        return {"saved": bp_id}

    # ── DELETE /admin/blueprints/{id} ───────────────────────────────────────
    @router.delete("/admin/blueprints/{bp_id}")
    def delete_blueprint_api(bp_id: str, _a: tuple = Depends(require_admin)) -> dict:
        deleted = delete_blueprint(bp_id)
        if not deleted:
            raise HTTPException(404, f"Blueprint '{bp_id}' nicht gefunden")
        return {"deleted": bp_id}

    # ── POST /admin/blueprints/{id}/install/{agent_id} ─────────────────────
    @router.post("/admin/blueprints/{bp_id}/install/{agent_id}")
    def install_blueprint(bp_id: str, agent_id: str, _a: tuple = Depends(require_admin)) -> dict:
        result = install_to_agent(bp_id, agent_id)
        if "error" in result:
            raise HTTPException(404, result["error"])
        return result

    # ── Schema für Promotion ─────────────────────────────────────────────────

    class PromoteScratchpadRequest(BaseModel):
        blueprint_id: str
        description_override: str = ""

    # ── #314: Promotion Scratchpad → Blueprint ──────────────────────────────

    @router.post("/admin/blueprints/promote-scratchpad/{agent_id}")
    def promote_scratchpad(
        agent_id: str,
        req: PromoteScratchpadRequest,
        _a: tuple = Depends(require_admin),
    ) -> dict:
        result = promote_scratchpad_to_blueprint(
            agent_id=agent_id,
            bp_id=req.blueprint_id,
            description_override=req.description_override,
        )
        if "error" in result:
            raise HTTPException(400, result["error"])
        return result

    @router.get("/admin/blueprints/promote-scratchpad/{agent_id}/preview")
    def preview_scratchpad_promotion(
        agent_id: str,
        _a: tuple = Depends(require_admin),
    ) -> dict:
        result = preview_promotion(agent_id)
        if "error" in result:
            raise HTTPException(404, result["error"])
        return result
