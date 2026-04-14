"""
context_channels.py — Strukturierte System-Prompt-Schichten (Issue #627)

Statt Memory, Identity, Skills, Tools etc. flach in eine `parts: list[str]` zu
kippen, hält ContextChannels jeden Block in einem benannten Slot. Vorteile:

- **Cache-Stabilität:** statische Slots bleiben byte-identisch zwischen Turns,
  dynamische werden klar markiert. Ist die Voraussetzung für #629 (mehrere
  cache_control-Breakpoints zwischen den Channels).
- **Debugging:** Channel-Sizes pro Turn loggbar, Memory-Treffer im Prompt
  identifizierbar (Marker `<memory_dynamic>...</memory_dynamic>`).
- **Saubere Trennung:** statische vs. dynamische Anteile werden beim Bauen
  zugeordnet, nicht erst beim Joinen.

Migration: Alte Stellen, die Strings in `parts` schoben, setzen jetzt den
passenden Channel-Slot. `to_static_str()` + `to_dynamic_str()` liefern die
zwei Strings, die der Stream-Builder bisher zurückgab — backward-kompatibel.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields


# Marker für den dynamischen Memory-Block (klar identifizierbar im Prompt,
# spätere Cache-Logik kann hier den Breakpoint setzen).
MEMORY_OPEN = "<memory_dynamic>"
MEMORY_CLOSE = "</memory_dynamic>"


@dataclass
class ContextChannels:
    """Strukturierter System-Prompt — pro Slot ein logischer Block.

    Static-Slots (cacheable, query-unabhängig):
    """
    # ── Static (cacheable) ──────────────────────────────────────────
    agent_identity: str = ""       # "Du bist X." / Datum (variiert minutenweise; vgl. Hinweis unten)
    onboarding: str = ""           # startup.md (Onboarding-Phase)
    soul: str = ""                 # AGENT.md / soul.md
    memory_index: str = ""         # INDEX.md (Vault-Pattern, slim)
    learning: str = ""             # learned-memory snippet
    sources: str = ""              # zugewiesene externe Quellen
    repos: str = ""                # zugewiesene Git-Repos
    servers: str = ""              # zugewiesene Remote-Server (#636 Migration)
    handbook: str = ""             # globales System-Handbook
    blueprint: str = ""            # workflow_blueprint.json Kontext
    workflow: str = ""             # Agent-spezifischer Workflow
    policies: str = ""             # statische Verhaltens-/Memory-Regeln

    # ── Dynamic (query-abhängig, nicht cacheable) ───────────────────
    memory_hits: str = ""          # BM25-Treffer für aktuelle Query
    amem_hits: str = ""             # A-MEM globale Treffer
    working_state: str = ""         # WorkingState-Snapshot (#632) — Anomalien zuerst
    last_session: str = ""          # _last_session.md (Session-Continuity)
    skills: str = ""                # query-spezifisch ausgewählte Skills
    repo_guidance: str = ""         # repo-review-guidance bei Repo-Querys
    deferred_tools: str = ""        # ToolSearch-Hinweis (#620)
    plan_mode: str = ""             # Plan-Mode-Injection
    frustration: str = ""           # Frustration-Hint (#485)

    # ── Helpers ─────────────────────────────────────────────────────

    _STATIC_SLOTS = (
        "agent_identity", "onboarding", "soul", "memory_index", "learning",
        "sources", "repos", "servers", "handbook", "blueprint", "workflow", "policies",
    )
    _DYNAMIC_SLOTS = (
        "memory_hits", "amem_hits", "working_state", "last_session", "skills",
        "repo_guidance", "deferred_tools", "plan_mode", "frustration",
    )

    def to_static_str(self) -> str:
        return _join([getattr(self, s) for s in self._STATIC_SLOTS])

    def to_dynamic_str(self) -> str:
        """Liefert alle dynamischen Channels als String, gewrappt in Markern.

        Der `<memory_dynamic>`-Marker wird IMMER gesetzt sobald ein
        dynamischer Channel non-empty ist — damit #629 (Cache-Segmentierung)
        zuverlässig den Cut zwischen cacheable Vorlauf und volatilem Schwanz
        finden kann. Reihenfolge entspricht `_DYNAMIC_SLOTS` (memory zuerst,
        working_state zweitens — Anomalien sollen früh sichtbar sein).
        """
        parts: list[str] = []
        for s in self._DYNAMIC_SLOTS:
            val = getattr(self, s)
            if val:
                parts.append(val)
        if not parts:
            return ""
        body = _join(parts)
        return f"{MEMORY_OPEN}\n{body}\n{MEMORY_CLOSE}"

    def to_full_str(self) -> str:
        s = self.to_static_str()
        d = self.to_dynamic_str()
        return (s + "\n\n" + d).strip() if d else s

    def sizes(self) -> dict[str, int]:
        """Zeichen-Längen pro Channel (für Logging/Diagnose)."""
        return {f.name: len(getattr(self, f.name) or "") for f in fields(self)
                if not f.name.startswith("_")}


def _join(parts: list[str]) -> str:
    return "\n\n".join(p for p in parts if p)
