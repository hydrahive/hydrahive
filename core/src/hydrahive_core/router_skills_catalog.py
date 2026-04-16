"""
router_skills_catalog.py — Read-only Skill-Catalog für `/skill install` (#658).

V1:
- Curated lokale Quelle unter `settings.skills_catalog_dir` (default
  `/opt/hydrahive/skills/catalog/`). Admin-befüllt.
- Keine Remote-URLs, kein Git-Clone, kein Auto-Import.
- Fehlende oder leere Catalog-Dir → leere Liste, kein Fehler.
- Parse-Fehler pro Datei werden isoliert und als `errors[]` gemeldet; eine
  kaputte Datei bricht das Listing nicht ab.
- Detail-Get liefert 422 bei kaputter Datei.

Sicherheit:
- Skill-Name-Regex `^[a-z0-9][a-z0-9_.-]{0,63}$` — keine `..`, keine Slashes.
- Pfad-Auflösung via `.resolve().relative_to(catalog_dir)` blockt Traversal.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Callable

import yaml
from fastapi import APIRouter, Depends, HTTPException

logger = logging.getLogger(__name__)

# V1 konservativ: kleinbuchstaben, Ziffern, `_`, `-`. Muss mit alnum starten.
# Kein Punkt — wird in V2 erwogen falls semver oder Dateinamen-Suffix nötig.
CATALOG_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def _is_valid_name(name: str) -> bool:
    if not name or not isinstance(name, str):
        return False
    if ".." in name or "/" in name or "\\" in name or "." in name:
        return False
    return bool(CATALOG_NAME_RE.match(name))


def safe_catalog_path(catalog_dir: Path, name: str) -> Path:
    """Resolve `<catalog_dir>/<name>.md` mit Traversal-Schutz."""
    if not _is_valid_name(name):
        raise HTTPException(400, f"Ungültiger Skill-Name: {name!r}")
    candidate = (catalog_dir / f"{name}.md").resolve()
    try:
        candidate.relative_to(catalog_dir.resolve())
    except ValueError:
        raise HTTPException(400, "Pfad-Traversal abgelehnt")
    return candidate


_FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


def _parse_strict(path: Path) -> dict | None:
    """
    Strict-Parser für Catalog-Dateien: erwartet YAML-Frontmatter MIT
    `skill`-Pflichtfeld. Liefert None bei fehlendem/defektem Frontmatter
    oder fehlendem `skill`-Key — im Gegensatz zu
    `router_agent_skills._parse_skill_file`, das fallback-toleranter ist.
    """
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    m = _FRONTMATTER_RE.match(text)
    if not m:
        return None
    try:
        meta = yaml.safe_load(m.group(1))
    except yaml.YAMLError:
        return None
    if not isinstance(meta, dict) or "skill" not in meta:
        return None
    body = text[m.end():].strip()
    return {
        "filename": path.stem,
        "skill":    str(meta["skill"]),
        "version":  str(meta.get("version", "1.0")),
        "scope":    meta.get("scope", "on-demand"),
        "triggers": meta.get("triggers", []) or [],
        "priority": int(meta.get("priority", 50)),
        "content":  body,
    }


def _summarize(parsed: dict, filename: str) -> dict:
    """Kurzer Listen-Eintrag (keine Volltext-Payload)."""
    content = parsed.get("content", "") or ""
    first_line = content.splitlines()[0].strip() if content else ""
    description = first_line[:200] if first_line else ""
    return {
        "name":        filename,
        "skill":       parsed.get("skill", filename),
        "version":     str(parsed.get("version", "1.0")),
        "scope":       parsed.get("scope", "on-demand"),
        "description": description,
    }


def list_catalog(catalog_dir: Path) -> tuple[list[dict], list[dict]]:
    """
    Returns (items, errors).
    - Dir fehlt / leer: ([], []).
    - Pro Datei einzeln parsen; Fehler landen in errors, brechen nichts ab.
    """
    if not catalog_dir.exists() or not catalog_dir.is_dir():
        return ([], [])

    items: list[dict] = []
    errors: list[dict] = []
    for p in sorted(catalog_dir.glob("*.md")):
        name = p.stem
        if not _is_valid_name(name):
            errors.append({"name": name, "error": "invalid_name"})
            continue
        try:
            parsed = _parse_strict(p)
        except Exception as exc:  # pragma: no cover — defensiv
            errors.append({"name": name, "error": f"parse_exception: {exc}"[:160]})
            continue
        if parsed is None:
            errors.append({"name": name, "error": "invalid_frontmatter"})
            continue
        items.append(_summarize(parsed, name))
    return (items, errors)


def get_catalog_entry(catalog_dir: Path, name: str) -> dict:
    """Detail-Ansicht. 404 wenn fehlt, 422 wenn kaputt."""
    target = safe_catalog_path(catalog_dir, name)
    if not target.exists() or not target.is_file():
        raise HTTPException(404, f"Skill '{name}' nicht im Catalog")
    try:
        parsed = _parse_strict(target)
    except Exception as exc:
        raise HTTPException(422, f"Skill-Datei nicht parsebar: {exc}"[:200])
    if parsed is None:
        raise HTTPException(422, f"Skill-Datei '{name}' hat kein gültiges Frontmatter")
    return {
        "name":     name,
        "skill":    parsed.get("skill"),
        "version":  str(parsed.get("version", "1.0")),
        "scope":    parsed.get("scope", "on-demand"),
        "triggers": parsed.get("triggers", []) or [],
        "priority": int(parsed.get("priority", 50)),
        "content":  parsed.get("content", ""),
    }


def register_skills_catalog_routes(
    auth_router: APIRouter,
    *,
    require_auth,
    catalog_dir_provider: Callable[[], Path],
    logger,
) -> None:
    @auth_router.get("/skills/catalog")
    def _list_skills_catalog(_a: tuple = Depends(require_auth)):
        catalog = Path(catalog_dir_provider())
        items, errors = list_catalog(catalog)
        return {
            "catalog_dir": str(catalog),
            "skills":      items,
            "errors":      errors,
        }

    @auth_router.get("/skills/catalog/{name}")
    def _get_skills_catalog_entry(name: str, _a: tuple = Depends(require_auth)):
        catalog = Path(catalog_dir_provider())
        return get_catalog_entry(catalog, name)
