"""skill_package_rule.py — Skill Package Datenmodell + JSON-Persistenz"""
from __future__ import annotations

import uuid
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

SKILL_PACKAGES_DIR = Path("/etc/hydrahive/skill_packages")


class SkillPackage(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    description: str = ""
    enabled: bool = True
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    edges: list[dict[str, Any]] = Field(default_factory=list)


def load_packages() -> list[SkillPackage]:
    SKILL_PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
    packages: list[SkillPackage] = []
    for f in sorted(SKILL_PACKAGES_DIR.glob("*.json")):
        try:
            packages.append(SkillPackage.model_validate_json(f.read_text()))
        except Exception:
            pass
    return packages


def get_package(pkg_id: str) -> SkillPackage | None:
    p = SKILL_PACKAGES_DIR / f"{pkg_id}.json"
    if not p.exists():
        return None
    try:
        return SkillPackage.model_validate_json(p.read_text())
    except Exception:
        return None


def save_package(pkg: SkillPackage) -> None:
    SKILL_PACKAGES_DIR.mkdir(parents=True, exist_ok=True)
    (SKILL_PACKAGES_DIR / f"{pkg.id}.json").write_text(
        pkg.model_dump_json(indent=2), encoding="utf-8"
    )


def delete_package(pkg_id: str) -> bool:
    p = SKILL_PACKAGES_DIR / f"{pkg_id}.json"
    if p.exists():
        p.unlink()
        return True
    return False
