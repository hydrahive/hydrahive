from __future__ import annotations

import re
from pathlib import Path
from typing import Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel


# #658: Skill-Namen-Policy V1 — konservativ. Kleinbuchstaben, Ziffern, `_`, `-`.
# Muss mit alnum starten. Keine Punkte, keine `..`, keine Slashes. Max 64 Zeichen.
SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# Maximale Dateigröße beim Install aus dem Catalog (256 KiB).
MAX_SKILL_BYTES = 256 * 1024


def _sanitize_skill_name(name: str) -> str:
    """Härtet Filename-Sanitization für Skill-Routen. Ersetzt schwachen
    `replace("..","")` durch strikte Regex-Prüfung."""
    if not isinstance(name, str):
        raise HTTPException(400, f"Skill-Name muss string sein: {name!r}")
    n = name.strip()
    if n.endswith(".md"):
        n = n[:-3]
    if ".." in n or "/" in n or "\\" in n:
        raise HTTPException(400, f"Ungültiger Skill-Name: {name!r}")
    if not SKILL_NAME_RE.match(n):
        raise HTTPException(400, f"Ungültiger Skill-Name: {name!r}")
    return n


def _safe_skill_target(skills_dir: Path, name: str) -> Path:
    """Resolve Zielpfad + Traversal-Schutz via `.resolve().relative_to()`."""
    safe = _sanitize_skill_name(name)
    candidate = (skills_dir / f"{safe}.md").resolve()
    try:
        candidate.relative_to(skills_dir.resolve())
    except ValueError:
        raise HTTPException(400, "Pfad-Traversal abgelehnt")
    return candidate


class SkillRequest(BaseModel):
    filename: str
    skill: str
    version: str = "1.0"
    scope: str = "on-demand"
    triggers: list[str] = []
    priority: int = 50
    content: str = ""


class SkillInstallRequest(BaseModel):
    """Body für `POST /agents/{id}/skills/install` (#658).

    V1: source ist immer `"catalog"` — installiert aus lokalem curated
    Verzeichnis. Remote-URLs/Git sind bewusst ausgeschlossen.
    """
    source: str = "catalog"
    name:   str
    force:  bool = False


def _skills_dir(agents_dir: str, agent_id: str) -> Path:
    return Path(agents_dir) / agent_id / "skills"


def _parse_skill_file(path: Path) -> dict:
    import re as _re
    import yaml as _yaml

    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {}

    m = _re.match(r"^---\s*\n(.*?)\n---\s*\n", text, _re.DOTALL)
    if not m:
        return {
            "filename": path.stem,
            "skill": path.stem,
            "version": "1.0",
            "scope": "on-demand",
            "triggers": [],
            "priority": 50,
            "content": text.strip(),
        }

    try:
        meta = _yaml.safe_load(m.group(1)) or {}
    except Exception:
        meta = {}

    result: dict = {
        "filename": path.stem,
        "skill": meta.get("skill", path.stem),
        "version": str(meta.get("version", "1.0")),
        "scope": meta.get("scope", "on-demand"),
        "triggers": meta.get("triggers", []) or [],
        "priority": int(meta.get("priority", 50)),
        "content": text[m.end():].strip(),
    }
    if "author" in meta:
        result["author"] = str(meta["author"])
    return result


def _write_skill_file(skills_dir: Path, filename: str, data: dict) -> Path:
    import yaml as _yaml

    skills_dir.mkdir(parents=True, exist_ok=True)
    path = _safe_skill_target(skills_dir, filename)
    safe = path.stem

    frontmatter = {
        "skill": data.get("skill", safe),
        "version": data.get("version", "1.0"),
        "scope": data.get("scope", "on-demand"),
        "priority": int(data.get("priority", 50)),
    }
    triggers = data.get("triggers", [])
    if triggers:
        frontmatter["triggers"] = triggers

    yaml_str = _yaml.dump(frontmatter, allow_unicode=True, default_flow_style=False)
    content = data.get("content", "").strip()
    path.write_text(f"---\n{yaml_str}---\n\n{content}\n", encoding="utf-8")
    return path


def register_agent_skill_routes(
    auth_router: APIRouter,
    *,
    require_auth,
    check_agent_write,
    agents_dir: str,
    logger,
    catalog_dir_provider: Callable[[], Path] | None = None,
) -> None:
    @auth_router.get("/agents/{agent_id}/skills")
    def list_agent_skills(
        agent_id: str,
        layers: str = Query("installed", pattern="^(installed|effective|all)$"),
        _a: tuple[str, str] = Depends(require_auth),
    ):
        """
        #659: Multi-Layer-View.

        - `layers=installed` (Default, backwärtskompatibel zu #658):
          nur agent-lokale Skills, altes Response-Shape.
        - `layers=effective`: pro Stem der effektive Skill. `skills[]`
          enthält nur agent/project/user (prompt-wirksam); System/Catalog
          wird separat in `available[]` ausgewiesen und ist NICHT
          prompt-wirksam.
        - `layers=all`: wie `effective` plus `shadows[]` in `skills[]`
          und `errors[]` auf Response-Ebene; `content` eingebettet pro
          Skill für `/skill run`.
        """
        agent_dir = Path(agents_dir) / agent_id
        if not agent_dir.exists():
            raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")

        if layers == "installed":
            skills_dir = _skills_dir(agents_dir, agent_id)
            if not skills_dir.exists():
                return {"agent_id": agent_id, "skills": []}

            skills = []
            for path in sorted(skills_dir.glob("*.md")):
                skill = _parse_skill_file(path)
                if skill:
                    skills.append(skill)

            skills.sort(key=lambda s: s.get("priority", 50))
            return {"agent_id": agent_id, "skills": skills}

        # effective | all — Resolver-basierte Ansicht.
        #
        # Wichtig: `effective` bedeutet „in einem installierten Layer
        # (agent/project/user) vorhanden". System-/Catalog-Skills tauchen
        # ausdrücklich NICHT in `skills[]` auf — sie werden in `available[]`
        # separat ausgewiesen. So kann `/skill run` strikt gegen `skills[]`
        # matchen, ohne dass ein uninstalled Catalog-Skill aktivierbar wird.
        from .skill_resolver import (
            origin_to_dict,
            resolve_full_view,
            resolved_to_dict,
        )
        from .settings import settings as _settings

        project_dir: Path | None = None
        if agent_dir.parent.resolve() == _settings.projects_dir.resolve():
            project_dir = agent_dir

        # #668: User-Layer aus Auth-Username. `internal` (HMAC-signed
        # sub-calls) oder ungültige Namen → Layer übersprungen, NIE 500.
        _req_username, _ = _a
        _user_skills_dir: Path | None = None
        if _req_username and _req_username != "internal":
            try:
                _user_skills_dir = _settings.user_skills_dir(_req_username)
            except ValueError:
                logger.warning(
                    "skills-listing: ungültiger request_user %r — User-Layer übersprungen",
                    _req_username,
                )
                _user_skills_dir = None

        catalog_dir = (Path(catalog_dir_provider()) if catalog_dir_provider
                       else None)
        resolved, errors = resolve_full_view(
            agent_dir=_skills_dir(agents_dir, agent_id),
            project_dir=(project_dir / "skills") if project_dir else None,
            user_skills_dir=_user_skills_dir,
            system_catalog_dir=catalog_dir,
        )

        effective: list[dict] = []
        available: list[dict] = []
        include_content = (layers == "all")
        for r in resolved:
            if r.effective.source == "system":
                # System-only (nicht in agent/project/user vorhanden) →
                # als installierbare Quelle ausweisen, nie als effective.
                entry = origin_to_dict(r.effective)
                entry["name"] = r.name
                if include_content and r.effective.skill is not None:
                    entry["content"] = r.effective.skill.content
                available.append(entry)
                continue
            item = resolved_to_dict(r, include_content=include_content)
            if layers == "effective":
                item.pop("shadows", None)
                item.pop("shadowed_by", None)
            effective.append(item)

        response: dict = {
            "agent_id":  agent_id,
            "skills":    effective,
            "available": available,
        }
        if layers == "all":
            response["errors"] = errors
        return response

    @auth_router.post("/agents/{agent_id}/skills", status_code=201)
    def create_agent_skill(agent_id: str, req: SkillRequest, auth: tuple = Depends(require_auth)):
        check_agent_write(agent_id, auth)
        agent_dir = Path(agents_dir) / agent_id
        if not agent_dir.exists():
            raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")

        skills_dir = _skills_dir(agents_dir, agent_id)
        target = _safe_skill_target(skills_dir, req.filename)
        if target.exists():
            raise HTTPException(409, f"Skill '{req.filename}' existiert bereits")

        path = _write_skill_file(skills_dir, req.filename, req.model_dump())
        logger.info("Skill angelegt: %s/%s", agent_id, path.name)
        return {"created": True, "agent_id": agent_id, "filename": path.stem}

    @auth_router.put("/agents/{agent_id}/skills/{filename}")
    def update_agent_skill(agent_id: str, filename: str, req: SkillRequest, auth: tuple = Depends(require_auth)):
        check_agent_write(agent_id, auth)
        agent_dir = Path(agents_dir) / agent_id
        if not agent_dir.exists():
            raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")

        path = _write_skill_file(_skills_dir(agents_dir, agent_id), filename, req.model_dump())
        logger.info("Skill aktualisiert: %s/%s", agent_id, path.name)
        return {"updated": True, "agent_id": agent_id, "filename": path.stem}

    @auth_router.delete("/agents/{agent_id}/skills/{filename}")
    def delete_agent_skill(agent_id: str, filename: str, auth: tuple = Depends(require_auth)):
        check_agent_write(agent_id, auth)
        path = _safe_skill_target(_skills_dir(agents_dir, agent_id), filename)
        if not path.exists():
            raise HTTPException(404, f"Skill '{filename}' nicht gefunden")
        path.unlink()
        logger.info("Skill geloescht: %s/%s", agent_id, path.stem)
        return {"deleted": True, "agent_id": agent_id, "filename": path.stem}

    # #658: Install aus curated Catalog (nur lokal, keine Remote-URLs).
    @auth_router.post("/agents/{agent_id}/skills/install", status_code=201)
    def install_agent_skill(
        agent_id: str,
        req: SkillInstallRequest,
        auth: tuple = Depends(require_auth),
    ):
        check_agent_write(agent_id, auth)
        if req.source != "catalog":
            raise HTTPException(
                400,
                f"Quelle '{req.source}' nicht unterstützt (V1: nur 'catalog')",
            )
        if catalog_dir_provider is None:
            raise HTTPException(500, "Catalog-Quelle nicht konfiguriert")

        agent_dir = Path(agents_dir) / agent_id
        if not agent_dir.exists():
            raise HTTPException(404, f"Agent '{agent_id}' nicht gefunden")

        from .router_skills_catalog import safe_catalog_path  # lazy

        catalog = Path(catalog_dir_provider())
        src = safe_catalog_path(catalog, req.name)
        if not src.exists() or not src.is_file():
            raise HTTPException(404, f"Skill '{req.name}' nicht im Catalog")

        size = src.stat().st_size
        if size > MAX_SKILL_BYTES:
            raise HTTPException(
                413,
                f"Skill '{req.name}' zu groß ({size} bytes, max {MAX_SKILL_BYTES})",
            )

        from .router_skills_catalog import _parse_strict  # lazy
        parsed = _parse_strict(src)
        if parsed is None:
            raise HTTPException(
                422,
                f"Catalog-Skill '{req.name}' hat kein gültiges Frontmatter",
            )

        skills_dir = _skills_dir(agents_dir, agent_id)
        skills_dir.mkdir(parents=True, exist_ok=True)
        target = _safe_skill_target(skills_dir, req.name)
        if target.exists() and not req.force:
            raise HTTPException(
                409,
                f"Skill '{req.name}' existiert bereits (force=true zum Überschreiben)",
            )

        payload = src.read_text(encoding="utf-8")
        target.write_text(payload, encoding="utf-8")
        logger.info(
            "Skill installiert aus catalog: %s → %s/%s (%d bytes)",
            req.name, agent_id, target.name, size,
        )
        return {
            "installed": True,
            "agent_id":  agent_id,
            "filename":  target.stem,
            "source":    "catalog",
            "bytes":     size,
            "overwrote": req.force and target.exists(),
        }
