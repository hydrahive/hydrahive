"""
router_teams.py — Admin-API für Agent-Teams (#789)
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from .agent_teams import get_team, list_teams, save_team


# ── Schema ───────────────────────────────────────────────────────────────────

class TeamData(BaseModel):
    name: str
    members: list[dict] = []


def register_team_routes(
    router: APIRouter,
    *,
    require_admin,
) -> None:
    """Registriert Team-Routen auf dem gegebenen Router."""

    # ── GET /admin/teams ─────────────────────────────────────────────────────
    @router.get("/admin/teams", response_model=list)
    def list_teams_api(_a: tuple = Depends(require_admin)) -> list:
        return list_teams()

    # ── GET /admin/teams/{team_id} ──────────────────────────────────────────
    @router.get("/admin/teams/{team_id}")
    def get_team_api(team_id: str, _a: tuple = Depends(require_admin)) -> dict:
        team = get_team(team_id)
        if not team:
            raise HTTPException(404, f"Team '{team_id}' nicht gefunden")
        return {
            "name": team.name,
            "members": [{"agent_id": m.agent_id, "role": m.role} for m in team.members.values()],
        }

    # ── PUT /admin/teams/{team_id} ──────────────────────────────────────────
    @router.put("/admin/teams/{team_id}")
    def save_team_api(team_id: str, req: TeamData, _a: tuple = Depends(require_admin)) -> dict:
        from .agent_teams import AgentTeam
        team = AgentTeam(name=req.name)
        for m in req.members:
            team.add_member(m["agent_id"], role=m.get("role", "worker"))
        save_team(team_id, team)
        return {"saved": team_id}

    # ── DELETE /admin/teams/{team_id} ──────────────────────────────────────
    @router.delete("/admin/teams/{team_id}")
    def delete_team_api(team_id: str, _a: tuple = Depends(require_admin)) -> dict:
        import shutil
        from .agent_teams import TEAMS_DIR
        path = TEAMS_DIR / f"{team_id}.yaml"
        if not path.exists():
            raise HTTPException(404, f"Team '{team_id}' nicht gefunden")
        shutil.rmtree(path)
        return {"deleted": team_id}
