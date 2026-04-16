"""skill_resolver.py — Multi-Layer Skill-Resolver (#659).

Lädt Skills aus mehreren Verzeichnis-Ebenen und erzeugt pro Filename-Stem
genau einen `effective` Skill plus optionale Shadowed-Einträge.

Layer-Prioritäten V1:
  1. agent   — /agents/<id>/skills/
  2. project — /projects/<id>/skills/
  3. user    — /var/lib/hydrahive/users/<username>/skills/
  4. system  — /opt/hydrahive/skills/catalog/          (nur Quelle, NICHT
                automatisch prompt-aktiv; siehe `resolve_prompt_skills`)

Design-Entscheidungen (siehe Plan-Review):

- **Shadowing-Key = Dateiname-Stem.** Frontmatter-`skill` wird nicht für
  Shadowing genutzt; Duplikat-Erkennung würde sonst unpraktikabel.
- **Kein Feld-Merging.** Effective-Skill ersetzt niedrigere Layer komplett
  (Content, Triggers, Tool-Constraints, Priority).
- **Fallback bei Parse-Fehlern.** Ist die höchste Datei kaputt, rückt der
  nächste Layer nach; Fehler landet in `errors[]`.
- **Keine Verzeichnis-Erzeugung.** Der Resolver legt nirgends `mkdir` an.
- **Dedup.** Wenn `agent_dir == project_dir` (z.B. v2-Projekt-Agent via
  `agent_config_from_project`) wird der Projekt-Layer übersprungen. Keine
  Self-Shadowing-Einträge.
- **System ≠ Prompt-Layer.** `resolve_prompt_skills()` nutzt bewusst nur
  agent/project/user. `resolve_full_view()` nimmt system zusätzlich rein,
  aber nur als _listbare Quelle_ — für `/skill list`.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .skill_loader import Skill, _parse_skill_file

logger = logging.getLogger(__name__)


SkillSource = Literal["agent", "project", "user", "system"]

# Gleiche Policy wie Skill-/Catalog-Namen (#658), angewendet auf Benutzernamen.
# Muss mit alnum starten. Kein Punkt/Slash/.., Länge ≤ 64.
USERNAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def validate_username(username: str) -> str:
    if not isinstance(username, str):
        raise ValueError(f"Username muss string sein: {username!r}")
    u = username.strip()
    if not USERNAME_RE.match(u) or ".." in u or "/" in u or "\\" in u:
        raise ValueError(f"Ungültiger Username: {username!r}")
    return u


@dataclass(frozen=True)
class SkillOrigin:
    """Ein konkretes Skill-File auf Platte, einem Layer zugeordnet."""
    source:    SkillSource
    path:      Path
    parsed_ok: bool
    skill:     Skill | None = None   # None wenn parsed_ok=False
    error:     str | None = None


@dataclass(frozen=True)
class ResolvedSkill:
    """Effective Skill pro Stem + Audit-Trail der überdeckten Kandidaten."""
    name:      str
    effective: SkillOrigin
    shadowed:  tuple[SkillOrigin, ...] = field(default_factory=tuple)


_LAYER_ORDER: tuple[SkillSource, ...] = ("agent", "project", "user", "system")


def _scan_dir(layer: SkillSource, directory: Path | None) -> list[SkillOrigin]:
    """Alle *.md in directory als SkillOrigin. Liefert [] wenn dir None/fehlt."""
    if directory is None:
        return []
    if not directory.exists() or not directory.is_dir():
        return []

    origins: list[SkillOrigin] = []
    for path in sorted(directory.glob("*.md")):
        try:
            skill = _parse_skill_file(path)
        except Exception as exc:  # pragma: no cover — defensiv
            origins.append(SkillOrigin(
                source=layer, path=path,
                parsed_ok=False, error=f"parse_exception: {exc}"[:160],
            ))
            continue
        if skill is None:
            origins.append(SkillOrigin(
                source=layer, path=path,
                parsed_ok=False, error="invalid_frontmatter_or_missing_skill_field",
            ))
            continue
        origins.append(SkillOrigin(
            source=layer, path=path, parsed_ok=True, skill=skill,
        ))
    return origins


def _dedup_project(agent_dir: Path | None, project_dir: Path | None) -> Path | None:
    """Projekt-Layer kollabieren, wenn er identisch zum agent-Layer ist."""
    if agent_dir is None or project_dir is None:
        return project_dir
    try:
        if agent_dir.resolve() == project_dir.resolve():
            return None
    except OSError:
        # Pfad nicht auflösbar → konservativ trotzdem dedupen, wenn gleich
        if str(agent_dir) == str(project_dir):
            return None
    return project_dir


def _resolve(
    *,
    agent_dir: Path | None,
    project_dir: Path | None,
    user_skills_dir: Path | None,
    system_catalog_dir: Path | None,
    include_system: bool,
) -> tuple[list[ResolvedSkill], list[dict]]:
    """Gemeinsame Implementierung. `include_system` steuert, ob der
    System-Layer als Kandidat für Effective-Wahl gilt."""
    project_dir = _dedup_project(agent_dir, project_dir)

    layers: dict[SkillSource, Path | None] = {
        "agent":   agent_dir,
        "project": project_dir,
        "user":    user_skills_dir,
        "system":  system_catalog_dir if include_system else None,
    }

    # Pro Stem Origins sammeln, in Layer-Prio-Reihenfolge.
    by_stem: dict[str, list[SkillOrigin]] = {}
    errors:  list[dict] = []
    for layer in _LAYER_ORDER:
        directory = layers[layer]
        for origin in _scan_dir(layer, directory):
            stem = origin.path.stem
            by_stem.setdefault(stem, []).append(origin)
            if not origin.parsed_ok:
                errors.append({
                    "name":   stem,
                    "source": layer,
                    "path":   str(origin.path),
                    "error":  origin.error or "unknown",
                })

    resolved: list[ResolvedSkill] = []
    for stem, origins in by_stem.items():
        # origins sind bereits in Prio-Reihenfolge (agent→system).
        effective: SkillOrigin | None = None
        fallthrough: list[SkillOrigin] = []
        for o in origins:
            if effective is None and o.parsed_ok:
                effective = o
            else:
                fallthrough.append(o)
        if effective is None:
            # Kein parsebarer Skill in irgendeinem Layer — nur errors, kein Eintrag.
            continue
        resolved.append(ResolvedSkill(
            name=stem, effective=effective, shadowed=tuple(fallthrough),
        ))

    # Stabile Sortierung für deterministische Tests & UI.
    resolved.sort(key=lambda r: r.name)
    return (resolved, errors)


def resolve_prompt_skills(
    *,
    agent_dir: Path | None,
    project_dir: Path | None = None,
    user_skills_dir: Path | None = None,
) -> tuple[list[ResolvedSkill], list[dict]]:
    """Für System-Prompt-Injection.

    System/Catalog-Skills werden **bewusst nicht** berücksichtigt — Admin
    soll kontrollieren, was ohne Installation prompt-wirksam ist.
    """
    return _resolve(
        agent_dir=agent_dir,
        project_dir=project_dir,
        user_skills_dir=user_skills_dir,
        system_catalog_dir=None,
        include_system=False,
    )


def resolve_full_view(
    *,
    agent_dir: Path | None,
    project_dir: Path | None = None,
    user_skills_dir: Path | None = None,
    system_catalog_dir: Path | None = None,
) -> tuple[list[ResolvedSkill], list[dict]]:
    """Für `/skill list` (layers=all/effective).

    Nimmt den System-Layer als zusätzliche Quelle — aber weiterhin mit
    agent > project > user > system Priorität. Ein system-only Skill wird
    als effective geliefert; der Aufrufer entscheidet, wie er ihn rendert.
    """
    return _resolve(
        agent_dir=agent_dir,
        project_dir=project_dir,
        user_skills_dir=user_skills_dir,
        system_catalog_dir=system_catalog_dir,
        include_system=True,
    )


def origin_to_dict(o: SkillOrigin) -> dict:
    """Serialisierungs-Helfer für API-Responses. Kein Skill-Content — nur
    Metadaten. Content muss der Aufrufer selbst beifügen, wenn gewünscht
    (z.B. für /skill run-Injection)."""
    out: dict = {
        "source":    o.source,
        "path":      str(o.path),
        "parsed_ok": o.parsed_ok,
    }
    if o.skill is not None:
        out["skill"]    = o.skill.skill
        out["version"]  = o.skill.version
        out["scope"]    = o.skill.scope
        out["priority"] = o.skill.priority
        out["triggers"] = list(o.skill.triggers)
    if o.error is not None:
        out["error"] = o.error
    return out


def resolved_to_dict(r: ResolvedSkill, *, include_content: bool = False) -> dict:
    """Serialisierungs-Helfer für API-Responses."""
    out: dict = {
        "name":        r.name,
        "effective":   origin_to_dict(r.effective),
        "shadowed_by": [],  # der Effective wird nie von anderen beschattet
        "shadows":     [origin_to_dict(s) for s in r.shadowed],
    }
    if include_content and r.effective.skill is not None:
        out["content"] = r.effective.skill.content
    return out
