"""Profile-Composer Routen (#645 Phase 1b + 1c + 1d).

Phase 1b/1c: `/me/agent/composer/*` — Personal-Agent.
Phase 1d: `/admin/agents/{agent_id}/composer/*` — Admin-Agent.

agent_profile.yaml ist Truth-File, Presets + Konfliktregeln identisch.
Projekt-Boss-Composer bleibt bewusst out of scope (Phase 1e).
"""
from __future__ import annotations

import re
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


_AGENT_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")


def _validate_admin_agent_id(agent_id: str, agents_root: Path) -> Path:
    """Validiert agent_id für Admin-Composer und liefert absoluten agent_dir.

    - Syntax-Check blockt Path-Traversal (`..`, `/`, Backslash, Null).
    - `personal_*` wird explizit mit 403 abgewiesen (Admin soll diese nicht
      über den Admin-Composer editieren, wir erhalten die Rollentrennung).
    - Fehlendes Verzeichnis → 404.
    """
    if not agent_id or not _AGENT_ID_RE.match(agent_id):
        raise HTTPException(status_code=400, detail="Ungültige agent_id.")
    if agent_id.startswith("personal_"):
        raise HTTPException(
            status_code=403,
            detail="Personal-Agenten werden über /me/agent/composer gepflegt, nicht über den Admin-Composer.",
        )
    agent_dir = (agents_root / agent_id).resolve()
    try:
        agent_dir.relative_to(agents_root.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Pfad außerhalb agents_dir.")
    if not agent_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Agent nicht gefunden: {agent_id}")
    return agent_dir


def _build_profile_response(agent_dir: Path) -> dict:
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


def _validate_save_input(body: "ComposerInput") -> list[dict]:
    """Prüft body, wirft HTTPException bei harten Fehlern, liefert Warnings."""
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
    return warnings


def _perform_save(
    agent_dir: Path,
    agent_id: str,
    body: "ComposerInput",
    *,
    invalidate_prompt_cache: Callable[[str], None],
    logger,
    audit_log,
    audit_action: str,
    audit_user: str,
) -> dict:
    """Schreibt AGENT.md + agent_profile.yaml, legt Backup an, invalidiert Cache.

    Voraussetzung: _validate_save_input wurde bereits aufgerufen. Leere
    Selection wird hier mit 400 abgelehnt.
    """
    warnings = evaluate_warnings(body.selected, body.preset)  # konsistent zur Preview
    markdown = render_agent_md(body.selected)
    if not markdown.strip():
        raise HTTPException(
            status_code=400,
            detail="Mindestens einen Baustein auswählen, bevor AGENT.md geschrieben wird.",
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
        audit_action,
        user=audit_user,
        target=agent_id,
        details={
            "block_count": len(body.selected),
            "backup": backup_created,
            "preset": body.preset,
        },
    )
    logger.info(
        "Composer AGENT.md geschrieben: agent=%s blocks=%d preset=%s backup=%s (%s)",
        agent_id, len(body.selected), body.preset, backup_created, audit_action,
    )
    return {
        "updated": True,
        "agent_id": agent_id,
        "backup_created": backup_created,
        "bytes_written": len(markdown.encode("utf-8")),
        "preset": body.preset,
        "warnings": warnings,
    }


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
        return _build_profile_response(agent_dir)

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
        _validate_save_input(body)

        agent_id, _cfg = ensure_personal_agent(username)
        agent_dir = Path(agents_dir) / agent_id
        if not agent_dir.exists():
            raise HTTPException(
                status_code=404,
                detail=f"Personal-Agent-Verzeichnis nicht gefunden: {agent_id}",
            )

        return _perform_save(
            agent_dir,
            agent_id,
            body,
            invalidate_prompt_cache=invalidate_prompt_cache,
            logger=logger,
            audit_log=audit_log,
            audit_action="personal_agent.composer_save",
            audit_user=username,
        )


# ===========================================================================
# Phase 1d — Admin-Agent-Composer
# ===========================================================================


def register_admin_composer_routes(
    admin_router: APIRouter,
    *,
    agents_dir: str,
    invalidate_prompt_cache: Callable[[str], None],
    logger,
    audit_log,
    require_admin,
) -> None:
    """Registriert `/admin/agents/{agent_id}/composer/*` Routen.

    admin_router hat bereits `require_admin` als Dependency. Wir hängen es
    zusätzlich hier an, damit ein auth-Tupel für das Audit-Log verfügbar ist.
    `personal_*` wird mit 403 abgelehnt, Path-Traversal mit 400.
    """
    agents_root = Path(agents_dir)

    @admin_router.get("/admin/agents/{agent_id}/composer/blocks")
    def admin_get_blocks(
        agent_id: str,
        auth: tuple[str, str] = Depends(require_admin),
    ):
        _validate_admin_agent_id(agent_id, agents_root)
        return {"categories": list_blocks()}

    @admin_router.get("/admin/agents/{agent_id}/composer/presets")
    def admin_get_presets(
        agent_id: str,
        auth: tuple[str, str] = Depends(require_admin),
    ):
        _validate_admin_agent_id(agent_id, agents_root)
        return {"presets": list_presets()}

    @admin_router.get("/admin/agents/{agent_id}/composer/profile")
    def admin_get_profile(
        agent_id: str,
        auth: tuple[str, str] = Depends(require_admin),
    ):
        agent_dir = _validate_admin_agent_id(agent_id, agents_root)
        return _build_profile_response(agent_dir)

    @admin_router.post("/admin/agents/{agent_id}/composer/preview")
    def admin_preview(
        agent_id: str,
        body: ComposerInput = Body(...),
        auth: tuple[str, str] = Depends(require_admin),
    ):
        _validate_admin_agent_id(agent_id, agents_root)
        markdown = render_agent_md(body.selected)
        warnings = evaluate_warnings(body.selected, body.preset)
        return {
            "markdown": markdown,
            "warnings": warnings,
            "save_blocked": save_blocked(warnings),
        }

    @admin_router.put("/admin/agents/{agent_id}/composer")
    def admin_save(
        agent_id: str,
        body: ComposerInput = Body(...),
        auth: tuple[str, str] = Depends(require_admin),
    ):
        username, _role = auth
        agent_dir = _validate_admin_agent_id(agent_id, agents_root)
        _validate_save_input(body)
        return _perform_save(
            agent_dir,
            agent_id,
            body,
            invalidate_prompt_cache=invalidate_prompt_cache,
            logger=logger,
            audit_log=audit_log,
            audit_action="admin.agent.composer_save",
            audit_user=username,
        )
