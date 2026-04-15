"""Personal-Agent Profile-Composer Routen (#645 Phase 1b).

Deckt ausschließlich `/me/agent/composer/*` ab — Admin-Composer folgt in
Phase 1c in einem separaten PR.
"""
from __future__ import annotations

import shutil
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field

from .composer_engine import known_block_ids, list_blocks, render_agent_md


class ComposerSelection(BaseModel):
    selected: list[str] = Field(default_factory=list)


def register_composer_routes(
    auth_router: APIRouter,
    *,
    require_auth,
    agents_dir: str,
    ensure_personal_agent,
    invalidate_prompt_cache: Callable[[str], None],
    logger,
    audit_log,
) -> None:
    @auth_router.get("/me/agent/composer/blocks")
    def get_composer_blocks(auth: tuple[str, str] = Depends(require_auth)):
        return {"categories": list_blocks()}

    @auth_router.post("/me/agent/composer/preview")
    def preview_composer(
        body: ComposerSelection = Body(...),
        auth: tuple[str, str] = Depends(require_auth),
    ):
        markdown = render_agent_md(body.selected)
        return {"markdown": markdown}

    @auth_router.put("/me/agent/composer")
    def save_composer(
        body: ComposerSelection = Body(...),
        auth: tuple[str, str] = Depends(require_auth),
    ):
        username, _role = auth
        known = known_block_ids()
        unknown = [sid for sid in body.selected if sid not in known]
        if unknown:
            raise HTTPException(
                status_code=400,
                detail=f"Unbekannte Composer-Blöcke: {unknown}",
            )

        agent_id, _cfg = ensure_personal_agent(username)
        agent_dir = Path(agents_dir) / agent_id
        if not agent_dir.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Personal-Agent-Verzeichnis nicht gefunden: {agent_id}",
            )

        markdown = render_agent_md(body.selected)
        if not markdown.strip():
            raise HTTPException(
                status_code=400,
                detail="Mindestens einen Baustein auswählen, bevor AGENT.md geschrieben wird.",
            )

        agent_md = agent_dir / "AGENT.md"
        backup_created = False
        if agent_md.exists():
            shutil.copy2(agent_md, agent_dir / "AGENT.md.backup")
            backup_created = True

        agent_md.write_text(markdown, encoding="utf-8")

        try:
            invalidate_prompt_cache(agent_id)
        except Exception as e:
            logger.warning("Composer: Prompt-Cache-Invalidierung fehlgeschlagen: %s", e)

        audit_log(
            "personal_agent.composer_save",
            user=username,
            target=agent_id,
            details={"block_count": len(body.selected), "backup": backup_created},
        )
        logger.info(
            "Composer AGENT.md geschrieben: agent=%s blocks=%d backup=%s",
            agent_id, len(body.selected), backup_created,
        )
        return {
            "updated": True,
            "agent_id": agent_id,
            "backup_created": backup_created,
            "bytes_written": len(markdown.encode("utf-8")),
        }
