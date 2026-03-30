"""
skill_loader.py — QMD Skill-Loading (#8, QM1-QM4, #41, #44)

Liest .md-Dateien aus /agents/<name>/skills/ mit YAML-Frontmatter.
scope: always  → immer in den System-Prompt geladen
scope: on-demand → Keyword-Match ODER semantischer Score ≥ threshold
priority: Ladereihenfolge bei mehreren Matches (niedrigere Zahl = höher)
max_tokens: Skill-Inhalt auf diese Anzahl Zeichen kürzen (0 = kein Limit)
token_budget: Gesamt-Zeichenbudget für alle Skills im System-Prompt
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
    max_tokens:    int = 0       # max Zeichen für Content-Kürzung (0 = kein Limit)
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

    def truncated_content(self) -> str:
        """Content auf max_tokens Zeichen kürzen (0 = kein Limit)."""
        if self.max_tokens and len(self.content) > self.max_tokens:
            return self.content[: self.max_tokens].rstrip() + "\n…"
        return self.content

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
        max_tokens=int(meta.get("max_tokens", 0)),
        content=body.strip(),
        source=path,
        allowed_tools=meta.get("allowed_tools", []) or [],
        blocked_tools=meta.get("blocked_tools", []) or [],
    )


def select_skills(
    skills: list[Skill],
    user_text: str,
    semantic_scores: dict[str, float] | None = None,
    threshold: float = 0.35,
) -> list[Skill]:
    """
    Gibt relevante Skills zurück:
    - scope=always: immer
    - scope=on-demand: Keyword-Match ODER semantischer Score ≥ threshold (#44)

    semantic_scores: {skill.skill → score 0.0–1.0} aus score_texts()
    threshold: Mindest-Score für semantische Inklusion (Default 0.35)
    """
    result = []
    for s in skills:
        if s.scope == "always":
            result.append(s)
            continue
        if s.matches(user_text):
            result.append(s)
            continue
        if semantic_scores and semantic_scores.get(s.skill, 0.0) >= threshold:
            result.append(s)
    return result


def skills_to_system_prompt(skills: list[Skill], token_budget: int = 0) -> str:
    """
    Skills als System-Prompt-Block zusammenfassen.

    token_budget: maximale Zeichenanzahl gesamt (0 = kein Limit).
                  Skills mit niedrigerer Priority werden zuerst weggelassen.
    Respektiert skill.max_tokens für individuelle Kürzung.
    """
    if not skills:
        return ""

    parts = []
    used  = 0
    for skill in skills:
        body  = skill.truncated_content()
        block = f"## Skill: {skill.skill}\n\n{body}"
        if token_budget and used + len(block) > token_budget:
            logger.debug(
                "skill_loader: '%s' weggelassen (Token-Budget %d erschöpft)",
                skill.skill, token_budget,
            )
            continue
        parts.append(block)
        used += len(block)

    return "\n\n---\n\n".join(parts)
