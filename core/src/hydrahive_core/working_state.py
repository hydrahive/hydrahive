"""
working_state.py — Working-Memory-Schicht + Resume-Snapshots (Issue #630)

Klare Trennung der Memory-Schichten:

- **Projekt-Memory** — persistent, agentenweit (`agent_dir/memory/*.md`).
- **Session-Memory** — persistent, sessionweit (Messages, Snapshots in SQLite).
- **Working-Memory** — letzter Snapshot der laufenden Session: offene Files,
  aktive Tool-Runde, letzte Memory-Treffer, Compaction-Stand, Budget-
  Entscheidungen, aktuelles Ziel. Wird beim Resume reaktiviert und in den
  `working_state`-Channel (#627) injiziert.
- **Turn-Kontext** — flüchtig, nur in der aktuellen Runde.

Snapshots werden pro Turn-Ende geschrieben, beim Resume wird der jüngste
Snapshot geladen.

Format ist versioniert (SCHEMA_VERSION) damit zukünftige Felder rückwärts-
kompatibel ergänzt werden können.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone


SCHEMA_VERSION = 1


@dataclass
class WorkingState:
    """Snapshot des Sessions-Arbeitsgedächtnisses am Ende eines Turns."""

    schema_version: int = SCHEMA_VERSION

    # Letzte aktive Memory-Treffer (BM25/A-MEM): kompakte Liste mit Source-Pfaden
    last_memory_hits: list[str] = field(default_factory=list)

    # Files die im letzten Turn gelesen / editiert wurden
    open_files: list[str] = field(default_factory=list)

    # Aktive (noch nicht abgeschlossene) Tool-Aufrufe — z.B. langlaufende
    # Background-Tasks
    active_tools: list[dict] = field(default_factory=list)

    # Wie viel Compaction wurde im letzten Turn angewandt
    compaction_state: dict = field(default_factory=dict)

    # Budget-Entscheidungen: was wurde gekürzt, warum
    budget_decisions: list[str] = field(default_factory=list)

    # Aktuelles Ziel / Aufgabe (frei formuliertes 1-Liner)
    current_goal: str = ""

    # Wann wurde dieser Snapshot erzeugt (UTC ISO)
    created_at: str = ""

    def to_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)

    @classmethod
    def from_json(cls, raw: str) -> "WorkingState":
        try:
            data = json.loads(raw or "{}")
        except Exception:
            return cls()
        # Forward-compatible: unbekannte Felder ignorieren, Defaults für fehlende
        known = {f for f in cls.__dataclass_fields__}
        clean = {k: v for k, v in data.items() if k in known}
        return cls(**clean)

    def to_channel_text(self) -> str:
        """Format für Injektion in den `working_state`-Channel (#627).

        Bewusst kompakt — der Snapshot soll Hintergrund liefern, nicht die
        Hauptaufmerksamkeit beanspruchen.
        """
        lines = []
        if self.current_goal:
            lines.append(f"**Aktuelles Ziel:** {self.current_goal}")
        if self.open_files:
            lines.append(f"**Offene Files:** {', '.join(self.open_files[:8])}")
        if self.last_memory_hits:
            lines.append(f"**Zuletzt aktive Memories:** {', '.join(self.last_memory_hits[:8])}")
        if self.active_tools:
            tool_summary = ", ".join(t.get("name", "?") for t in self.active_tools[:5])
            lines.append(f"**Aktive Tools:** {tool_summary}")
        if self.budget_decisions:
            lines.append(f"**Letzte Budget-Entscheidungen:** {'; '.join(self.budget_decisions[:3])}")
        if self.compaction_state:
            lines.append(f"**Compaction:** {json.dumps(self.compaction_state, ensure_ascii=False)}")
        if not lines:
            return ""
        header = "## Working-Memory (Snapshot vor Resume)"
        if self.created_at:
            header += f" — {self.created_at}"
        return header + "\n\n" + "\n".join(lines)


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
