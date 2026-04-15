"""Personal-Agent Profile-Composer Routen (#645 Phase 1b + 1c).

Deckt ausschließlich `/me/agent/composer/*` ab — Admin-Composer folgt in
einem späteren PR.

Phase 1c: agent_profile.yaml als Truth-File, Presets, Konfliktregeln.
"""
from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import yaml as _yaml
from fastapi import APIRouter, Body, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError

from .composer_engine import (
    CURRENT_SCHEMA_VERSION,
    AgentProfile,
    evaluate_warnings,
    known_block_ids,
    known_preset_ids,
    list_blocks,
    list_presets,
    render_agent_md,
    save_blocked,
)


PROFILE_FILENAME = "agent_profile.yaml"
AGENT_MD_FILENAME = "AGENT.md"
AGENT_MD_BACKUP = "AGENT.md.backup"
_MTIME_TOLERANCE_SECONDS = 2.0


class ComposerInput(BaseModel):
    selected: list[str] = Field(default_factory=list)
    preset: Optional[str] = None


def _load_profile(agent_dir: Path) -> tuple[AgentProfile, list[dict]]:
    """Liest agent_profile.yaml robust. Bei Fehler: leeres Profil + Warning.

    Nie 404/500 — der Composer soll bedienbar bleiben.
    """
    warnings: list[dict] = []
    path = agent_dir / PROFILE_FILENAME
    if not path.exists():
        return AgentProfile(), warnings
    try:
        raw = _yaml.safe_load(path.read_text(encoding="utf-8"))
    except Exception as e:
        warnings.append({
            "rule": "profile_yaml_corrupt",
            "severity": "warning",
            "message": f"agent_profile.yaml konnte nicht gelesen werden: {e}. Leeres Profil geladen.",
            "block_ids": [],
        })
        return AgentProfile(), warnings
    if not isinstance(raw, dict):
        warnings.append({
            "rule": "profile_yaml_corrupt",
            "severity": "warning",
            "message": "agent_profile.yaml enthält kein Mapping. Leeres Profil geladen.",
            "block_ids": [],
        })
        return AgentProfile(), warnings
    try:
        profile = AgentProfile(**raw)
    except ValidationError as e:
        warnings.append({
            "rule": "profile_yaml_corrupt",
            "severity": "warning",
            "message": f"agent_profile.yaml Schema-Validierung fehlgeschlagen: {e.errors()[0].get('msg', str(e))}. Leeres Profil geladen.",
            "block_ids": [],
        })
        return AgentProfile(), warnings

    if profile.schema_version > CURRENT_SCHEMA_VERSION:
        warnings.append({
            "rule": "profile_schema_newer",
            "severity": "warning",
            "message": (
                f"agent_profile.yaml hat schema_version={profile.schema_version}, "
                f"unterstützt wird {CURRENT_SCHEMA_VERSION}. Leeres Profil geladen."
            ),
            "block_ids": [],
        })
        return AgentProfile(), warnings

    # Unbekannte Block-IDs stumm filtern — können aus alter Katalog-Version stammen
    known = known_block_ids()
    unknown = [sid for sid in profile.selected if sid not in known]
    if unknown:
        profile = profile.model_copy(update={
            "selected": [sid for sid in profile.selected if sid in known],
        })
        warnings.append({
            "rule": "profile_unknown_blocks_dropped",
            "severity": "info",
            "message": f"Unbekannte Block-IDs wurden ignoriert: {unknown}",
            "block_ids": unknown,
        })

    # Unbekanntes Preset → als Custom behandeln (preset=None), warnen
    if profile.preset is not None and profile.preset not in known_preset_ids():
        warnings.append({
            "rule": "profile_unknown_preset",
            "severity": "info",
            "message": f"Unbekanntes Preset '{profile.preset}' — als Custom behandelt.",
            "block_ids": [],
        })
        profile = profile.model_copy(update={"preset": None})

    return profile, warnings


def _agent_md_mtime_matches(agent_dir: Path) -> tuple[bool, bool]:
    """Returns (agent_md_exists, mtime_matches)."""
    agent_md = agent_dir / AGENT_MD_FILENAME
    profile = agent_dir / PROFILE_FILENAME
    if not agent_md.exists():
        return False, True  # kein AGENT.md → kein Drift möglich
    if not profile.exists():
        # AGENT.md ohne Profile — wir können Drift nicht bestimmen, aber
        # es gibt auch keinen „letzten Composer-Save" gegen den zu vergleichen
        # wäre. Behandeln als „matches=True" — der Hinweis wäre irreführend.
        return True, True
    try:
        md_mtime = agent_md.stat().st_mtime
        profile_mtime = profile.stat().st_mtime
        return True, (md_mtime - profile_mtime) <= _MTIME_TOLERANCE_SECONDS
    except OSError:
        return True, True


def _write_profile(agent_dir: Path, profile: AgentProfile) -> None:
    data = profile.model_dump()
    (agent_dir / PROFILE_FILENAME).write_text(
        _yaml.safe_dump(data, allow_unicode=True, sort_keys=False, default_flow_style=False),
        encoding="utf-8",
    )


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

    @auth_router.get("/me/agent/composer/presets")
    def get_composer_presets(auth: tuple[str, str] = Depends(require_auth)):
        return {"presets": list_presets()}

    @auth_router.get("/me/agent/composer/profile")
    def get_composer_profile(auth: tuple[str, str] = Depends(require_auth)):
        username, _role = auth
        agent_id, _cfg = ensure_personal_agent(username)
        agent_dir = Path(agents_dir) / agent_id
        profile, load_warnings = _load_profile(agent_dir)
        agent_md_exists, mtime_matches = _agent_md_mtime_matches(agent_dir)
        warnings = load_warnings + evaluate_warnings(profile.selected, profile.preset)
        return {
            "schema_version": profile.schema_version,
            "preset": profile.preset,
            "selected": profile.selected,
            "updated_at": profile.updated_at,
            "agent_md_exists": agent_md_exists,
            "agent_md_mtime_matches": mtime_matches,
            "warnings": warnings,
        }

    @auth_router.post("/me/agent/composer/preview")
    def preview_composer(
        body: ComposerInput = Body(...),
        auth: tuple[str, str] = Depends(require_auth),
    ):
        markdown = render_agent_md(body.selected)
        warnings = evaluate_warnings(body.selected, body.preset)
        return {
            "markdown": markdown,
            "warnings": warnings,
            "save_blocked": save_blocked(warnings),
        }

    @auth_router.put("/me/agent/composer")
    def save_composer(
        body: ComposerInput = Body(...),
        auth: tuple[str, str] = Depends(require_auth),
    ):
        username, _role = auth

        unknown_blocks = [sid for sid in body.selected if sid not in known_block_ids()]
        if unknown_blocks:
            raise HTTPException(
                status_code=400,
                detail=f"Unbekannte Composer-Blöcke: {unknown_blocks}",
            )
        if body.preset is not None and body.preset not in known_preset_ids():
            raise HTTPException(
                status_code=400,
                detail=f"Unbekanntes Preset: {body.preset}",
            )

        warnings = evaluate_warnings(body.selected, body.preset)
        if save_blocked(warnings):
            raise HTTPException(
                status_code=422,
                detail={"message": "Save blockiert durch Konflikte.", "warnings": warnings},
            )

        markdown = render_agent_md(body.selected)
        if not markdown.strip():
            raise HTTPException(
                status_code=400,
                detail="Mindestens einen Baustein auswählen, bevor AGENT.md geschrieben wird.",
            )

        agent_id, _cfg = ensure_personal_agent(username)
        agent_dir = Path(agents_dir) / agent_id
        if not agent_dir.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Personal-Agent-Verzeichnis nicht gefunden: {agent_id}",
            )

        agent_md = agent_dir / AGENT_MD_FILENAME
        backup_created = False
        if agent_md.exists():
            shutil.copy2(agent_md, agent_dir / AGENT_MD_BACKUP)
            backup_created = True

        agent_md.write_text(markdown, encoding="utf-8")

        profile = AgentProfile(
            schema_version=CURRENT_SCHEMA_VERSION,
            preset=body.preset,
            selected=list(body.selected),
            updated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        )
        _write_profile(agent_dir, profile)

        try:
            invalidate_prompt_cache(agent_id)
        except Exception as e:
            logger.warning("Composer: Prompt-Cache-Invalidierung fehlgeschlagen: %s", e)

        audit_log(
            "personal_agent.composer_save",
            user=username,
            target=agent_id,
            details={
                "block_count": len(body.selected),
                "backup": backup_created,
                "preset": body.preset,
            },
        )
        logger.info(
            "Composer AGENT.md geschrieben: agent=%s blocks=%d preset=%s backup=%s",
            agent_id, len(body.selected), body.preset, backup_created,
        )
        return {
            "updated": True,
            "agent_id": agent_id,
            "backup_created": backup_created,
            "bytes_written": len(markdown.encode("utf-8")),
            "preset": body.preset,
            "warnings": warnings,
        }
