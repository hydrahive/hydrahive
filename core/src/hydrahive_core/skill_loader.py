"""
skill_loader.py — QMD Skill-Loading (#8, QM1-QM4)

Liest .md-Dateien aus /agents/<name>/skills/ mit YAML-Frontmatter.
scope: always  → immer in den System-Prompt geladen
scope: on-demand → nur wenn ein Keyword aus triggers im User-Text vorkommt
priority: Ladereihenfolge bei mehreren Matches (niedrigere Zahl = höher)
"""

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

FRONTMATTER_RE = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.DOTALL)


@dataclass
class Skill:
    skill:         str           # Skill-Name / ID
    version:       str = "1.0"
    scope:         str = "on-demand"   # always | on-demand
    triggers:      list[str] = field(default_factory=list)
    priority:      int = 50
    content:       str = ""      # Markdown-Body (ohne Frontmatter)
    source:        Path | None = None
    allowed_tools: list[str] = field(default_factory=list)  # Allowlist (leer = keine Einschränkung)
    blocked_tools: list[str] = field(default_factory=list)  # Blocklist (leer = keine Einschränkung)

    def matches(self, text: str) -> bool:
        """True wenn scope=always oder ein Trigger-Keyword im Text vorkommt."""
        if self.scope == "always":
            return True
        lower = text.lower()
        return any(kw.lower() in lower for kw in self.triggers)

    def apply_tool_constraints(self, tool_ids: list[str]) -> list[str]:
        """
        Wendet allowed_tools / blocked_tools auf eine Tool-ID-Liste an.
        - allowed_tools nicht leer → nur diese Tools erlaubt (Schnittmenge)
        - blocked_tools nicht leer → diese Tools entfernen
        - blocked_tools gewinnt bei Konflikt
        """
        result = tool_ids
        if self.allowed_tools:
            result = [t for t in result if t in self.allowed_tools]
        if self.blocked_tools:
            result = [t for t in result if t not in self.blocked_tools]
        return result


def load_skills(agent_dir: Path) -> list[Skill]:
    """
    Alle Skills eines Agenten laden.
    Fehlerhafte Dateien werden geloggt und übersprungen.
    """
    skills_dir = agent_dir / "skills"
    if not skills_dir.exists():
        return []

    skills = []
    for path in sorted(skills_dir.glob("*.md")):
        skill = _parse_skill_file(path)
        if skill:
            skills.append(skill)

    # Nach Priority sortieren (niedrig = zuerst)
    skills.sort(key=lambda s: s.priority)
    logger.debug("%d Skills geladen aus %s", len(skills), agent_dir)
    return skills


def _parse_skill_file(path: Path) -> Skill | None:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Skill-Datei nicht lesbar (%s): %s", path, e)
        return None

    m = FRONTMATTER_RE.match(text)
    if not m:
        logger.warning("Kein YAML-Frontmatter in %s — übersprungen", path)
        return None

    try:
        meta = yaml.safe_load(m.group(1))
    except yaml.YAMLError as e:
        logger.warning("YAML-Fehler in %s: %s", path, e)
        return None

    if not isinstance(meta, dict) or "skill" not in meta:
        logger.warning("Pflichtfeld 'skill' fehlt in %s", path)
        return None

    body = text[m.end():]
    return Skill(
        skill=meta["skill"],
        version=str(meta.get("version", "1.0")),
        scope=meta.get("scope", "on-demand"),
        triggers=meta.get("triggers", []) or [],
        priority=int(meta.get("priority", 50)),
        content=body.strip(),
        source=path,
        allowed_tools=meta.get("allowed_tools", []) or [],
        blocked_tools=meta.get("blocked_tools", []) or [],
    )


def select_skills(skills: list[Skill], user_text: str) -> list[Skill]:
    """
    Gibt relevante Skills zurück:
    - scope=always: immer
    - scope=on-demand: nur wenn Trigger matcht (QM3: Keyword-Matching, kein ML)
    """
    return [s for s in skills if s.matches(user_text)]


def skills_to_system_prompt(skills: list[Skill]) -> str:
    """Skills als lesbaren System-Prompt-Block zusammenfassen."""
    if not skills:
        return ""
    parts = []
    for skill in skills:
        parts.append(f"## Skill: {skill.skill}\n\n{skill.content}")
    return "\n\n---\n\n".join(parts)
