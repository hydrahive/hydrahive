"""Profile-Composer Routen (#645 Phase 1b + 1c + 1d).

Phase 1b/1c: `/me/agent/composer/*` — Personal-Agent.
Phase 1d: `/admin/agents/{agent_id}/composer/*` — Admin-Agent.

agent_profile.yaml ist Truth-File, Presets + Konfliktregeln identisch.
Projekt-Boss-Composer bleibt bewusst out of scope (Phase 1e).
"""
from __future__ import annotations

import hashlib
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import yaml as _yaml
from fastapi import APIRouter, Body, Depends, Header, HTTPException
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
_VERSIONED_BACKUP_RE = re.compile(r"^AGENT\.md\.\d{8}T\d{6}Z(-\d+)?\.backup$")
_MTIME_TOLERANCE_SECONDS = 2.0


def _versioned_backup_name(agent_dir: Path, now: datetime | None = None) -> str:
    """Liefert einen eindeutigen Basename `AGENT.md.<UTC>.backup`.

    Format: `AGENT.md.YYYYMMDDTHHMMSSZ.backup`. Bei Kollision im selben
    Sekundenfenster hängt Suffix `-2`, `-3`, ... an — Windows-safe, keine `:`.
    """
    ts = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    base = f"AGENT.md.{ts}.backup"
    if not (agent_dir / base).exists():
        return base
    i = 2
    while True:
        candidate = f"AGENT.md.{ts}-{i}.backup"
        if not (agent_dir / candidate).exists():
            return candidate
        i += 1


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


def _compute_etag(agent_dir: Path) -> str:
    """Stat-Fingerprint von AGENT.md + agent_profile.yaml → sha256[:16].

    Keine Dateien → Hash von `empty`. Fehlt eine Seite, wird `missing` als
    Platzhalter eingesetzt, damit Übergänge (kein profile → mit profile)
    sichere Key-Wechsel auslösen.
    """
    agent_md = agent_dir / AGENT_MD_FILENAME
    profile = agent_dir / PROFILE_FILENAME

    def _part(p: Path, label: str) -> str:
        if not p.exists():
            return f"{label}:missing"
        try:
            st = p.stat()
        except OSError:
            return f"{label}:missing"
        return f"{label}:{st.st_mtime_ns}:{st.st_size}"

    if not agent_md.exists() and not profile.exists():
        raw = "empty"
    else:
        raw = _part(agent_md, "agent") + "|" + _part(profile, "profile")
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


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
        "etag": _compute_etag(agent_dir),
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
    versioned_backup: str | None = None
    if agent_md.exists():
        shutil.copy2(agent_md, agent_dir / AGENT_MD_BACKUP)
        versioned_backup = _versioned_backup_name(agent_dir)
        shutil.copy2(agent_md, agent_dir / versioned_backup)
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
        "versioned_backup": versioned_backup,
        "bytes_written": len(markdown.encode("utf-8")),
        "preset": body.preset,
        "warnings": warnings,
        "etag": _compute_etag(agent_dir),
    }


# ===========================================================================
# #647 — Backup-Listing, Preview, Restore (shared helpers)
# ===========================================================================
#
# Backup-Konvention aus `_perform_save`:
#   - `AGENT.md.backup`                  → letzter aktiver Stand (latest, rolling)
#   - `AGENT.md.<YYYYMMDDTHHMMSSZ>.backup`  → versioniert (+ optional `-N`)
# Listing und Restore operieren ausschließlich auf dieser Menge.
#
# Path-Traversal-Schutz: strikte Filename-Regex + `.resolve().relative_to()`.
# Restore ist strict-ETag (`If-Match` Pflicht): 428 wenn fehlt, 409 wenn
# mismatch, 200 bei match.

_BACKUP_NAME_RE = re.compile(
    r"^AGENT\.md\.(\d{8}T\d{6}Z(-\d+)?\.backup|backup)$"
)
_BACKUP_PREVIEW_MAX_BYTES = 1 * 1024 * 1024   # 1 MiB
_BACKUP_LISTING_MAX = 500


def _safe_backup_target(agent_dir: Path, name: str) -> Path:
    """Resolve `<agent_dir>/<name>` mit strikter Regex und Traversal-Schutz.

    Nur `AGENT.md.backup` und `AGENT.md.<UTC>.backup` (optional `-N`) sind
    zulässig. Alles andere → 400.
    """
    if not isinstance(name, str) or not _BACKUP_NAME_RE.match(name):
        raise HTTPException(status_code=400, detail=f"Ungültiger Backup-Name: {name!r}")
    candidate = (agent_dir / name).resolve()
    try:
        candidate.relative_to(agent_dir.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Pfad-Traversal abgelehnt.")
    return candidate


def _list_backups(agent_dir: Path) -> tuple[list[dict], bool]:
    """Liefert (items, truncated).

    Sortierung: versioned nach mtime DESC (neueste zuerst), dann
    `AGENT.md.backup` als eigener Eintrag mit kind='latest' am Ende.
    """
    if not agent_dir.exists() or not agent_dir.is_dir():
        return [], False

    versioned: list[dict] = []
    latest: dict | None = None
    for p in agent_dir.iterdir():
        if not p.is_file():
            continue
        n = p.name
        if n == AGENT_MD_BACKUP:
            try:
                st = p.stat()
            except OSError:
                continue
            latest = {
                "name":       n,
                "kind":       "latest",
                "size_bytes": st.st_size,
                "mtime":      datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
                                    .isoformat(timespec="seconds"),
            }
            continue
        if _VERSIONED_BACKUP_RE.match(n):
            try:
                st = p.stat()
            except OSError:
                continue
            versioned.append({
                "name":       n,
                "kind":       "versioned",
                "size_bytes": st.st_size,
                "mtime":      datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
                                    .isoformat(timespec="seconds"),
                "_sort_ts":   st.st_mtime,
            })
    versioned.sort(key=lambda d: d["_sort_ts"], reverse=True)
    for d in versioned:
        d.pop("_sort_ts", None)

    truncated = False
    if len(versioned) > _BACKUP_LISTING_MAX:
        versioned = versioned[:_BACKUP_LISTING_MAX]
        truncated = True

    items: list[dict] = list(versioned)
    if latest is not None:
        items.append(latest)
    return items, truncated


def _read_backup_for_preview(target: Path) -> dict:
    """Liest Backup und wirft 404/413. Content byte-treu via utf-8."""
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Backup nicht gefunden.")
    try:
        st = target.stat()
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"Backup-Stat fehlgeschlagen: {e}")
    if st.st_size > _BACKUP_PREVIEW_MAX_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Backup größer als {_BACKUP_PREVIEW_MAX_BYTES} bytes — Preview verweigert.",
        )
    try:
        content = target.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="Backup ist nicht UTF-8 und kann nicht previewed werden.")
    return {
        "name":       target.name,
        "content":    content,
        "size_bytes": st.st_size,
        "mtime":      datetime.fromtimestamp(st.st_mtime, tz=timezone.utc)
                              .isoformat(timespec="seconds"),
    }


def _require_etag_match_strict(agent_dir: Path, if_match: Optional[str]) -> None:
    """Strict ETag-Guard für alle Composer-Writes.

    Fehlender Header → 428 Precondition Required mit `current_etag`,
    Mismatch → 409 Conflict mit `current_etag`. Beides mit
    `detail={message, current_etag}`-Shape, den das Frontend zum
    Reload-Banner verarbeitet.

    Nach #647 (Restore) und #650 (Save/Admin/Project) der einzige
    ETag-Guard — die frühere lax-Variante wurde entfernt, weil kein
    Legacy-Client ohne If-Match mehr unterstützt wird.
    """
    current = _compute_etag(agent_dir)
    if if_match is None:
        raise HTTPException(
            status_code=428,
            detail={
                "message":      "If-Match Header erforderlich. Aktuellen ETag aus GET /composer/profile laden.",
                "current_etag": current,
            },
        )
    if if_match != current:
        raise HTTPException(
            status_code=409,
            detail={
                "message":      "AGENT.md wurde seit dem Laden geändert. Bitte Profil neu laden.",
                "current_etag": current,
            },
        )


def _perform_restore(
    agent_dir: Path,
    agent_id: str,
    backup_name: str,
    if_match: Optional[str],
    *,
    invalidate_prompt_cache: Callable[[str], None],
    logger,
    audit_log,
    audit_action: str,
    audit_user: str,
) -> dict:
    """Restore-Ablauf, atomar in Bezug auf eine Request:

    1. Pfad-/Name-Validation (Regex + resolve/relative_to) → 400.
    2. Target existent? → 404.
    3. If-Match strict (428/409).
    4. Pre-Restore-Snapshot: current AGENT.md → neues `AGENT.md.<UTC>.backup`.
    5. `AGENT.md.backup` = Pre-Restore-Content (rolling latest-Semantik).
    6. `AGENT.md` = selected backup content (byte-treu).
    7. Cache invalidate + Audit.
    """
    target = _safe_backup_target(agent_dir, backup_name)
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Backup nicht gefunden.")

    agent_md = agent_dir / AGENT_MD_FILENAME
    # Strict ETag-Check: AGENT.md muss existieren und aktuellen Stand spiegeln.
    _require_etag_match_strict(agent_dir, if_match)

    pre_restore_snapshot: str | None = None
    if agent_md.exists():
        pre_restore_snapshot = _versioned_backup_name(agent_dir)
        shutil.copy2(agent_md, agent_dir / pre_restore_snapshot)
        # rolling latest: AGENT.md.backup zeigt auf den Stand VOR dem Restore.
        shutil.copy2(agent_md, agent_dir / AGENT_MD_BACKUP)

    # Restore: selected backup → AGENT.md (byte-treu).
    shutil.copy2(target, agent_md)

    try:
        invalidate_prompt_cache(agent_id)
    except Exception as e:
        logger.warning("Composer-Restore: Prompt-Cache-Invalidierung fehlgeschlagen: %s", e)

    audit_log(
        audit_action,
        user=audit_user,
        target=agent_id,
        details={
            "from_backup":          backup_name,
            "pre_restore_snapshot": pre_restore_snapshot,
        },
    )
    logger.info(
        "Composer Restore: agent=%s from=%s pre_snapshot=%s (%s)",
        agent_id, backup_name, pre_restore_snapshot, audit_action,
    )

    return {
        "restored":             True,
        "agent_id":             agent_id,
        "from_backup":          backup_name,
        "pre_restore_snapshot": pre_restore_snapshot,
        "etag":                 _compute_etag(agent_dir),
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
        if_match: Optional[str] = Header(None, alias="If-Match"),
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

        _require_etag_match_strict(agent_dir, if_match)
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

    # #647: Backup-Listing / Preview / Restore (Personal-Scope).
    @auth_router.get("/me/agent/composer/backups")
    def personal_list_backups(auth: tuple[str, str] = Depends(require_auth)):
        username, _role = auth
        agent_id, _cfg = ensure_personal_agent(username)
        agent_dir = Path(agents_dir) / agent_id
        items, truncated = _list_backups(agent_dir)
        return {"backups": items, "count": len(items), "truncated": truncated}

    @auth_router.get("/me/agent/composer/backups/{name}")
    def personal_preview_backup(
        name: str,
        auth: tuple[str, str] = Depends(require_auth),
    ):
        username, _role = auth
        agent_id, _cfg = ensure_personal_agent(username)
        agent_dir = Path(agents_dir) / agent_id
        target = _safe_backup_target(agent_dir, name)
        return _read_backup_for_preview(target)

    @auth_router.post("/me/agent/composer/backups/{name}/restore")
    def personal_restore_backup(
        name: str,
        if_match: Optional[str] = Header(None, alias="If-Match"),
        auth: tuple[str, str] = Depends(require_auth),
    ):
        username, _role = auth
        agent_id, _cfg = ensure_personal_agent(username)
        agent_dir = Path(agents_dir) / agent_id
        return _perform_restore(
            agent_dir, agent_id, name, if_match,
            invalidate_prompt_cache=invalidate_prompt_cache,
            logger=logger,
            audit_log=audit_log,
            audit_action="personal_agent.composer_restore",
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
        if_match: Optional[str] = Header(None, alias="If-Match"),
        auth: tuple[str, str] = Depends(require_admin),
    ):
        username, _role = auth
        agent_dir = _validate_admin_agent_id(agent_id, agents_root)
        _validate_save_input(body)
        _require_etag_match_strict(agent_dir, if_match)
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

    # #647: Backup-Listing / Preview / Restore (Admin-Scope).
    @admin_router.get("/admin/agents/{agent_id}/composer/backups")
    def admin_list_backups(
        agent_id: str,
        auth: tuple[str, str] = Depends(require_admin),
    ):
        agent_dir = _validate_admin_agent_id(agent_id, agents_root)
        items, truncated = _list_backups(agent_dir)
        return {"backups": items, "count": len(items), "truncated": truncated}

    @admin_router.get("/admin/agents/{agent_id}/composer/backups/{name}")
    def admin_preview_backup(
        agent_id: str,
        name: str,
        auth: tuple[str, str] = Depends(require_admin),
    ):
        agent_dir = _validate_admin_agent_id(agent_id, agents_root)
        target = _safe_backup_target(agent_dir, name)
        return _read_backup_for_preview(target)

    @admin_router.post("/admin/agents/{agent_id}/composer/backups/{name}/restore")
    def admin_restore_backup(
        agent_id: str,
        name: str,
        if_match: Optional[str] = Header(None, alias="If-Match"),
        auth: tuple[str, str] = Depends(require_admin),
    ):
        agent_dir = _validate_admin_agent_id(agent_id, agents_root)
        username, _role = auth
        return _perform_restore(
            agent_dir, agent_id, name, if_match,
            invalidate_prompt_cache=invalidate_prompt_cache,
            logger=logger,
            audit_log=audit_log,
            audit_action="admin.agent.composer_restore",
            audit_user=username,
        )


# ===========================================================================
# Phase 1e — Projekt-Boss-Composer
# ===========================================================================


_PROJECT_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_-]*$")


def _validate_project_id(project_id: str, projects_root: Path) -> Path:
    """Validiert project_id und liefert absoluten project_dir.

    - Regex-Syntax-Check blockt `/`, `..`, Backslash, leer, Null.
    - `.resolve()` + `.relative_to(projects_root.resolve())` als zweite Schicht.
    - Fehlendes Verzeichnis → 404.

    Personal-Projekte (`personal_<user>`) werden hier NICHT blockiert; der
    Rechte-Check entscheidet ob der Aufrufer schreiben darf.
    """
    if not project_id or not _PROJECT_ID_RE.match(project_id):
        raise HTTPException(status_code=400, detail="Ungültige project_id.")
    project_dir = (projects_root / project_id).resolve()
    try:
        project_dir.relative_to(projects_root.resolve())
    except ValueError:
        raise HTTPException(status_code=400, detail="Pfad außerhalb projects_dir.")
    if not project_dir.is_dir():
        raise HTTPException(status_code=404, detail=f"Projekt nicht gefunden: {project_id}")
    return project_dir


def _require_project_composer_access(auth: tuple[str, str], project_id: str) -> None:
    """Admin ODER Personal-Projekt-Owner. Alles andere → 403.

    Reguläre Project-Owner und Members sind bewusst ausgeschlossen — der
    Composer steuert Safety-/Confirm-Defaults und wird konservativ gegated.
    """
    username, role = auth
    if role == "admin":
        return
    if project_id == f"personal_{username}":
        return
    raise HTTPException(
        status_code=403,
        detail="Nur Admin oder Personal-Projekt-Owner dürfen den Projekt-Composer nutzen.",
    )


def register_project_composer_routes(
    auth_router: APIRouter,
    *,
    require_auth,
    projects_dir: str,
    invalidate_prompt_cache: Callable[[str], None],
    logger,
    audit_log,
) -> None:
    """Registriert `/projects/{project_id}/composer/*` Routen.

    Rechte: Admin oder Personal-Projekt-Owner (reguläre Project-Owner/Members
    werden mit 403 abgewiesen).
    """
    projects_root = Path(projects_dir)

    @auth_router.get("/projects/{project_id}/composer/blocks")
    def project_get_blocks(
        project_id: str,
        auth: tuple[str, str] = Depends(require_auth),
    ):
        _validate_project_id(project_id, projects_root)
        _require_project_composer_access(auth, project_id)
        return {"categories": list_blocks()}

    @auth_router.get("/projects/{project_id}/composer/presets")
    def project_get_presets(
        project_id: str,
        auth: tuple[str, str] = Depends(require_auth),
    ):
        _validate_project_id(project_id, projects_root)
        _require_project_composer_access(auth, project_id)
        return {"presets": list_presets()}

    @auth_router.get("/projects/{project_id}/composer/profile")
    def project_get_profile(
        project_id: str,
        auth: tuple[str, str] = Depends(require_auth),
    ):
        project_dir = _validate_project_id(project_id, projects_root)
        _require_project_composer_access(auth, project_id)
        return _build_profile_response(project_dir)

    @auth_router.post("/projects/{project_id}/composer/preview")
    def project_preview(
        project_id: str,
        body: ComposerInput = Body(...),
        auth: tuple[str, str] = Depends(require_auth),
    ):
        _validate_project_id(project_id, projects_root)
        _require_project_composer_access(auth, project_id)
        markdown = render_agent_md(body.selected)
        warnings = evaluate_warnings(body.selected, body.preset)
        return {
            "markdown": markdown,
            "warnings": warnings,
            "save_blocked": save_blocked(warnings),
        }

    @auth_router.put("/projects/{project_id}/composer")
    def project_save(
        project_id: str,
        body: ComposerInput = Body(...),
        if_match: Optional[str] = Header(None, alias="If-Match"),
        auth: tuple[str, str] = Depends(require_auth),
    ):
        project_dir = _validate_project_id(project_id, projects_root)
        _require_project_composer_access(auth, project_id)
        username, _role = auth
        _validate_save_input(body)
        _require_etag_match_strict(project_dir, if_match)
        return _perform_save(
            project_dir,
            project_id,
            body,
            invalidate_prompt_cache=invalidate_prompt_cache,
            logger=logger,
            audit_log=audit_log,
            audit_action="project.composer_save",
            audit_user=username,
        )

    # #647: Backup-Listing / Preview / Restore (Project-Scope).
    @auth_router.get("/projects/{project_id}/composer/backups")
    def project_list_backups(
        project_id: str,
        auth: tuple[str, str] = Depends(require_auth),
    ):
        project_dir = _validate_project_id(project_id, projects_root)
        _require_project_composer_access(auth, project_id)
        items, truncated = _list_backups(project_dir)
        return {"backups": items, "count": len(items), "truncated": truncated}

    @auth_router.get("/projects/{project_id}/composer/backups/{name}")
    def project_preview_backup(
        project_id: str,
        name: str,
        auth: tuple[str, str] = Depends(require_auth),
    ):
        project_dir = _validate_project_id(project_id, projects_root)
        _require_project_composer_access(auth, project_id)
        target = _safe_backup_target(project_dir, name)
        return _read_backup_for_preview(target)

    @auth_router.post("/projects/{project_id}/composer/backups/{name}/restore")
    def project_restore_backup(
        project_id: str,
        name: str,
        if_match: Optional[str] = Header(None, alias="If-Match"),
        auth: tuple[str, str] = Depends(require_auth),
    ):
        project_dir = _validate_project_id(project_id, projects_root)
        _require_project_composer_access(auth, project_id)
        username, _role = auth
        return _perform_restore(
            project_dir, project_id, name, if_match,
            invalidate_prompt_cache=invalidate_prompt_cache,
            logger=logger,
            audit_log=audit_log,
            audit_action="project.composer_restore",
            audit_user=username,
        )
